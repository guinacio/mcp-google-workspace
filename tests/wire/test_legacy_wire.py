"""Golden wire-contract fixtures: MCP 2025-11-25 (legacy compatibility).

``initialize`` -> ``tools/list`` -> a safe tool call run as raw JSON-RPC over
HTTP (``starlette.testclient.TestClient``). The elicitation confirmation
branch is captured over the in-memory FastMCP transport instead of raw HTTP:
legacy elicitation is a *server-initiated* nested request
(``elicitation/create``) sent back down the same duplex stream while a tool
call is still in flight, which a single stateless ``TestClient.post()`` call
cannot represent. ``tests/test_http_live.py`` (a real uvicorn server) and
``tests/test_confirmation_protocols.py`` establish the same pattern:
``fastmcp.Client(server, mode="legacy", elicitation_handler=...)`` drives the
real duplex round trip. The in-memory transport speaks the same
``SessionMessage``/JSON-RPC wire format a real stdio subprocess would (see
``tests/wire/harness.py``), so this doubles as the "stdio" evidence for the
legacy sequence; the MCPB bundle test covers a real stdio subprocess
separately.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import anyio
import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult
from starlette.testclient import TestClient

import mcp_google_workspace.auth.google_auth as google_auth
from mcp_google_workspace.server import workspace_mcp

from .harness import ACCEPT, assert_matches_fixture, normalize, normalize_headers

LEGACY = "2025-11-25"


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    app = workspace_mcp.http_app(
        transport="http",
        stateless_http=False,
        json_response=True,
        allowed_hosts=["testserver"],
        allowed_origins=["http://testserver"],
    )
    with TestClient(app) as test_client:
        yield test_client


def _legacy_initialize(client: TestClient) -> Any:
    return client.post(
        "/mcp",
        headers={"Accept": ACCEPT, "Content-Type": "application/json"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LEGACY,
                "capabilities": {},
                "clientInfo": {"name": "legacy-wire-test", "version": "0"},
            },
        },
    )


def test_legacy_initialize_then_tools_list_then_safe_call(client: TestClient) -> None:
    init = _legacy_initialize(client)
    assert init.status_code == 200, init.text
    session_id = init.headers.get("mcp-session-id")
    assert session_id

    headers = {"Accept": ACCEPT, "Content-Type": "application/json", "mcp-session-id": session_id}
    listed = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert listed.status_code == 200, listed.text
    by_name = {tool["name"]: tool for tool in listed.json()["result"]["tools"]}

    called = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_workspace_capabilities", "arguments": {}},
        },
    )
    assert called.status_code == 200, called.text

    payload = {
        "initialize": {
            "status_code": init.status_code,
            "headers": normalize_headers(dict(init.headers)),
            "result": init.json()["result"],
        },
        "tools_list": {
            "status_code": listed.status_code,
            "actual_tool_count_over_100": len(by_name) >= 100,
            "named_tools": {"get_workspace_capabilities": by_name["get_workspace_capabilities"]},
        },
        "safe_call": {"status_code": called.status_code, "result": called.json()["result"]},
    }
    # Legacy content-length/date-ish headers are dropped; only the session id matters here.
    payload["initialize"]["headers"] = {
        key: value
        for key, value in payload["initialize"]["headers"].items()
        if key.lower() in {"mcp-session-id", "content-type"}
    }
    assert_matches_fixture("legacy_http_initialize_list_call", normalize(payload))


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


async def _legacy_elicitation_round(accept: bool) -> dict[str, Any]:
    prompts: list[str] = []

    async def handler(message: str, _type: Any, params: Any, _context: Any) -> Any:
        prompts.append(message)
        if not accept:
            return ElicitResult(action="decline")
        return {name: True for name in params.requested_schema.get("properties", {})}

    async with Client(workspace_mcp, mode="legacy", elicitation_handler=handler) as fastmcp_client:
        assert fastmcp_client.protocol_version == LEGACY
        result = await fastmcp_client.call_tool(
            "people_delete_contact", {"person_name": "people/c-wire-legacy"}, raise_on_error=False
        )
        protocol_version = fastmcp_client.protocol_version
    return {
        "protocol_version": protocol_version,
        "prompts": prompts,
        "is_error": result.is_error,
        "structured_content": result.structured_content,
    }


def test_legacy_elicitation_confirmation_branch_declined(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _GoogleRecorder(calls))
    payload = anyio.run(_legacy_elicitation_round, False)
    assert calls == []
    assert_matches_fixture("legacy_stdio_elicitation_declined", normalize(payload))


def test_legacy_elicitation_confirmation_branch_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _GoogleRecorder(calls))
    payload = anyio.run(_legacy_elicitation_round, True)
    assert calls == ["people.deleteContact"]
    assert_matches_fixture("legacy_stdio_elicitation_accepted", normalize(payload))
