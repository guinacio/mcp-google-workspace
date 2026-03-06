"""Meet FastMCP subserver."""

from __future__ import annotations

from fastmcp import FastMCP

from ..common.component_annotations import apply_default_tool_annotations
from .tools import register_tools

meet_mcp = FastMCP(name="meet-mcp", instructions="Google Meet MCP subserver.")

register_tools(meet_mcp)

apply_default_tool_annotations(meet_mcp)
