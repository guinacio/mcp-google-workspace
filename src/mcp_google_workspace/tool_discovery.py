"""Transport-neutral progressive tool discovery configuration."""

from __future__ import annotations

import os
from typing import Any

from fastmcp.server.transforms.search import BM25SearchTransform

from .auth import is_apps_dashboard_enabled

_CONFIGURED_SERVERS: set[int] = set()

_ALWAYS_VISIBLE = [
    "connect_google_workspace",
    "get_google_connection_status",
    "disconnect_google_workspace",
    "refresh_workspace_catalog",
    "get_workspace_capabilities",
    "get_mcp_apps_diagnostics",
    "search_workspace",
    "resolve_workspace_resource",
    "prepare_workspace_action",
    "commit_workspace_action",
    "files_file_manager",
    "files_list_files",
    "files_list_files_page",
]


def tool_search_enabled() -> bool:
    """Return whether the catalog should collapse behind FastMCP Tool Search.

    ``MCP_TOOL_SEARCH=on|off`` is the explicit override. In ``auto`` mode,
    Claude clients receive the complete catalog because Claude Desktop's Apps
    bridge currently resolves globally-qualified UI tool names from that list.
    Other clients receive progressive discovery by default.
    """
    mode = os.getenv("MCP_TOOL_SEARCH", "auto").strip().lower()
    if mode in {"1", "true", "yes", "on", "enabled"}:
        return True
    if mode in {"0", "false", "no", "off", "disabled"}:
        return False
    if mode != "auto":
        raise ValueError("MCP_TOOL_SEARCH must be auto, on, or off.")
    client_model = os.getenv("MCP_CLIENT_MODEL", "").strip().lower()
    return "claude" not in client_model


def configure_tool_search(server: Any) -> bool:
    """Install FastMCP's BM25 Tool Search once when enabled for this client."""
    server_id = id(server)
    if server_id in _CONFIGURED_SERVERS or not tool_search_enabled():
        return False
    always_visible = list(_ALWAYS_VISIBLE)
    if is_apps_dashboard_enabled():
        always_visible.append("apps_get_dashboard")
    server.add_transform(
        BM25SearchTransform(
            max_results=8,
            always_visible=always_visible,
            search_tool_name="search_tools",
            call_tool_name="call_tool",
        )
    )
    _CONFIGURED_SERVERS.add(server_id)
    return True


def tool_search_diagnostics() -> dict[str, object]:
    mode = os.getenv("MCP_TOOL_SEARCH", "auto").strip().lower()
    model = os.getenv("MCP_CLIENT_MODEL", "").strip()
    return {
        "enabled": tool_search_enabled(),
        "mode": mode,
        "client_model": model or None,
        "disabled_for_claude": mode == "auto" and "claude" in model.lower(),
        "max_results": 8,
        "always_visible": list(_ALWAYS_VISIBLE),
    }
