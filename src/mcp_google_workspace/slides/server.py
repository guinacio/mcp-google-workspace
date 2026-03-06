"""Slides FastMCP subserver."""

from __future__ import annotations

from fastmcp import FastMCP

from ..common.component_annotations import apply_default_tool_annotations
from .tools import register_tools

slides_mcp = FastMCP(name="slides-mcp", instructions="Google Slides MCP subserver.")

register_tools(slides_mcp)

apply_default_tool_annotations(slides_mcp)
