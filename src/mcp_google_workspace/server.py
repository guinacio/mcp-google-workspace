"""Composed Google Workspace FastMCP server."""

from __future__ import annotations

from fastmcp import FastMCP

from .apps import apps_mcp
from .common.component_annotations import apply_default_tool_annotations
from .auth import (
    is_apps_dashboard_enabled,
    is_chat_enabled,
    is_gemini_enabled,
    is_keep_enabled,
    is_meet_enabled,
)
from .calendar import calendar_mcp
from .chat import chat_mcp
from .docs import docs_mcp
from .drive import drive_mcp
from .forms import forms_mcp
from .gemini import gemini_mcp
from .gmail import gmail_mcp
from .keep import keep_mcp
from .meet import meet_mcp
from .people import people_mcp
from .sheets import sheets_mcp
from .slides import slides_mcp
from .tasks import tasks_mcp

workspace_mcp = FastMCP(
    name="google-workspace-mcp",
    instructions=(
        "Unified Google Workspace MCP server with Gmail, Calendar, Drive, Docs, Sheets, "
        "Tasks, People, Forms, Slides, and optional Meet/Keep/Chat/Gemini integrations."
    ),
)

workspace_mcp.mount(gmail_mcp, namespace="gmail")
workspace_mcp.mount(calendar_mcp, namespace="calendar")
workspace_mcp.mount(drive_mcp, namespace="drive")
workspace_mcp.mount(sheets_mcp, namespace="sheets")
workspace_mcp.mount(docs_mcp, namespace="docs")
workspace_mcp.mount(tasks_mcp, namespace="tasks")
workspace_mcp.mount(people_mcp, namespace="people")
workspace_mcp.mount(forms_mcp, namespace="forms")
workspace_mcp.mount(slides_mcp, namespace="slides")
if is_apps_dashboard_enabled():
    workspace_mcp.mount(apps_mcp, namespace="apps")
if is_chat_enabled():
    workspace_mcp.mount(chat_mcp, namespace="chat")
if is_gemini_enabled():
    workspace_mcp.mount(gemini_mcp, namespace="gemini")
if is_keep_enabled():
    workspace_mcp.mount(keep_mcp, namespace="keep")
if is_meet_enabled():
    workspace_mcp.mount(meet_mcp, namespace="meet")

apply_default_tool_annotations(workspace_mcp)
