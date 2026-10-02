"""W5: HTTP boundaries over real sockets (uvicorn on ephemeral loopback ports).

One module-scoped server pair: a local signing-key server publishing JWKS, and
the MCP app built exactly as ``server_http`` builds production
(``HttpServingPolicy`` + ``RemoteAuthProvider`` around ``WorkspaceJWTVerifier``
+ the production middleware stack). The public base URL carries a reverse
proxy prefix (``/gw``) that the proxy strips, so discovery is exercised
through a proxy base path.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from dataclasses import dataclass, field
import json
import socket
import threading
from typing import Any
from urllib.parse import urlparse

import anyio
import httpx
import pytest
from cryptography.fernet import Fernet
from fastmcp import Client, Context, FastMCP
from fastmcp.client.transports import StreamableHttpTransport

from http_harness import LiveServers, SigningKeys, mcp_body, mcp_headers, mint, reserve_sockets

from mcp_google_workspace.auth.identity import current_principal
from mcp_google_workspace.auth.remote_auth import build_jwt_verifier, build_remote_auth
from mcp_google_workspace.common.admission import AdmissionController, AdmissionLimits
from mcp_google_workspace.common.component_annotations import apply_default_tool_annotations
from mcp_google_workspace.common.confirmation import (
    build_request_state_security,
    confirm_destructive_action,
)
from mcp_google_workspace.common.errors import StructuredToolErrorMiddleware
from mcp_google_workspace.common.production import (
    CapabilityCatalogMiddleware,
    ProductionControlMiddleware,
    production_lifespan,
)
from mcp_google_workspace.runtime import RemoteSecuritySettings
from mcp_google_workspace.server_http import HttpServingPolicy, build_http_app

ISSUER = "https://issuer.w5.test"
AUDIENCE = "w5-mcp"
STATE_KEYS = "s" * 64
MAX_BODY = 256 * 1024
TIMEOUT = 10.0


@dataclass
class Recorder:
    calls: list[str] = field(default_factory=list)
    started: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)


def _server(recorder: Recorder) -> FastMCP:
    server = FastMCP(
        "w5-live",
        lifespan=production_lifespan,
        request_state_security=build_request_state_security(
            environ={"MCP_REQUEST_STATE_KEYS": STATE_KEYS}
        ),
    )

    @server.tool
    async def probe(value: str = "x") -> dict[str, str]:
        """Record a call and echo the authenticated subject."""
        recorder.calls.append(value)
        return {"subject": current_principal().subject, "value": value}

    @server.tool
    async def slow() -> dict[str, bool]:
        """Run until cancelled."""
        recorder.started.set()
        try:
            await anyio.sleep(120)
        except anyio.get_cancelled_exc_class():
            recorder.cancelled.set()
            raise
        return {"finished": True}

    @server.tool
    async def progress(ctx: Context) -> dict[str, bool]:
        """Report two progress steps."""
        await ctx.report_progress(1, 2, "half")
        await ctx.report_progress(2, 2, "done")
        return {"done": True}

    @server.tool
    async def forget_thing(thing_id: str, ctx: Context) -> dict[str, str]:
        """Confirmed destructive action."""
        if not await confirm_destructive_action(ctx, "forget_thing", f"Forget {thing_id}?"):
            return {"status": "cancelled"}
        recorder.calls.append(f"forget:{thing_id}")
        return {"status": "forgotten"}

    apply_default_tool_annotations(server)
    server.add_middleware(StructuredToolErrorMiddleware())
    server.add_middleware(ProductionControlMiddleware(AdmissionController(AdmissionLimits())))
    server.add_middleware(CapabilityCatalogMiddleware())
    return server


@dataclass
class Live:
    url: str
    base_url: str
    keys: SigningKeys
    recorder: Recorder

    @property
    def mcp(self) -> str:
        return f"{self.url}/mcp"

    def token(self, subject: str = "alice", kid: str = "k1", **kwargs: Any) -> str:
        return mint(self.keys.published[kid], kid=kid, subject=subject, issuer=ISSUER, audience=AUDIENCE, **kwargs)

    def post(self, method: str, params: dict[str, Any] | None = None, *, token: str | None = None,
             name: str | None = None, headers: dict[str, str] | None = None, request_id: int = 1,
             version: str = "2026-07-28", meta: dict[str, Any] | None = None) -> httpx.Response:
        return httpx.post(
            self.mcp,
            headers=mcp_headers(method, token=token, name=name, version=version, extra=headers),
            json=mcp_body(request_id, method, params, version=version, meta=meta),
            timeout=TIMEOUT,
        )


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Live]:
    keys = SigningKeys()
    keys.add("k1")
    recorder = Recorder()
    tokens = tmp_path_factory.mktemp("tokens")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("MCP_HTTP_JWT_ISSUER", ISSUER)
        patch.setenv("MCP_SHUTDOWN_GRACE_SECONDS", "1")
        patch.setenv("MCP_USER_TOKEN_DIR", str(tokens))
        patch.setenv("MCP_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
        patch.delenv("MCP_REDIS_URL", raising=False)
        jwks_sock, mcp_sock = reserve_sockets(2)
        jwks_url = "http://{}:{}".format(*jwks_sock.getsockname()[:2])
        url = "http://{}:{}".format(*mcp_sock.getsockname()[:2])
        base_url = f"{url}/gw"
        settings = RemoteSecuritySettings(
            base_url=base_url,
            google_oauth_redirect_url=f"{base_url}/google/oauth/callback",
            jwt_audience=AUDIENCE,
            jwt_issuer=ISSUER,
            jwt_jwks_uri=f"{jwks_url}/jwks",
            token_encryption_key="unused",
            user_token_dir=tokens,
        )
        server = _server(recorder)
        server.auth = build_remote_auth(settings, verifier=build_jwt_verifier(settings))
        policy = HttpServingPolicy.from_environment(base_url)
        policy = HttpServingPolicy(
            allowed_hosts=policy.allowed_hosts,
            allowed_origins=policy.allowed_origins,
            max_request_bytes=MAX_BODY,
        )
        app = build_http_app(server, policy)
        with LiveServers([jwks_sock, mcp_sock], [keys.app(), app]):
            live = Live(url, base_url, keys, recorder)
            yield live


# ---------------------------------------------------------------------------
# Authorization discovery and bearer validation
# ---------------------------------------------------------------------------


def test_protected_resource_metadata_is_served_where_the_challenge_points(live: Live) -> None:
    response = live.post("tools/list")
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    metadata_url = challenge.split('resource_metadata="', 1)[1].split('"', 1)[0]
    # Behind a proxy prefix, RFC 9728 inserts the well-known segment after the
    # host and keeps the resource path (/gw/mcp).
    assert metadata_url == f"{live.url}/.well-known/oauth-protected-resource/gw/mcp"

    metadata = httpx.get(metadata_url, timeout=TIMEOUT)
    assert metadata.status_code == 200
    document = metadata.json()
    assert document["resource"] == f"{live.base_url}/mcp"
    assert [server.rstrip("/") for server in document["authorization_servers"]] == [ISSUER]
    assert document["bearer_methods_supported"] == ["header"]
    assert document["resource_name"] == "Google Workspace MCP"


def test_valid_token_is_accepted_and_identifies_the_principal(live: Live) -> None:
    response = live.post(
        "tools/call", {"name": "probe", "arguments": {"value": "who"}}, token=live.token("alice"), name="probe"
    )
    assert response.status_code == 200, response.text
    assert "result" in response.json(), response.text
    assert response.json()["result"]["structuredContent"]["subject"] == "alice"


@pytest.mark.parametrize(
    ("case", "kwargs"),
    [
        ("expired", {"expires_in": -120}),
        ("wrong_issuer", {"extra": {"iss": "https://evil.issuer.test"}}),
        ("wrong_audience", {"extra": {"aud": "another-resource"}}),
        ("missing_exp", {"drop": ("exp",)}),
        ("missing_sub", {"drop": ("sub",)}),
        ("future_nbf", {"extra": {"nbf": 4_102_444_800}}),
    ],
)
def test_invalid_tokens_are_rejected_before_any_tool_runs(live: Live, case: str, kwargs: dict[str, Any]) -> None:
    before = list(live.recorder.calls)
    response = live.post(
        "tools/call", {"name": "probe", "arguments": {"value": case}}, token=live.token(**kwargs), name="probe"
    )
    assert response.status_code == 401, (case, response.text)
    assert 'error="invalid_token"' in response.headers["www-authenticate"]
    assert live.recorder.calls == before


def test_token_signed_by_an_unpublished_key_is_rejected(live: Live) -> None:
    from fastmcp.server.auth.providers.jwt import RSAKeyPair

    forged = mint(RSAKeyPair.generate(), kid="k1", subject="alice", issuer=ISSUER, audience=AUDIENCE)
    response = live.post("tools/list", token=forged)
    assert response.status_code == 401


def test_jwks_rotation_is_picked_up_and_unknown_kids_cannot_force_refetches(live: Live) -> None:
    live.keys.add("k2")
    rotated = live.post("tools/list", token=live.token("alice", kid="k2"))
    assert rotated.status_code == 200, rotated.text
    fetches = live.keys.fetches

    live.keys.add("k3")
    # Another unknown kid right after a forced refresh: rejected without
    # hitting the key server again (bounded refetch).
    storm = live.post("tools/list", token=live.token("alice", kid="k3"))
    assert storm.status_code == 401
    assert live.keys.fetches == fetches
    # Already-cached keys keep working.
    assert live.post("tools/list", token=live.token("alice", kid="k1")).status_code == 200


# ---------------------------------------------------------------------------
# Routing headers, versions, Host and Origin
# ---------------------------------------------------------------------------


def _b64_header(value: str) -> str:
    return f"=?base64?{base64.b64encode(value.encode()).decode()}?="


@pytest.mark.parametrize(
    ("case", "headers", "version_meta"),
    [
        ("name_mismatch", {"Mcp-Name": "slow"}, None),
        ("method_mismatch", {"Mcp-Method": "tools/list"}, None),
        ("encoded_name_mismatch", {"Mcp-Name": _b64_header("slow")}, None),
        ("malformed_encoded_name", {"Mcp-Name": "=?base64?cHJvYmU?="}, None),
        ("version_header_vs_meta", {}, "2025-11-25"),
    ],
)
def test_routing_header_mismatches_are_rejected_before_execution(
    live: Live, case: str, headers: dict[str, str], version_meta: str | None
) -> None:
    before = list(live.recorder.calls)
    base = mcp_headers("tools/call", token=live.token(), name="probe")
    base.update(headers)
    body = mcp_body(7, "tools/call", {"name": "probe", "arguments": {"value": case}})
    if version_meta is not None:
        body["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = version_meta
    response = httpx.post(live.mcp, headers=base, json=body, timeout=TIMEOUT)
    assert response.status_code == 400, (case, response.text)
    assert response.json()["error"]["code"] == -32020
    assert live.recorder.calls == before


def test_missing_and_duplicated_name_headers_are_rejected(live: Live) -> None:
    before = list(live.recorder.calls)
    body = mcp_body(8, "tools/call", {"name": "probe", "arguments": {"value": "hdr"}})
    missing = mcp_headers("tools/call", token=live.token())
    response = httpx.post(live.mcp, headers=missing, json=body, timeout=TIMEOUT)
    assert response.status_code == 400 and response.json()["error"]["code"] == -32020

    duplicated = list(mcp_headers("tools/call", token=live.token(), name="probe").items()) + [("Mcp-Name", "probe")]
    response = httpx.post(live.mcp, headers=duplicated, json=body, timeout=TIMEOUT)
    assert response.status_code == 400 and response.json()["error"]["code"] == -32020
    assert live.recorder.calls == before


def test_base64_encoded_matching_name_is_accepted(live: Live) -> None:
    response = live.post(
        "tools/call", {"name": "probe", "arguments": {"value": "encoded"}},
        token=live.token(), headers={"Mcp-Name": _b64_header("probe")},
    )
    assert response.status_code == 200, response.text
    assert "encoded" in live.recorder.calls


def test_unknown_protocol_version_is_rejected_with_supported_versions(live: Live) -> None:
    response = live.post("tools/list", token=live.token(), version="2031-01-01")
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == -32022
    assert error["data"] == {"supported": ["2026-07-28"], "requested": "2031-01-01"}


def test_modern_requests_create_no_session(live: Live) -> None:
    response = live.post("tools/list", token=live.token())
    assert response.status_code == 200
    assert "mcp-session-id" not in response.headers
    listed = [tool["name"] for tool in response.json()["result"]["tools"]]
    assert listed == sorted(listed)
    assert response.json()["result"]["ttlMs"] == 0
    assert response.json()["result"]["cacheScope"] == "private"


@pytest.mark.parametrize(
    ("origin", "status"),
    [("https://evil.example", 403), ("null", 403), ("https://127.0.0.1.evil.example", 403), (None, 200), ("self", 200)],
)
def test_origin_is_validated(live: Live, origin: str | None, status: int) -> None:
    headers: dict[str, str] = {}
    if origin == "self":
        headers["Origin"] = live.url
    elif origin is not None:
        headers["Origin"] = origin
    response = live.post("tools/list", token=live.token(), headers=headers)
    assert response.status_code == status, response.text


def test_unexpected_host_is_rejected(live: Live) -> None:
    response = live.post("tools/list", token=live.token(), headers={"Host": "evil.example"})
    assert response.status_code == 421


# ---------------------------------------------------------------------------
# Bounded request bodies, disconnects, request-scoped SSE
# ---------------------------------------------------------------------------


def _raw_request_head(live: Live, *, extra: dict[str, str], name: str = "probe") -> bytes:
    netloc = urlparse(live.url).netloc
    headers = {
        "Host": netloc,
        **mcp_headers("tools/call", token=live.token(), name=name),
        **extra,
    }
    lines = ["POST /mcp HTTP/1.1", *(f"{key}: {value}" for key, value in headers.items())]
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def _connect(live: Live) -> socket.socket:
    host, port = urlparse(live.url).hostname, urlparse(live.url).port
    sock = socket.create_connection((host, port), timeout=TIMEOUT)
    sock.settimeout(TIMEOUT)
    return sock


def _read_status(sock: socket.socket) -> int:
    data = b""
    while b"\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return int(data.split(b" ", 2)[1])


def test_chunked_oversized_body_is_rejected_while_streaming(live: Live) -> None:
    before = list(live.recorder.calls)
    sock = _connect(live)
    try:
        sock.sendall(_raw_request_head(live, extra={"Transfer-Encoding": "chunked"}))
        chunk = b"x" * (64 * 1024)
        sent = 0
        try:
            # Never send the terminating chunk: the rejection must not wait for
            # (or buffer) the complete payload.
            while sent <= MAX_BODY + len(chunk):
                sock.sendall(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                sent += len(chunk)
        except OSError:
            pass  # the server may close the connection once it has answered
        assert _read_status(sock) == 413
    finally:
        sock.close()
    assert live.recorder.calls == before


def test_declared_oversized_body_is_rejected_without_reading_it(live: Live) -> None:
    sock = _connect(live)
    try:
        sock.sendall(_raw_request_head(live, extra={"Content-Length": str(MAX_BODY + 1)}))
        assert _read_status(sock) == 413
    finally:
        sock.close()


def test_small_chunked_body_is_served(live: Live) -> None:
    body = json.dumps(mcp_body(9, "tools/call", {"name": "probe", "arguments": {"value": "chunked-ok"}})).encode()
    sock = _connect(live)
    try:
        sock.sendall(_raw_request_head(live, extra={"Transfer-Encoding": "chunked", "Connection": "close"}))
        sock.sendall(f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n")
        assert _read_status(sock) == 200
    finally:
        sock.close()
    assert "chunked-ok" in live.recorder.calls


def test_client_disconnect_cancels_a_running_tool(live: Live) -> None:
    live.recorder.started.clear()
    live.recorder.cancelled.clear()
    body = json.dumps(mcp_body(10, "tools/call", {"name": "slow", "arguments": {}})).encode()
    sock = _connect(live)
    try:
        sock.sendall(_raw_request_head(live, extra={"Content-Length": str(len(body))}, name="slow") + body)
        assert live.recorder.started.wait(TIMEOUT), "tool never started"
    finally:
        sock.close()
    assert live.recorder.cancelled.wait(TIMEOUT), "closing the response stream did not cancel the tool"


def _sse_messages(text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data:"):].strip())
        for line in text.splitlines()
        if line.startswith("data:")
    ]


def test_progress_is_streamed_as_request_scoped_sse(live: Live) -> None:
    response = live.post(
        "tools/call", {"name": "progress", "arguments": {}}, token=live.token(), name="progress",
        meta={"progressToken": "p-1"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    messages = _sse_messages(response.text)
    progress = [m for m in messages if m.get("method") == "notifications/progress"]
    assert [m["params"]["progress"] for m in progress] == [1, 2]
    assert all(m["params"]["progressToken"] == "p-1" for m in progress)
    assert messages[-1]["id"] == 1 and messages[-1]["result"]["structuredContent"] == {"done": True}


def test_request_without_progress_token_gets_a_plain_json_response(live: Live) -> None:
    response = live.post("tools/call", {"name": "progress", "arguments": {}}, token=live.token(), name="progress")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["result"]["structuredContent"] == {"done": True}


def test_subscriptions_are_not_advertised_or_served(live: Live) -> None:
    discovered = live.post("server/discover", token=live.token())
    assert discovered.json()["result"]["capabilities"]["tools"]["listChanged"] is False
    listen = live.post("subscriptions/listen", {"notifications": {"toolsListChanged": True}}, token=live.token())
    assert listen.status_code == 404
    assert listen.json()["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# Legacy (2025-11-25) connectivity is kept
# ---------------------------------------------------------------------------


def test_legacy_client_still_connects_calls_and_confirms(live: Live) -> None:
    prompts: list[str] = []

    async def accept(message: str, _type: Any, params: Any, _context: Any) -> Any:
        prompts.append(message)
        return {name: True for name in params.requested_schema.get("properties", {})}

    async def scenario() -> tuple[str | None, list[str], Any, Any]:
        transport = StreamableHttpTransport(live.mcp, auth=live.token("alice"))
        async with Client(transport, mode="legacy", elicitation_handler=accept) as client:
            tools = [tool.name for tool in await client.list_tools()]
            probed = await client.call_tool("probe", {"value": "legacy"})
            forgot = await client.call_tool("forget_thing", {"thing_id": "t-1"})
            return client.protocol_version, tools, probed, forgot

    version, tools, probed, forgot = anyio.run(scenario)
    assert version == "2025-11-25"
    assert {"probe", "forget_thing"} <= set(tools)
    assert probed.structured_content["subject"] == "alice"
    assert forgot.structured_content == {"status": "forgotten"}
    assert prompts == ["Forget t-1?"]
    assert "forget:t-1" in live.recorder.calls


def test_legacy_handshake_still_issues_a_session(live: Live) -> None:
    response = httpx.post(
        live.mcp,
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {live.token()}",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "legacy-raw", "version": "0"},
            },
        },
        timeout=TIMEOUT,
    )
    assert response.status_code == 200, response.text
    assert response.headers.get("mcp-session-id")
