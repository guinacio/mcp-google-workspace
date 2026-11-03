"""Read resources for workspace dashboard and calendar views."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fastmcp import FastMCP

from ..common.async_ops import read_text_file, run_blocking
from ..common.timezone import resolve_user_timezone, user_now
from .addressing import DASHBOARD_UI_MIME, DASHBOARD_UI_URI
from .schemas import DashboardState
from .tools import build_dashboard_payload, build_weekly_calendar_payload

_UI_HTML_PATH = Path(__file__).parent / "ui" / "dist" / "index.html"


async def _resource_state(
    *, anchor_date: date | None = None, view: str = "week"
) -> DashboardState:
    # Resources are pure reads: they build a transient default state and never
    # create, read, or modify a dashboard view.
    timezone_name = await resolve_user_timezone()
    state = DashboardState(
        timezone=timezone_name,
        anchor_date=user_now(timezone_name).date(),
    )
    updates: dict[str, date | str] = {"view": view}
    if anchor_date is not None:
        updates["anchor_date"] = anchor_date
    return state.model_copy(update=updates)


def register_resources(server: FastMCP) -> None:
    @server.resource("apps://dashboard/current", name="apps_dashboard_current")
    async def apps_dashboard_current() -> str:
        payload = await run_blocking(build_dashboard_payload, await _resource_state())
        return json.dumps(payload, indent=2)

    @server.resource("apps://dashboard/day/{ymd}", name="apps_dashboard_day")
    async def apps_dashboard_day(ymd: str) -> str:
        target = date.fromisoformat(ymd)
        payload = await run_blocking(
            build_dashboard_payload,
            await _resource_state(anchor_date=target, view="day"),
        )
        return json.dumps(payload, indent=2)

    @server.resource("apps://dashboard/week/{ymd}", name="apps_dashboard_week")
    async def apps_dashboard_week(ymd: str) -> str:
        target = date.fromisoformat(ymd)
        payload = await run_blocking(
            build_dashboard_payload,
            await _resource_state(anchor_date=target, view="week"),
        )
        return json.dumps(payload, indent=2)

    @server.resource("apps://calendar/week/{ymd}", name="apps_calendar_weekly_view")
    async def apps_calendar_weekly_view(ymd: str) -> str:
        target = date.fromisoformat(ymd)
        payload = await run_blocking(
            build_weekly_calendar_payload,
            await _resource_state(anchor_date=target, view="week"),
            date_override=target,
        )
        return json.dumps(payload, indent=2)

    # No ``_meta.ui.csp``: the single-file dashboard makes no network requests,
    # so the host's restrictive default CSP applies unchanged.
    @server.resource(
        DASHBOARD_UI_URI,
        name="apps_dashboard_ui_mcp",
        mime_type=DASHBOARD_UI_MIME,
    )
    async def apps_dashboard_ui_mcp() -> str:
        return await read_text_file(_UI_HTML_PATH, encoding="utf-8")
