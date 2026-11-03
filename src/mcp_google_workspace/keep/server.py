"""Google Keep FastMCP subserver."""

from __future__ import annotations

from fastmcp import FastMCP

from ..common.component_annotations import apply_default_tool_annotations
from .prompts import register_prompts
from .resources import register_resources
from .tools import register_tools

keep_mcp = FastMCP(name="keep-mcp", instructions="Google Keep MCP subserver.")

register_tools(keep_mcp)
register_resources(keep_mcp)
register_prompts(keep_mcp)


apply_default_tool_annotations(keep_mcp)
