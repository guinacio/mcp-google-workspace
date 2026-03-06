"""Forms FastMCP subserver."""

from __future__ import annotations

from fastmcp import FastMCP

from ..common.component_annotations import apply_default_tool_annotations
from .tools import register_tools

forms_mcp = FastMCP(name="forms-mcp", instructions="Google Forms MCP subserver.")

register_tools(forms_mcp)

apply_default_tool_annotations(forms_mcp)
