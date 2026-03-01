"""Read resources for workspace dashboard and morning briefing."""

from __future__ import annotations

import json
from datetime import date

from fastmcp import FastMCP

from .schemas import DashboardStatePatch, MorningBriefingRequest
from .state import get_state, patch_state
from .tools import (
    build_dashboard_payload,
    build_morning_briefing_payload,
    build_weekly_calendar_payload,
)


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

    @server.resource("apps://briefing/morning/{ymd}", name="apps_morning_briefing")
    async def apps_morning_briefing(ymd: str) -> str:
        target = date.fromisoformat(ymd)
        state = patch_state("resource-default", DashboardStatePatch(anchor_date=target))
        payload = build_morning_briefing_payload(
            state,
            MorningBriefingRequest(date=target, session_id="resource-default"),
        )
        return json.dumps(payload, indent=2)
