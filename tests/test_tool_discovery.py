from __future__ import annotations

import anyio
from fastmcp import Client, FastMCP

from mcp_google_workspace import tool_discovery


async def _catalog(server: FastMCP) -> set[str]:
    async with Client(server) as client:
        return {tool.name for tool in await client.list_tools()}


def _server() -> FastMCP:
    server = FastMCP("discovery-test")

    @server.tool(name="alpha_lookup")
    def alpha_lookup(query: str) -> dict[str, str]:
        """Look up an alpha record by query."""
        return {"query": query}

    @server.tool(name="beta_update")
    def beta_update(value: str) -> dict[str, str]:
        """Update a beta record."""
        return {"value": value}

    return server


def test_progressive_discovery_is_enabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("MCP_TOOL_SEARCH", raising=False)
    monkeypatch.delenv("MCP_CLIENT_MODEL", raising=False)
    tool_discovery._CONFIGURED_SERVERS.clear()
    server = _server()
    assert tool_discovery.configure_tool_search(server)
    assert anyio.run(_catalog, server) == {"search_tools", "call_tool"}


def test_claude_auto_mode_keeps_the_complete_catalog(monkeypatch) -> None:
    monkeypatch.setenv("MCP_TOOL_SEARCH", "auto")
    monkeypatch.setenv("MCP_CLIENT_MODEL", "claude-4-sonnet")
    tool_discovery._CONFIGURED_SERVERS.clear()
    server = _server()
    assert not tool_discovery.configure_tool_search(server)
    assert anyio.run(_catalog, server) == {"alpha_lookup", "beta_update"}


def test_explicit_setting_overrides_client_model(monkeypatch) -> None:
    monkeypatch.setenv("MCP_TOOL_SEARCH", "on")
    monkeypatch.setenv("MCP_CLIENT_MODEL", "claude")
    tool_discovery._CONFIGURED_SERVERS.clear()
    server = _server()
    assert tool_discovery.configure_tool_search(server)
    assert anyio.run(_catalog, server) == {"search_tools", "call_tool"}


async def _search_and_call(server: FastMCP):
    async with Client(server) as client:
        matches = await client.call_tool("search_tools", {"query": "alpha lookup"})
        called = await client.call_tool(
            "call_tool",
            {"name": "alpha_lookup", "arguments": {"query": "needle"}},
        )
    return matches, called


def test_search_results_can_be_called_through_the_proxy(monkeypatch) -> None:
    monkeypatch.setenv("MCP_TOOL_SEARCH", "on")
    tool_discovery._CONFIGURED_SERVERS.clear()
    server = _server()
    tool_discovery.configure_tool_search(server)
    matches, called = anyio.run(_search_and_call, server)
    assert "alpha_lookup" in matches.content[0].text
    assert called.structured_content == {"query": "needle"}


def test_workspace_front_page_shows_service_entry_points_not_plumbing(monkeypatch) -> None:
    """Pins what a client sees before its first search on the composed server."""
    import importlib

    import mcp_google_workspace.server as server_module

    monkeypatch.setenv("MCP_TOOL_SEARCH", "on")
    monkeypatch.delenv("ENABLE_APPS_DASHBOARD", raising=False)
    tool_discovery._CONFIGURED_SERVERS.clear()
    try:
        server = importlib.reload(server_module).workspace_mcp
        assert tool_discovery.configure_tool_search(server)
        visible = anyio.run(_catalog, server)
    finally:
        monkeypatch.delenv("MCP_TOOL_SEARCH", raising=False)
        importlib.reload(server_module)

    assert visible == {
        "call_tool",
        "search_tools",
        "connect_google_workspace",
        "get_google_connection_status",
        "disconnect_google_workspace",
        "search_workspace",
        "resolve_workspace_resource",
        "prepare_workspace_action",
        "commit_workspace_action",
        "gmail_search_emails",
        "gmail_get_mail_digest",
        "calendar_search_events",
        "calendar_get_calendar_digest",
        "drive_list_files",
        "tasks_tasks_digest",
    }
    assert not visible & {
        "uploads_file_manager",
        "uploads_list_files",
        "get_mcp_apps_diagnostics",
    }
