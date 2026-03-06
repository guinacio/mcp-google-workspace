"""People FastMCP subserver."""

from __future__ import annotations

from fastmcp import FastMCP

from ..common.component_annotations import apply_default_tool_annotations
from .tools import register_tools

people_mcp = FastMCP(name="people-mcp", instructions="Google People MCP subserver.")

register_tools(people_mcp)

apply_default_tool_annotations(people_mcp)
