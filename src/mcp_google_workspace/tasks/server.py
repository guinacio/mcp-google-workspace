"""Tasks FastMCP subserver."""

from __future__ import annotations

from fastmcp import FastMCP

from ..common.component_annotations import apply_default_tool_annotations
from .tools import register_tools

tasks_mcp = FastMCP(name="tasks-mcp", instructions="Google Tasks MCP subserver.")

register_tools(tasks_mcp)

apply_default_tool_annotations(tasks_mcp)
