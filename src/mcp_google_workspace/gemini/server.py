"""Gemini FastMCP subserver."""

from __future__ import annotations

from fastmcp import FastMCP

from ..common.component_annotations import apply_default_tool_annotations
from .tools import register_tools

gemini_mcp = FastMCP(
    name="gemini-mcp",
    instructions=(
        "Gemini media subserver for local image generation/editing and audio/video understanding."
    ),
)

register_tools(gemini_mcp)

apply_default_tool_annotations(gemini_mcp)
