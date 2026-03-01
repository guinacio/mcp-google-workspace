"""Read resources for workspace dashboard and calendar views."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fastmcp import FastMCP

from .schemas import DashboardStatePatch
from .state import get_state, patch_state
from .tools import (
    build_dashboard_payload,
    build_weekly_calendar_payload,
)

_UI_HTML_PATH = Path(__file__).parent / "ui" / "dist" / "index.html"
_MCP_APP_UI_URI = "ui://dashboard-ui"
_MCP_APP_UI_MIME = "text/html;profile=mcp-app"


def register_resources(server: FastMCP) -> None:
    @server.resource("apps://dashboard/current", name="apps_dashboard_current")
    async def apps_dashboard_current() -> str:
        state = get_state("resource-default")
        payload = build_dashboard_payload(state)
        return json.dumps(payload, indent=2)

    @server.resource("apps://dashboard/day/{ymd}", name="apps_dashboard_day")
    async def apps_dashboard_day(ymd: str) -> str:
        target = date.fromisoformat(ymd)
        state = patch_state("resource-default", DashboardStatePatch(anchor_date=target, view="day"))
        payload = build_dashboard_payload(state)
        return json.dumps(payload, indent=2)

    @server.resource("apps://dashboard/week/{ymd}", name="apps_dashboard_week")
    async def apps_dashboard_week(ymd: str) -> str:
        target = date.fromisoformat(ymd)
        state = patch_state("resource-default", DashboardStatePatch(anchor_date=target, view="week"))
        payload = build_dashboard_payload(state)
        return json.dumps(payload, indent=2)

    @server.resource("apps://calendar/week/{ymd}", name="apps_calendar_weekly_view")
    async def apps_calendar_weekly_view(ymd: str) -> str:
        target = date.fromisoformat(ymd)
        state = patch_state("resource-default", DashboardStatePatch(anchor_date=target, view="week"))
        payload = build_weekly_calendar_payload(state, date_override=target)
        return json.dumps(payload, indent=2)

    @server.resource(
        _MCP_APP_UI_URI,
        name="apps_dashboard_ui_mcp",
        mime_type=_MCP_APP_UI_MIME,
    )
    async def apps_dashboard_ui_mcp() -> str:
        return _UI_HTML_PATH.read_text(encoding="utf-8")

    # Backward-compatible URI for existing local integrations.
    @server.resource(
        "apps://dashboard/ui",
        name="apps_dashboard_ui",
        mime_type="text/html",
    )
    async def apps_dashboard_ui() -> str:
        return _UI_HTML_PATH.read_text(encoding="utf-8")
