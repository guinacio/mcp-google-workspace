"""Sheets FastMCP subserver."""

from __future__ import annotations

from fastmcp import FastMCP

from ..common.component_annotations import apply_default_tool_annotations
from .tools import register_tools

sheets_mcp = FastMCP(name="sheets-mcp", instructions="Google Sheets MCP subserver.")

register_tools(sheets_mcp)

apply_default_tool_annotations(sheets_mcp)
