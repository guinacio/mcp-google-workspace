"""Gmail FastMCP server registration."""

from __future__ import annotations

from fastmcp import FastMCP

from ..common.component_annotations import apply_default_tool_annotations
from .prompts import register_prompts
from .resources import register_resources
from .tools import register_tools

gmail_mcp = FastMCP(name="gmail-mcp", instructions="Production Gmail MCP server.")

register_tools(gmail_mcp)
register_resources(gmail_mcp)
register_prompts(gmail_mcp)

apply_default_tool_annotations(gmail_mcp)
