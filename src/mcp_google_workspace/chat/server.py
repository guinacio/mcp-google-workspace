"""Google Chat FastMCP subserver."""

from __future__ import annotations

from fastmcp import FastMCP

from ..common.component_annotations import apply_default_tool_annotations
from .prompts import register_prompts
from .resources import register_resources
from .tools import register_tools

chat_mcp = FastMCP(name="chat-mcp", instructions="Google Chat MCP subserver.")

register_tools(chat_mcp)
register_resources(chat_mcp)
register_prompts(chat_mcp)


apply_default_tool_annotations(chat_mcp)
