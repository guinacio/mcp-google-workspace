"""Drive FastMCP subserver."""

from __future__ import annotations

from fastmcp import FastMCP

from .resources import register_resources
from .tools import register_tools

drive_mcp = FastMCP(name="drive-mcp", instructions="Google Drive MCP subserver.")

register_tools(drive_mcp)
register_resources(drive_mcp)
