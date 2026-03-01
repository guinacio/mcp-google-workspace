"""Composed Google Workspace FastMCP server."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastmcp import FastMCP

from .apps import apps_mcp
from .auth import get_credentials, is_apps_dashboard_enabled, is_chat_enabled, is_keep_enabled
from .calendar import calendar_mcp
from .chat import chat_mcp
from .drive import drive_mcp
from .gmail import gmail_mcp
from .keep import keep_mcp


@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[dict[str, object]]:
    creds = get_credentials()
    yield {"google_credentials": creds}


workspace_mcp = FastMCP(
    name="google-workspace-mcp",
    instructions=(
        "Unified Gmail + Google Calendar MCP server with production-oriented "
        "operations, attachment handling, search, labels, and MCP Context features."
    ),
    lifespan=lifespan,
)

workspace_mcp.mount(gmail_mcp, namespace="gmail")
workspace_mcp.mount(calendar_mcp, namespace="calendar")
workspace_mcp.mount(drive_mcp, namespace="drive")
if is_apps_dashboard_enabled():
    workspace_mcp.mount(apps_mcp, namespace="apps")
if is_chat_enabled():
    workspace_mcp.mount(chat_mcp, namespace="chat")
if is_keep_enabled():
    workspace_mcp.mount(keep_mcp, namespace="keep")
