import json
from datetime import date

import anyio
from fastmcp import Client

from mcp_google_workspace.apps.schemas import DashboardState
from mcp_google_workspace.apps.server import apps_mcp
from mcp_google_workspace.apps.state import get_state, set_state


async def _round_trip_state():
    async with Client(apps_mcp) as client:
        await client.call_tool(
            "set_state",
            {
                "view": "day",
                "anchor_date": "2026-03-10",
                "timezone": "UTC",
            },
        )
        result = await client.call_tool("get_state", {})
        return result.data


async def _dashboard_override_round_trip():
    async with Client(apps_mcp) as client:
        await client.call_tool(
            "set_state",
            {
                "view": "week",
                "anchor_date": "2026-03-02",
                "timezone": "UTC",
            },
        )
        dashboard = await client.call_tool("get_dashboard", {"date_override": "2026-03-09"})
        state = await client.call_tool("get_state", {})
        return dashboard.data, state.data


async def _read_resource(uri: str):
    async with Client(apps_mcp) as client:
        contents = await client.read_resource(uri)
        return contents[0].text


def test_apps_state_reads_follow_client_session():
    state = anyio.run(_round_trip_state)
    assert state["view"] == "day"
    assert state["anchor_date"] == "2026-03-10"
    assert state["timezone"] == "UTC"


def test_dashboard_date_override_does_not_persist(monkeypatch):
    monkeypatch.setattr(
        "mcp_google_workspace.apps.tools.build_dashboard_payload",
        lambda state: {"anchor_date": state.anchor_date.isoformat()},
    )

    async def _fake_with_progress(state, ctx):
        return {"anchor_date": state.anchor_date.isoformat()}

    monkeypatch.setattr(
        "mcp_google_workspace.apps.tools.build_dashboard_payload_with_progress",
        _fake_with_progress,
    )

    dashboard, state = anyio.run(_dashboard_override_round_trip)

    assert dashboard["anchor_date"] == "2026-03-09"
    assert state["anchor_date"] == "2026-03-02"


def test_apps_resources_do_not_mutate_shared_state(monkeypatch):
    set_state("resource-default", DashboardState(session_id="resource-default", anchor_date=date(2026, 3, 1)))
    monkeypatch.setattr(
        "mcp_google_workspace.apps.resources.build_dashboard_payload",
        lambda state: {"anchor_date": state.anchor_date.isoformat(), "view": state.view},
    )

    payload = anyio.run(_read_resource, "apps://dashboard/day/2026-03-11")

    assert json.loads(payload) == {"anchor_date": "2026-03-11", "view": "day"}
    assert get_state("resource-default").anchor_date == date(2026, 3, 1)
