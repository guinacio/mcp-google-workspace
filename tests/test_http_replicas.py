"""W5: authenticated round-robin across two replicas, without affinity.

Two independently constructed FastMCP applications, each served by its own
uvicorn server on its own port, share only what a real fleet shares: the
issuer's JWKS, the continuation key ring (``MCP_REQUEST_STATE_KEYS``), the
encrypted Google grant store, and one Redis (an in-memory Redis
implementation) holding dashboard state, the W4b operation records and
fleet-wide admission counters. Every request is an independent MCP 2026-07-28
POST sent to the next replica in turn; no request carries or receives an
``Mcp-Session-Id``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import count
import json
from typing import Any

import burner_redis
import httpx
import pytest
from cryptography.fernet import Fernet

from http_harness import LiveServers, SigningKeys, mcp_body, mcp_headers, mint, reserve_sockets

import mcp_google_workspace.apps.tools as apps_tools
import mcp_google_workspace.auth.google_auth as google_auth
from mcp_google_workspace.apps.server import create_apps_server
from mcp_google_workspace.apps.state import DashboardViewService
from mcp_google_workspace.auth.grants import clear_grant_cache
from mcp_google_workspace.auth.identity import Principal
from mcp_google_workspace.auth.remote_auth import build_jwt_verifier, build_remote_auth
from mcp_google_workspace.common.admission import AdmissionController, AdmissionLimits, RedisFleetLimits
from mcp_google_workspace.common.app_state import RedisAppStateStore
from mcp_google_workspace.common.confirmation import (
    build_request_state_security,
    reset_confirmation_keys,
)
from mcp_google_workspace.common.crypto import FernetKeyring
from mcp_google_workspace.common.errors import StructuredToolErrorMiddleware
from mcp_google_workspace.common.operations import (
    OPERATION_META_KEY,
    REDIS_PREFIX as OPERATION_PREFIX,
    OperationOutcomeMiddleware,
    OperationStore,
    set_operation_store,
)
from mcp_google_workspace.common.production import (
    CapabilityCatalogMiddleware,
    ConsequentialActionMiddleware,
    ProductionControlMiddleware,
    production_lifespan,
)
from mcp_google_workspace.people import people_mcp
from mcp_google_workspace.runtime import RemoteSecuritySettings
from mcp_google_workspace.server_http import HttpServingPolicy, build_http_app

ISSUER = "https://issuer.fleet.test"
AUDIENCE = "fleet-mcp"
STATE_KEYS = "f" * 64
RATE_LIMIT = 40
TIMEOUT = 10.0
ELICITATION = {"elicitation": {"form": {}}}
DELETE = {"name": "people_delete_contact", "arguments": {"person_name": "people/c-fleet"}}


class _GoogleRecorder:
    def __init__(self, calls: list[str], path: tuple[str, ...] = ()) -> None:
        self._calls = calls
        self._path = path

    def __getattr__(self, name: str) -> "_GoogleRecorder":
        if name.startswith("__"):
            raise AttributeError(name)
        return _GoogleRecorder(self._calls, (*self._path, name))

    def __call__(self, *_args: Any, **_kwargs: Any) -> "_GoogleRecorder":
        return self

    def execute(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self._calls.append(".".join(self._path))
        return {}


def _credentials(capabilities: list[str]) -> str:
    return json.dumps(
        {
            "token": "google-access-token",
            "refresh_token": "google-refresh-token",
            "client_id": "google-client",
            "client_secret": "google-secret",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": google_auth.get_google_scopes(capabilities),
            "expiry": "2099-01-01T00:00:00Z",
        }
    )


@dataclass
class Fleet:
    urls: list[str]
    keys: SigningKeys
    google_calls: list[str]
    ids: count = field(default_factory=lambda: count(1))
    turn: int = 0

    def token(self, subject: str) -> str:
        return mint(
            self.keys.published["k1"], kid="k1", subject=subject, issuer=ISSUER, audience=AUDIENCE,
            client_id=f"host-{subject}",
        )

    def next_url(self) -> tuple[int, str]:
        index = self.turn % len(self.urls)
        self.turn += 1
        return index, self.urls[index]

    def call(self, subject: str, method: str, params: dict[str, Any] | None = None, *,
             name: str | None = None, capabilities: dict[str, Any] | None = None,
             replica: int | None = None) -> tuple[int, httpx.Response]:
        index, url = self.next_url() if replica is None else (replica, self.urls[replica])
        response = httpx.post(
            f"{url}/mcp",
            headers=mcp_headers(method, token=self.token(subject), name=name),
            json=mcp_body(next(self.ids), method, params, capabilities=capabilities),
            timeout=TIMEOUT,
        )
        assert "mcp-session-id" not in response.headers
        return index, response

    def tool(self, subject: str, name: str, arguments: dict[str, Any] | None = None, *,
             capabilities: dict[str, Any] | None = None, extra: dict[str, Any] | None = None,
             replica: int | None = None) -> tuple[int, dict[str, Any]]:
        params = {"name": name, "arguments": arguments or {}, **(extra or {})}
        index, response = self.call(subject, "tools/call", params, name=name, capabilities=capabilities, replica=replica)
        assert response.status_code == 200, response.text
        return index, response.json()


@pytest.fixture()
def fleet(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Fleet]:
    monkeypatch.setenv("MCP_HTTP_JWT_ISSUER", ISSUER)
    monkeypatch.setenv("MCP_SHUTDOWN_GRACE_SECONDS", "1")
    monkeypatch.setenv("MCP_USER_TOKEN_DIR", str(tmp_path / "grants"))
    monkeypatch.setenv("MCP_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("MCP_REQUEST_STATE_KEYS", STATE_KEYS)
    monkeypatch.delenv("MCP_REDIS_URL", raising=False)
    reset_confirmation_keys()
    clear_grant_cache()
    shared_redis = burner_redis.BurnerRedis()
    keyring = FernetKeyring.single(Fernet.generate_key().decode())
    # W4b operation records (confirmation single use, commit replay) live in
    # the same shared Redis, exactly as build_operation_store() configures it.
    set_operation_store(
        OperationStore(RedisAppStateStore(shared_redis, prefix=OPERATION_PREFIX, keyring=keyring))
    )

    google_calls: list[str] = []
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _GoogleRecorder(google_calls))

    async def timezone() -> str:
        return "America/Sao_Paulo"

    async def dashboard(state, ctx) -> dict[str, Any]:
        return {"state": state.model_dump(mode="json")}

    monkeypatch.setattr(apps_tools, "resolve_user_timezone", timezone)
    monkeypatch.setattr(apps_tools, "build_dashboard_payload_with_progress", dashboard)

    google_auth.get_token_store().save_credentials_json(
        Principal(issuer=ISSUER, subject="alice"), _credentials(["people", "calendar", "gmail"])
    )
    google_auth.get_token_store().save_credentials_json(
        Principal(issuer=ISSUER, subject="bob"), _credentials(["people", "calendar", "gmail"])
    )

    keys = SigningKeys()
    keys.add("k1")
    jwks_sock, *replica_socks = reserve_sockets(3)
    jwks_url = "http://{}:{}".format(*jwks_sock.getsockname()[:2])
    urls = ["http://{}:{}".format(*sock.getsockname()[:2]) for sock in replica_socks]
    public = urls[0]
    settings = RemoteSecuritySettings(
        base_url=public,
        google_oauth_redirect_url=f"{public}/google/oauth/callback",
        jwt_audience=AUDIENCE,
        jwt_issuer=ISSUER,
        jwt_jwks_uri=f"{jwks_url}/jwks",
        token_encryption_key="unused",
        user_token_dir=tmp_path / "grants",
    )
    policy = HttpServingPolicy(
        allowed_hosts=[url.removeprefix("http://") for url in urls],
        allowed_origins=[public],
    )

    def replica():
        from fastmcp import FastMCP

        server = FastMCP(
            "replica",
            lifespan=production_lifespan,
            request_state_security=build_request_state_security(
                environ={"MCP_REQUEST_STATE_KEYS": STATE_KEYS}
            ),
        )
        server.auth = build_remote_auth(settings, verifier=build_jwt_verifier(settings))
        server.add_middleware(StructuredToolErrorMiddleware())
        server.add_middleware(OperationOutcomeMiddleware())
        server.add_middleware(
            ProductionControlMiddleware(
                AdmissionController(
                    AdmissionLimits(rate_limit_per_minute=RATE_LIMIT),
                    fleet=RedisFleetLimits(shared_redis),
                )
            )
        )
        server.add_middleware(CapabilityCatalogMiddleware())
        server.add_middleware(ConsequentialActionMiddleware())
        views = DashboardViewService(RedisAppStateStore(shared_redis, keyring=keyring), ttl_seconds=3600)
        server.mount(create_apps_server(views), namespace="apps")
        server.mount(people_mcp, namespace="people")
        return build_http_app(server, policy)

    try:
        with LiveServers([jwks_sock, *replica_socks], [keys.app(), replica(), replica()]):
            yield Fleet(urls, keys, google_calls)
    finally:
        set_operation_store(None)
        reset_confirmation_keys()
        clear_grant_cache()


def test_modern_flow_alternates_replicas_without_affinity(fleet: Fleet) -> None:
    served: list[int] = []

    # Catalog: identical, sorted, grant-filtered on both replicas.
    catalogs = []
    for _ in range(2):
        index, response = fleet.call("alice", "tools/list")
        served.append(index)
        catalogs.append([tool["name"] for tool in response.json()["result"]["tools"]])
    assert catalogs[0] == catalogs[1] == sorted(catalogs[0])
    assert "people_delete_contact" in catalogs[0] and "apps_get_dashboard" in catalogs[0]

    # Dashboard view opened on one replica, updated on the other.
    index, opened = fleet.tool("alice", "apps_get_dashboard")
    served.append(index)
    handle = opened["result"]["structuredContent"]["view"]["handle"]
    index, patched = fleet.tool(
        "alice", "apps_patch_state",
        {"view_handle": handle, "expected_revision": 1, "view": "day", "anchor_date": "2026-03-05"},
    )
    served.append(index)
    assert patched["result"]["structuredContent"]["view"]["revision"] == 2

    # MRTR confirmation: asked on one replica ...
    index, asked = fleet.tool("alice", DELETE["name"], DELETE["arguments"], capabilities=ELICITATION)
    served.append(index)
    first = asked["result"]
    assert first["resultType"] == "input_required"
    state = first["requestState"]
    assert fleet.google_calls == []
    answer = {"inputResponses": {"confirm": {"action": "accept", "content": {"value": True}}}, "requestState": state}

    # ... a different principal cannot use the continuation (either replica) ...
    index, foreign = fleet.call(
        "bob", "tools/call", {**DELETE, **answer}, name=DELETE["name"], capabilities=ELICITATION
    )
    served.append(index)
    # FastMCP's RequestStateSecurity binds the sealed state to the principal,
    # so the wire boundary refuses it before the tool runs.
    assert foreign.status_code == 400
    assert foreign.json()["error"]["code"] == -32602
    assert foreign.json()["error"]["data"] == {"reason": "invalid_request_state"}
    assert fleet.google_calls == []

    # ... answered on the other replica: exactly one mutation.
    index, done = fleet.tool("alice", DELETE["name"], DELETE["arguments"], capabilities=ELICITATION, extra=answer)
    served.append(index)
    assert done["result"]["resultType"] == "complete"
    assert done["result"].get("isError") in (None, False), done
    assert done["result"]["structuredContent"]["status"] == "deleted"
    assert fleet.google_calls == ["people.deleteContact"]

    # Replaying the same answer on the next replica cannot mutate again: the
    # shared W4b operation record returns the saved result instead.
    index, replayed = fleet.tool("alice", DELETE["name"], DELETE["arguments"], capabilities=ELICITATION, extra=answer)
    served.append(index)
    assert replayed["result"].get("isError") in (None, False), replayed
    assert replayed["result"]["structuredContent"]["status"] == "deleted"
    assert replayed["result"]["_meta"][OPERATION_META_KEY]["replayed"] is True
    assert fleet.google_calls == ["people.deleteContact"]

    # Dashboard state is still the shared, revisioned record.
    index, current = fleet.tool("alice", "apps_get_state", {"view_handle": handle})
    served.append(index)
    assert current["result"]["structuredContent"]["state"]["view"] == "day"
    assert current["result"]["structuredContent"]["view"]["revision"] == 2

    # Round robin really alternated between the two replicas.
    assert served[:6] == [0, 1, 0, 1, 0, 1]
    assert set(served) == {0, 1}


def test_stale_catalog_cannot_execute_after_grant_revocation(fleet: Fleet) -> None:
    _, listed = fleet.call("alice", "tools/list", replica=0)
    assert "people_delete_contact" in [tool["name"] for tool in listed.json()["result"]["tools"]]

    # The user disconnects People (the grant now covers Gmail only).
    google_auth.get_token_store().save_credentials_json(
        Principal(issuer=ISSUER, subject="alice"), _credentials(["gmail"])
    )

    # A client still holding the old catalog calls the tool on another replica.
    _, rejected = fleet.tool("alice", "people_list_contacts", {}, replica=1)
    result = rejected["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "missing_capability"
    assert result["structuredContent"]["required_action"] == {
        "tool": "connect_google_workspace",
        "arguments": {"capabilities": ["people"]},
    }
    assert fleet.google_calls == []

    _, relisted = fleet.call("alice", "tools/list", replica=1)
    names = [tool["name"] for tool in relisted.json()["result"]["tools"]]
    assert not any(name.startswith("people_") for name in names)
    assert "gmail_send_email" not in names  # gmail is not mounted on these replicas
    # Another principal's grant and catalog are unaffected (no shared cache).
    _, bob = fleet.call("bob", "tools/list", replica=0)
    assert "people_delete_contact" in [tool["name"] for tool in bob.json()["result"]["tools"]]

    # Full disconnection (no stored grant) authorizes nothing capability-bound.
    google_auth.get_token_store().delete_credentials(Principal(issuer=ISSUER, subject="alice"))
    _, disconnected = fleet.tool("alice", DELETE["name"], DELETE["arguments"], capabilities=ELICITATION, replica=0)
    assert disconnected["result"]["structuredContent"]["code"] == "missing_capability"
    assert fleet.google_calls == []


def test_per_principal_rate_limit_is_enforced_across_the_fleet(fleet: Fleet) -> None:
    statuses = []
    for _ in range(RATE_LIMIT):
        _, response = fleet.call("carol", "tools/call", {"name": "apps_get_dashboard", "arguments": {}},
                                 name="apps_get_dashboard")
        statuses.append(response.status_code)
    assert set(statuses) == {200}
    # Each replica alone saw only half of the budget, yet the next call (on
    # either replica) is refused: the limit is fleet-wide.
    for replica in (0, 1):
        _, refused = fleet.call("carol", "tools/call", {"name": "apps_get_dashboard", "arguments": {}},
                                name="apps_get_dashboard", replica=replica)
        error = refused.json()["error"]
        assert error["code"] == -32005
        assert error["data"]["code"] == "rate_limited"
        assert error["data"]["retryable"] is True
    # Another principal is unaffected.
    _, other = fleet.call("alice", "tools/list")
    assert other.status_code == 200
