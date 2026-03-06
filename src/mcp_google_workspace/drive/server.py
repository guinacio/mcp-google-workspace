"""Drive FastMCP subserver."""

from __future__ import annotations

from fastmcp import FastMCP

from ..common.component_annotations import apply_default_tool_annotations
from .resources import register_resources
from .tools import register_tools

drive_mcp = FastMCP(name="drive-mcp", instructions="Google Drive MCP subserver.")

register_tools(drive_mcp)
register_resources(drive_mcp)

apply_default_tool_annotations(drive_mcp)
