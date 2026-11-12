"""MCP Server Card (SEP-2127) endpoint."""

from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient

from mcp_google_workspace.common.server_card import MEDIA_TYPE, SCHEMA_URL
from mcp_google_workspace.server import SERVER_TITLE, workspace_mcp


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_HTTP_BASE_URL", "https://mcp.example.test")
    with TestClient(workspace_mcp.http_app(path="/mcp"), base_url="https://internal.test") as test_client:
        yield test_client


def test_card_is_public_cacheable_and_matches_server_info(client: TestClient) -> None:
    response = client.get("/mcp/server-card", headers={"Accept": MEDIA_TYPE})
    assert response.status_code == 200
    assert response.headers["content-type"] == MEDIA_TYPE
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["cache-control"] == "public, max-age=3600"
    card = response.json()
    # Required fields and the v1 schema constraints.
    assert card["$schema"] == SCHEMA_URL
    assert re.fullmatch(r"[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+", card["name"])
    assert 1 <= len(card["description"]) <= 100
    assert card["version"] == workspace_mcp.version
    assert card["title"] == SERVER_TITLE
    # The public endpoint comes from MCP_HTTP_BASE_URL, not the internal request host.
    (remote,) = card["remotes"]
    assert remote["type"] == "streamable-http"
    assert remote["url"] == "https://mcp.example.test/mcp"
    assert "2026-07-28" in remote["supportedProtocolVersions"]
    assert all(set(icon) >= {"src"} for icon in card["icons"])


def test_card_revalidates_with_etag(client: TestClient) -> None:
    first = client.get("/mcp/server-card")
    again = client.get("/mcp/server-card", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.content == b""
