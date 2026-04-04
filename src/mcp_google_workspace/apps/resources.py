"""Read resources for workspace dashboard and calendar views."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fastmcp import FastMCP

from ..common.async_ops import read_text_file, run_blocking
from .schemas import DashboardState
from .tools import build_dashboard_payload, build_weekly_calendar_payload

_UI_HTML_PATH = Path(__file__).parent / "ui" / "dist" / "index.html"
_MCP_APP_UI_URI = "ui://apps/dashboard-ui"
_MCP_APP_UI_URI_LEGACY = "ui://dashboard-ui"
_MCP_APP_UI_MIME = "text/html;profile=mcp-app"


def _resource_state(*, anchor_date: date | None = None, view: str = "week") -> DashboardState:
    state = DashboardState(session_id="resource-default")
    updates: dict[str, date | str] = {"view": view}
    if anchor_date is not None:
        updates["anchor_date"] = anchor_date
    return state.model_copy(update=updates)


def register_resources(server: FastMCP) -> None:
    @server.resource("apps://dashboard/current", name="apps_dashboard_current")
    async def apps_dashboard_current() -> str:
        payload = await run_blocking(build_dashboard_payload, _resource_state())
        return json.dumps(payload, indent=2)

    @server.resource("apps://dashboard/day/{ymd}", name="apps_dashboard_day")
    async def apps_dashboard_day(ymd: str) -> str:
        target = date.fromisoformat(ymd)
        payload = await run_blocking(
            build_dashboard_payload,
            _resource_state(anchor_date=target, view="day"),
        )
        return json.dumps(payload, indent=2)

    @server.resource("apps://dashboard/week/{ymd}", name="apps_dashboard_week")
    async def apps_dashboard_week(ymd: str) -> str:
        target = date.fromisoformat(ymd)
        payload = await run_blocking(
            build_dashboard_payload,
            _resource_state(anchor_date=target, view="week"),
        )
        return json.dumps(payload, indent=2)

    @server.resource("apps://calendar/week/{ymd}", name="apps_calendar_weekly_view")
    async def apps_calendar_weekly_view(ymd: str) -> str:
        target = date.fromisoformat(ymd)
        payload = await run_blocking(
            build_weekly_calendar_payload,
            _resource_state(anchor_date=target, view="week"),
            date_override=target,
        )
        return json.dumps(payload, indent=2)

    @server.resource(
        _MCP_APP_UI_URI,
        name="apps_dashboard_ui_mcp",
        mime_type=_MCP_APP_UI_MIME,
    )
    async def apps_dashboard_ui_mcp() -> str:
        return await read_text_file(_UI_HTML_PATH, encoding="utf-8")

    @server.resource(
        _MCP_APP_UI_URI_LEGACY,
        name="apps_dashboard_ui_mcp_legacy",
        mime_type=_MCP_APP_UI_MIME,
    )
    async def apps_dashboard_ui_mcp_legacy() -> str:
        return await read_text_file(_UI_HTML_PATH, encoding="utf-8")

    @server.resource(
        "apps://dashboard/ui",
        name="apps_dashboard_ui",
        mime_type="text/html",
    )
    async def apps_dashboard_ui() -> str:
        return await read_text_file(_UI_HTML_PATH, encoding="utf-8")
