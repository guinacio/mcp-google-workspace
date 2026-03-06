from __future__ import annotations

import json
from datetime import date

import anyio
import pytest
from fastmcp import Client

import mcp_google_workspace.apps.resources as apps_resources
import mcp_google_workspace.apps.tools as apps_tools
from mcp_google_workspace.apps import state as apps_state
from mcp_google_workspace.apps.schemas import DashboardState
from mcp_google_workspace.apps.server import apps_mcp


@pytest.fixture(autouse=True)
def clear_apps_state() -> None:
    apps_state._STATE_BY_SESSION.clear()
    yield
    apps_state._STATE_BY_SESSION.clear()


async def _client_session_state_scenario() -> tuple[dict, dict, dict, dict]:
    async with Client(apps_mcp) as client:
        await client.call_tool(
            "set_state",
            {
                "view": "day",
                "anchor_date": "2026-03-05",
                "include_weekend": True,
            },
        )
        state_payload = await client.call_tool("get_state")
        dashboard_payload = await client.call_tool(
            "get_dashboard",
            {"date_override": "2026-03-12"},
        )
        weekly_payload = await client.call_tool(
            "get_weekly_calendar_view",
            {"include_weekend": False},
        )
        final_state = await client.call_tool("get_state")
        return (
            state_payload.data,
            dashboard_payload.data,
            weekly_payload.data,
            final_state.data,
        )


async def _resource_read_scenario() -> tuple[dict, dict]:
    async with Client(apps_mcp) as client:
        day_contents = await client.read_resource("apps://dashboard/day/2026-03-05")
        week_contents = await client.read_resource("apps://calendar/week/2026-03-09")
        return json.loads(day_contents[0].text), json.loads(week_contents[0].text)


async def fake_dashboard_payload_with_progress(state: DashboardState, ctx) -> dict:
    return {"state": state.model_dump(mode="json")}


async def fake_weekly_payload_with_progress(
    state: DashboardState,
    *,
    ctx,
    date_override: date | None = None,
    include_weekend_override: bool | None = None,
) -> dict:
    weekly_state = state
    if date_override is not None:
        weekly_state = weekly_state.model_copy(update={"anchor_date": date_override})
    if include_weekend_override is not None:
        weekly_state = weekly_state.model_copy(update={"include_weekend": include_weekend_override})
    return {"state": weekly_state.model_dump(mode="json")}


def fake_dashboard_payload(state: DashboardState) -> dict:
    return {"state": state.model_dump(mode="json")}


def fake_weekly_payload(
    state: DashboardState,
    *,
    date_override: date | None = None,
    include_weekend_override: bool | None = None,
) -> dict:
    weekly_state = state
    if date_override is not None:
        weekly_state = weekly_state.model_copy(update={"anchor_date": date_override})
    if include_weekend_override is not None:
        weekly_state = weekly_state.model_copy(update={"include_weekend": include_weekend_override})
    return {"state": weekly_state.model_dump(mode="json")}


def test_apps_tools_use_client_session_for_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apps_tools, "build_dashboard_payload_with_progress", fake_dashboard_payload_with_progress)
    monkeypatch.setattr(apps_tools, "build_weekly_calendar_payload_with_progress", fake_weekly_payload_with_progress)

    state_payload, dashboard_payload, weekly_payload, final_state = anyio.run(_client_session_state_scenario)

    assert state_payload["view"] == "day"
    assert state_payload["anchor_date"] == "2026-03-05"
    assert dashboard_payload["state"]["view"] == "day"
    assert dashboard_payload["state"]["anchor_date"] == "2026-03-12"
    assert weekly_payload["state"]["anchor_date"] == "2026-03-05"
    assert weekly_payload["state"]["include_weekend"] is False
    assert final_state["anchor_date"] == "2026-03-05"
    assert final_state["include_weekend"] is True


def test_apps_resources_are_pure_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    apps_state.set_state(
        "resource-default",
        DashboardState(
            session_id="resource-default",
            view="month",
            anchor_date=date(2026, 1, 1),
        ),
    )
    monkeypatch.setattr(apps_resources, "build_dashboard_payload", fake_dashboard_payload)
    monkeypatch.setattr(apps_resources, "build_weekly_calendar_payload", fake_weekly_payload)

    day_payload, week_payload = anyio.run(_resource_read_scenario)
    persisted = apps_state.get_state("resource-default")

    assert day_payload["state"]["view"] == "day"
    assert day_payload["state"]["anchor_date"] == "2026-03-05"
    assert week_payload["state"]["view"] == "week"
    assert week_payload["state"]["anchor_date"] == "2026-03-09"
    assert persisted.view == "month"
    assert persisted.anchor_date == date(2026, 1, 1)
