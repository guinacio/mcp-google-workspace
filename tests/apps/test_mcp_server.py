from __future__ import annotations

import json
from datetime import date

import anyio
import pytest
from fastmcp import Client
from jsonschema import validate

import mcp_google_workspace.apps.resources as apps_resources
import mcp_google_workspace.apps.tools as apps_tools
from mcp_google_workspace.apps import state as apps_state
from mcp_google_workspace.apps.schemas import DashboardState
from mcp_google_workspace.apps.server import apps_mcp
from mcp_google_workspace.common.component_annotations import apply_default_tool_annotations


@pytest.fixture(autouse=True)
def clear_apps_state(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve_user_timezone() -> str:
        return "America/Sao_Paulo"

    apps_state._STATE_BY_SESSION.clear()
    monkeypatch.setattr(apps_tools, "resolve_user_timezone", fake_resolve_user_timezone)
    monkeypatch.setattr(apps_resources, "resolve_user_timezone", fake_resolve_user_timezone)
    yield
    apps_state._STATE_BY_SESSION.clear()


def test_detail_tool_output_schemas_accept_complete_ui_payloads() -> None:
    apply_default_tool_annotations(apps_mcp)

    async def schemas():
        tools = await apps_mcp.list_tools(run_middleware=False)
        return {tool.name: tool.output_schema for tool in tools}

    published = anyio.run(schemas)
    validate(
        {
            "message_id": "message-1",
            "thread_id": "thread-1",
            "subject": "Subject",
            "from_value": "Sender <sender@example.com>",
            "to": "me@example.com",
            "cc": None,
            "bcc": None,
            "date": "2026-07-11T20:00:00Z",
            "date_timezone": "America/Sao_Paulo",
            "source_date": "Fri, 11 Jul 2026 20:00:00 +0000",
            "snippet": "Preview",
            "text_body": "Complete body",
            "html_body": None,
            "attachments": [],
            "labels": ["INBOX"],
            "is_unread": False,
        },
        published["get_email_detail"],
    )
    validate(
        {
            "event_id": "event-1",
            "calendar_id": "primary",
            "title": "Planning",
            "start": "2026-07-11T20:00:00Z",
            "end": "2026-07-11T20:30:00Z",
            "timezone": "America/Sao_Paulo",
            "status": "confirmed",
            "location": None,
            "description": "Agenda",
            "conference_link": None,
            "conference_provider": None,
            "organizer_email": "owner@example.com",
            "organizer_name": "Owner",
            "self_response_status": "accepted",
            "attendees": [],
            "attachments": [],
        },
        published["get_event_detail"],
    )


def test_weekly_tool_output_schema_accepts_complete_ui_payload() -> None:
    apply_default_tool_annotations(apps_mcp)

    async def schemas():
        tools = await apps_mcp.list_tools(run_middleware=False)
        return {tool.name: tool.output_schema for tool in tools}

    published = anyio.run(schemas)
    payload = {
        "state": {
            "session_id": "session-1",
            "view": "week",
            "anchor_date": "2026-07-06",
            "timezone": "America/Sao_Paulo",
            "include_weekend": True,
            "selected_calendars": [],
            "selected_email_labels": ["INBOX"],
        },
        "week_start": "2026-07-06",
        "week_end": "2026-07-12",
        "timezone": "America/Sao_Paulo",
        "total_events": 1,
        "days": [
            {
                "date": "2026-07-06",
                "label": "Mon 6",
                "is_today": False,
                "events": [],
            }
        ],
        "fallback_text": "1 event from July 6 through July 12.",
    }

    assert published["get_weekly_calendar_view"]["properties"]["total_events"]["type"] == "integer"
    validate(payload, published["get_weekly_calendar_view"])


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
        def as_dict(result) -> dict:
            if result.structured_content is not None:
                return result.structured_content
            data = result.data
            return data.model_dump(mode="json") if hasattr(data, "model_dump") else data

        return tuple(
            as_dict(result)
            for result in (
                state_payload,
                dashboard_payload,
                weekly_payload,
                final_state,
            )
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
    visible_days = 7 if weekly_state.include_weekend else 5
    return {
        "state": weekly_state.model_dump(mode="json"),
        "week_start": "2026-03-02",
        "week_end": "2026-03-08",
        "timezone": weekly_state.timezone,
        "total_events": 0,
        "days": [
            {
                "date": f"2026-03-{day:02d}",
                "label": f"Day {day}",
                "is_today": False,
                "events": [],
            }
            for day in range(2, 2 + visible_days)
        ],
        "fallback_text": "No events this week.",
    }


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


async def _calendar_controls_scenario() -> tuple[dict, dict, dict, dict, dict]:
    async with Client(apps_mcp) as client:
        await client.call_tool(
            "set_state",
            {
                "session_id": "ui-controls",
                "view": "week",
                "anchor_date": "2026-03-05",
                "include_weekend": True,
            },
        )
        next_state = await client.call_tool("next_range", {"session_id": "ui-controls"})
        next_view = await client.call_tool(
            "get_weekly_calendar_view",
            {"session_id": "ui-controls"},
        )
        previous_state = await client.call_tool("prev_range", {"session_id": "ui-controls"})
        weekend_state = await client.call_tool(
            "patch_state",
            {"session_id": "ui-controls", "include_weekend": False},
        )
        weekday_view = await client.call_tool(
            "get_weekly_calendar_view",
            {"session_id": "ui-controls"},
        )

        def as_dict(result) -> dict:
            if result.structured_content is not None:
                return result.structured_content
            data = result.data
            return data.model_dump(mode="json") if hasattr(data, "model_dump") else data

        return tuple(
            as_dict(result)
            for result in (next_state, next_view, previous_state, weekend_state, weekday_view)
        )


def test_calendar_controls_persist_and_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        apps_tools,
        "build_weekly_calendar_payload_with_progress",
        fake_weekly_payload_with_progress,
    )

    next_state, next_view, previous_state, weekend_state, weekday_view = anyio.run(
        _calendar_controls_scenario
    )

    assert next_state["anchor_date"] == "2026-03-12"
    assert next_view["state"]["anchor_date"] == "2026-03-12"
    assert next_view["total_events"] == 0
    assert len(next_view["days"]) == 7
    assert previous_state["anchor_date"] == "2026-03-05"
    assert weekend_state["include_weekend"] is False
    assert weekday_view["state"]["include_weekend"] is False
    assert len(weekday_view["days"]) == 5


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
