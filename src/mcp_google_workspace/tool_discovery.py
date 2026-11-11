"""Transport-neutral progressive tool discovery configuration."""

from __future__ import annotations

import os
from typing import Any

from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations

from .auth import is_apps_dashboard_enabled

_CONFIGURED_SERVERS: set[int] = set()

# The front page a client sees before its first search: connection and consent
# tools, the cross-service search, and the main read entry point of each core
# service. The upload picker and the diagnostics tool stay reachable through
# search_tools/call_tool; they are plumbing, not a first step.
_ALWAYS_VISIBLE = [
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
]


def tool_search_enabled() -> bool:
    """Return whether the catalog should collapse behind FastMCP Tool Search.

    Opt-in (default ``off``): Claude clients already search large tool
    catalogs themselves, so a second, server-side search_tools/call_tool layer
    only hides tools from them, and Claude Desktop's Apps bridge resolves
    globally-qualified UI tool names from the complete list. ``on`` enables it
    for clients without their own tool search; ``auto`` enables it unless
    ``MCP_CLIENT_MODEL`` names a Claude model.
    """
    mode = os.getenv("MCP_TOOL_SEARCH", "off").strip().lower()
    if mode in {"1", "true", "yes", "on", "enabled"}:
        return True
    if mode in {"0", "false", "no", "off", "disabled"}:
        return False
    if mode != "auto":
        raise ValueError("MCP_TOOL_SEARCH must be auto, on, or off.")
    client_model = os.getenv("MCP_CLIENT_MODEL", "").strip().lower()
    return "claude" not in client_model


_SEARCH_TOOLS_DESCRIPTION = (
    "Find this server's tools that are not listed up front, by describing the task in natural "
    "language (for example 'share a Drive file' or 'create a calendar event'). Searches only the "
    "tools the caller may use; nothing is called. Returns up to 8 tool definitions ranked by "
    "relevance, each in the tools/list shape: name, title, description, inputSchema, "
    "outputSchema and annotations. Run one of them with call_tool."
)
_CALL_TOOL_DESCRIPTION = (
    "Run a tool found with search_tools, by its exact name. Only tools the caller may discover "
    "can be called, and the target tool's own consent prompts and checks still apply. Returns the "
    "target tool's result unchanged, so its content and structured fields follow that tool's "
    "outputSchema as listed by search_tools."
)
_CALL_TOOL_PARAMETER_DESCRIPTIONS = {
    "name": "Exact tool name as returned by search_tools, e.g. 'drive_create_permission'.",
    "arguments": (
        "The target tool's arguments as one object, following the inputSchema search_tools "
        "returned for it; omit for a tool without required arguments."
    ),
}


class AnnotatedBM25SearchTransform(BM25SearchTransform):
    """FastMCP's BM25 Tool Search with behavior annotations on its synthetic tools.

    ``search_tools`` reads this server's own catalog. ``call_tool`` is a proxy
    that can reach any discoverable tool, including ones that send, change or
    delete Google data, so it is declared with the most permissive hints of
    what it may run; each target still carries its own annotations and consent.
    """

    def _make_search_tool(self) -> Tool:
        tool = super()._make_search_tool()
        return tool.model_copy(
            update={
                "title": "Search Tools",
                "description": _SEARCH_TOOLS_DESCRIPTION,
                "annotations": ToolAnnotations(
                    title="Search Tools",
                    read_only_hint=True,
                    destructive_hint=False,
                    idempotent_hint=True,
                    open_world_hint=False,
                ),
            }
        )

    def _make_call_tool(self) -> Tool:
        tool = super()._make_call_tool()
        parameters = dict(tool.parameters)
        properties = {
            key: {**value, "description": _CALL_TOOL_PARAMETER_DESCRIPTIONS.get(key, value.get("description"))}
            for key, value in dict(parameters.get("properties", {})).items()
        }
        parameters["properties"] = properties
        return tool.model_copy(
            update={
                "title": "Call Tool",
                "description": _CALL_TOOL_DESCRIPTION,
                "parameters": parameters,
                "annotations": ToolAnnotations(
                    title="Call Tool",
                    read_only_hint=False,
                    destructive_hint=True,
                    idempotent_hint=False,
                    open_world_hint=True,
                ),
            }
        )


def configure_tool_search(server: Any) -> bool:
    """Install FastMCP's BM25 Tool Search once when enabled for this client."""
    server_id = id(server)
    if server_id in _CONFIGURED_SERVERS or not tool_search_enabled():
        return False
    always_visible = list(_ALWAYS_VISIBLE)
    if is_apps_dashboard_enabled():
        always_visible.append("apps_get_dashboard")
    server.add_transform(
        AnnotatedBM25SearchTransform(
            max_results=8,
            always_visible=always_visible,
            search_tool_name="search_tools",
            call_tool_name="call_tool",
        )
    )
    _CONFIGURED_SERVERS.add(server_id)
    return True


def tool_search_diagnostics() -> dict[str, object]:
    mode = os.getenv("MCP_TOOL_SEARCH", "off").strip().lower()
    model = os.getenv("MCP_CLIENT_MODEL", "").strip()
    return {
        "enabled": tool_search_enabled(),
        "mode": mode,
        "client_model": model or None,
        "disabled_for_claude": mode == "auto" and "claude" in model.lower(),
        "max_results": 8,
        "always_visible": list(_ALWAYS_VISIBLE),
    }
