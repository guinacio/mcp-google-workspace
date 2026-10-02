"""Raw Streamable HTTP wire checks against the production composition.

Uses the same transport options as ``server_http.main`` (stateful legacy
handling, JSON responses) without authentication, and speaks raw JSON-RPC so
nothing in a client library can paper over a protocol regression.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS
from starlette.testclient import TestClient

import mcp_google_workspace.auth.google_auth as google_auth
from mcp_google_workspace.common.production import build_version_payload
from mcp_google_workspace.server import workspace_mcp

MODERN = "2026-07-28"
_ACCEPT = "application/json, text/event-stream"


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


def _modern(
    client: TestClient,
    request_id: int,
    method: str,
    params: dict[str, Any] | None = None,
    name: str | None = None,
    capabilities: dict[str, Any] | None = None,
):
    headers = {
        "Accept": _ACCEPT,
        "Content-Type": "application/json",
        "MCP-Protocol-Version": MODERN,
        "Mcp-Method": method,
    }
    if name is not None:
        headers["Mcp-Name"] = name
    body_params = dict(params or {})
    body_params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientInfo": {"name": "wire-test", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": capabilities or {},
    }
    return client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": body_params},
    )


def test_modern_requests_work_without_initialize_or_session(client: TestClient) -> None:
    discovered = _modern(client, 1, "server/discover")
    assert discovered.status_code == 200, discovered.text
    assert "mcp-session-id" not in discovered.headers
    result = discovered.json()["result"]
    assert MODERN in result["supportedVersions"]
    assert "io.modelcontextprotocol/tasks" in result["capabilities"]["extensions"]

    listed = _modern(client, 2, "tools/list")
    assert listed.status_code == 200, listed.text
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert {"get_workspace_capabilities", "files_file_manager"} <= names

    called = _modern(
        client,
        3,
        "tools/call",
        {"name": "get_workspace_capabilities", "arguments": {}},
        name="get_workspace_capabilities",
    )
    assert called.status_code == 200, called.text
    payload = called.json()["result"]
    assert payload.get("isError") in (None, False)
    assert payload["structuredContent"]["status"] == "ok"


def test_modern_routing_header_mismatch_is_rejected_before_execution(client: TestClient) -> None:
    response = _modern(
        client,
        4,
        "tools/call",
        {"name": "get_workspace_capabilities", "arguments": {}},
        name="search_workspace",
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32020


@pytest.mark.parametrize("version", HANDSHAKE_PROTOCOL_VERSIONS)
def test_every_reported_legacy_version_still_negotiates(client: TestClient, version: str) -> None:
    assert version in build_version_payload()["mcp_protocol_versions"]["legacy"]
    response = client.post(
        "/mcp",
        headers={"Accept": _ACCEPT, "Content-Type": "application/json"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": version,
                "capabilities": {},
                "clientInfo": {"name": "legacy-wire-test", "version": "0"},
            },
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["protocolVersion"] == version
    assert response.headers.get("mcp-session-id")


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


_DELETE_CONTACT = {"name": "people_delete_contact", "arguments": {"person_name": "people/c-wire"}}


def test_modern_mutation_asks_first_and_completes_with_input_responses(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MRTR on the raw wire: input_required with no side effect, then completion."""
    calls: list[str] = []
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _GoogleRecorder(calls))
    capabilities = {"elicitation": {"form": {}}}

    asked = _modern(client, 20, "tools/call", _DELETE_CONTACT, name="people_delete_contact", capabilities=capabilities)
    assert asked.status_code == 200, asked.text
    first = asked.json()["result"]
    assert first["resultType"] == "input_required"
    assert "content" not in first and "structuredContent" not in first
    request = first["inputRequests"]["confirm"]
    assert request["method"] == "elicitation/create"
    assert request["params"]["mode"] == "form"
    assert request["params"]["requestedSchema"]["properties"]["value"]["type"] == "boolean"
    state = first["requestState"]
    assert isinstance(state, str) and state
    # Sealed on the wire: the plaintext continuation format is not visible.
    assert not state.startswith("cw1.")
    assert calls == []

    answered = _modern(
        client,
        21,
        "tools/call",
        {
            **_DELETE_CONTACT,
            "inputResponses": {"confirm": {"action": "accept", "content": {"value": True}}},
            "requestState": state,
        },
        name="people_delete_contact",
        capabilities=capabilities,
    )
    assert answered.status_code == 200, answered.text
    done = answered.json()["result"]
    assert done["resultType"] == "complete"
    assert done.get("isError") in (None, False)
    assert done["structuredContent"]["status"] == "deleted"
    assert calls == ["people.deleteContact"]


def test_modern_mutation_without_elicitation_capability_fails_closed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _GoogleRecorder(calls))

    response = _modern(client, 22, "tools/call", _DELETE_CONTACT, name="people_delete_contact")
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["resultType"] == "complete"
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "confirmation_required"
    assert calls == []
