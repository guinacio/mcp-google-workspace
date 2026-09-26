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


def _modern(client: TestClient, request_id: int, method: str, params: dict[str, Any] | None = None, name: str | None = None):
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
        "io.modelcontextprotocol/clientCapabilities": {},
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
