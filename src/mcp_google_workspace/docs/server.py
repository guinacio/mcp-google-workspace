"""Docs FastMCP subserver."""

from __future__ import annotations

from fastmcp import FastMCP

from ..common.component_annotations import apply_default_tool_annotations
from .tools import register_tools

docs_mcp = FastMCP(name="docs-mcp", instructions="Google Docs MCP subserver.")

register_tools(docs_mcp)

apply_default_tool_annotations(docs_mcp)
