"""Apps FastMCP subserver."""

from __future__ import annotations

from fastmcp import FastMCP

from .resources import register_resources
from .tools import register_tools

apps_mcp = FastMCP(
    name="apps-mcp",
    instructions=(
        "Workspace dashboard and morning briefing MCP app layer that composes "
        "calendar, inbox, and actionable scheduling workflows."
    ),
)

register_tools(apps_mcp)
register_resources(apps_mcp)
