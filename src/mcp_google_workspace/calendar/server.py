"""Calendar FastMCP subserver."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytz
from fastmcp import FastMCP

from ..auth import build_calendar_service
from ..common.async_ops import execute_google_request
from ..common.component_annotations import apply_default_tool_annotations
from .tools import register_tools

calendar_mcp = FastMCP(name="calendar-mcp", instructions="Google Calendar MCP subserver.")

register_tools(calendar_mcp)


@calendar_mcp.resource("calendar://today", name="calendar_today")
async def calendar_today() -> str:
    service = build_calendar_service()
    now = datetime.now(pytz.UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    events = (
        service.events()
        .list(calendarId="primary", timeMin=start, timeMax=end, singleEvents=True, orderBy="startTime")
    )
    events = await execute_google_request(events)
    return json.dumps(events, indent=2)


@calendar_mcp.resource("calendar://week", name="calendar_week")
async def calendar_week() -> str:
    service = build_calendar_service()
    now = datetime.now(pytz.UTC)
    end = now + timedelta(days=7)
    events = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
        )
    )
    events = await execute_google_request(events)
    return json.dumps(events, indent=2)


apply_default_tool_annotations(calendar_mcp)
