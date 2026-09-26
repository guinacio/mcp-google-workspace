"""Apps FastMCP subserver."""

from __future__ import annotations

from fastmcp import FastMCP

from ..common.component_annotations import apply_default_tool_annotations
from .resources import register_resources
from .state import DashboardViewService
from .tools import register_tools


def create_apps_server(views: DashboardViewService | None = None) -> FastMCP:
    """Build the dashboard subserver over ``views`` (default: configured store).

    Every instance built over the same app-state backend sees the same views,
    which is how separate replicas share dashboard state.
    """
    server = FastMCP(
        name="apps-mcp",
        instructions=(
            "Workspace dashboard MCP app layer that composes "
            "calendar, inbox, and actionable scheduling workflows."
        ),
    )
    register_tools(server, views)
    register_resources(server)
    apply_default_tool_annotations(server)
    return server


apps_mcp = create_apps_server()
