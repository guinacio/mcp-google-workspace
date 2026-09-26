from __future__ import annotations

import json
from datetime import date

import anyio
import pytest
from fastmcp import Client, FastMCP
from jsonschema import validate

import mcp_google_workspace.apps.resources as apps_resources
import mcp_google_workspace.apps.tools as apps_tools
from mcp_google_workspace.apps.schemas import DashboardState
from mcp_google_workspace.apps.server import apps_mcp
from mcp_google_workspace.common.app_state import (
    MemoryAppStateStore,
    reset_default_app_state_store,
)
from mcp_google_workspace.common.component_annotations import apply_default_tool_annotations

_STORE = MemoryAppStateStore()


@pytest.fixture(autouse=True)
def clear_apps_state(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve_user_timezone() -> str:
        return "America/Sao_Paulo"

    global _STORE
    _STORE = MemoryAppStateStore()
    reset_default_app_state_store(_STORE)
    monkeypatch.setattr(apps_tools, "resolve_user_timezone", fake_resolve_user_timezone)
    monkeypatch.setattr(apps_resources, "resolve_user_timezone", fake_resolve_user_timezone)
    yield
    reset_default_app_state_store(None)


def _as_dict(result) -> dict:
    if result.structured_content is not None:
        return result.structured_content
    data = result.data
    return data.model_dump(mode="json") if hasattr(data, "model_dump") else data


async def _call(name: str, arguments: dict | None = None, **options):
    """One modern (2026-07-28) request on its own client connection."""
    async with Client(apps_mcp, **options) as client:
        return await client.call_tool(name, arguments or {}, raise_on_error=False)


@pytest.mark.parametrize("namespace", [None, "apps"])
def test_dashboard_ui_metadata_reaches_client(namespace: str | None) -> None:
    server = apps_mcp
    if namespace:
        server = FastMCP("dashboard-metadata-test")
        server.mount(apps_mcp, namespace=namespace)

    async def catalog():
        async with Client(server) as client:
            return {tool.name: tool.model_dump(by_alias=True) for tool in await client.list_tools()}

    tools = anyio.run(catalog)
    prefix = f"{namespace}_" if namespace else ""
    for name in ("get_dashboard", "get_weekly_calendar_view"):
        metadata = tools[f"{prefix}{name}"]["_meta"]
        assert metadata["ui"]["resourceUri"] == "ui://apps/dashboard-ui"
        assert metadata["ui/resourceUri"] == "ui://apps/dashboard-ui"


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
        "view": {
            "handle": "wsv_" + "a" * 43,
            "revision": 1,
            "expires_at": 1_790_000_000,
            "ttl_seconds": 86_400,
        },
    }

    assert published["get_weekly_calendar_view"]["properties"]["total_events"]["type"] == "integer"
    validate(payload, published["get_weekly_calendar_view"])


async def _client_session_state_scenario() -> tuple[dict, dict, dict, dict]:
    # W3: no transport-session fallback. The launch tool mints a server-issued
    # view handle and every later call passes it; each call below is a separate
    # MCP 2026-07-28 request on its own client connection.
    opened = await _call("get_dashboard")
    assert opened.meta["mcp-google-workspace/view"] == opened.structured_content["view"]
    handle = opened.structured_content["view"]["handle"]
    await _call(
        "set_state",
        {
            "view_handle": handle,
            "view": "day",
            "anchor_date": "2026-03-05",
            "include_weekend": True,
        },
    )
    state_payload = await _call("get_state", {"view_handle": handle})
    dashboard_payload = await _call(
        "get_dashboard",
        {"view_handle": handle, "date_override": "2026-03-12"},
    )
    weekly_payload = await _call(
        "get_weekly_calendar_view",
        {"view_handle": handle, "include_weekend": False},
    )
    final_state = await _call("get_state", {"view_handle": handle})
    return tuple(
        _as_dict(result)
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

    assert state_payload["state"]["view"] == "day"
    assert state_payload["state"]["anchor_date"] == "2026-03-05"
    assert dashboard_payload["state"]["view"] == "day"
    assert dashboard_payload["state"]["anchor_date"] == "2026-03-12"
    assert weekly_payload["state"]["anchor_date"] == "2026-03-05"
    assert weekly_payload["state"]["include_weekend"] is False
    assert final_state["state"]["anchor_date"] == "2026-03-05"
    assert final_state["state"]["include_weekend"] is True
    # Overrides are transient: only set_state wrote (revision 1 -> 2).
    assert final_state["view"]["revision"] == 2
    assert dashboard_payload["view"]["handle"] == final_state["view"]["handle"]


async def _calendar_controls_scenario() -> tuple[dict, dict, dict, dict, dict]:
    opened = await _call("get_weekly_calendar_view")
    handle = opened.structured_content["view"]["handle"]
    await _call(
        "set_state",
        {
            "view_handle": handle,
            "view": "week",
            "anchor_date": "2026-03-05",
            "include_weekend": True,
        },
    )
    next_state = await _call("next_range", {"view_handle": handle})
    next_view = await _call("get_weekly_calendar_view", {"view_handle": handle})
    previous_state = await _call("prev_range", {"view_handle": handle})
    weekend_state = await _call(
        "patch_state",
        {"view_handle": handle, "include_weekend": False},
    )
    weekday_view = await _call("get_weekly_calendar_view", {"view_handle": handle})
    return tuple(
        _as_dict(result)
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

    assert next_state["state"]["anchor_date"] == "2026-03-12"
    assert next_view["state"]["anchor_date"] == "2026-03-12"
    assert next_view["total_events"] == 0
    assert len(next_view["days"]) == 7
    assert previous_state["state"]["anchor_date"] == "2026-03-05"
    assert weekend_state["state"]["include_weekend"] is False
    assert weekday_view["state"]["include_weekend"] is False
    assert len(weekday_view["days"]) == 5


def test_apps_resources_are_pure_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apps_resources, "build_dashboard_payload", fake_dashboard_payload)
    monkeypatch.setattr(apps_resources, "build_weekly_calendar_payload", fake_weekly_payload)

    day_payload, week_payload = anyio.run(_resource_read_scenario)

    assert day_payload["state"]["view"] == "day"
    assert day_payload["state"]["anchor_date"] == "2026-03-05"
    assert week_payload["state"]["view"] == "week"
    assert week_payload["state"]["anchor_date"] == "2026-03-09"
    # Resource reads never mint, read, or modify a dashboard view.
    assert _STORE._entries == {}


def test_detail_tools_return_structured_app_error_when_fetch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: object, **kwargs: object) -> dict:
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(apps_tools, "_fetch_event_detail", boom)
    monkeypatch.setattr(apps_tools, "_fetch_email_detail", boom)
    monkeypatch.setattr(apps_tools, "_fetch_email_attachment", boom)

    async def scenario() -> list[dict]:
        async with Client(apps_mcp) as client:
            event = await client.call_tool("get_event_detail", {"event_id": "evt-1"})
            email = await client.call_tool("get_email_detail", {"message_id": "msg-1"})
            attachment = await client.call_tool(
                "get_email_attachment",
                {"message_id": "msg-1", "attachment_id": "att-1"},
            )
            return [
                result.structured_content or result.data
                for result in (event, email, attachment)
            ]

    results = anyio.run(scenario)

    for payload in results:
        error = payload["error"]
        assert error["code"] == "PROVIDER_ERROR"
        assert error["retryable"] is False
        assert "provider exploded" in error["message"]
    assert results[0]["error"]["details"] == {"event_id": "evt-1", "calendar_id": "primary"}
    assert results[1]["error"]["details"] == {"message_id": "msg-1"}
    assert results[2]["error"]["details"] == {"message_id": "msg-1", "attachment_id": "att-1"}


def test_expired_view_handles_fail_with_a_clear_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_google_workspace.apps.server import create_apps_server
    from mcp_google_workspace.apps.state import DashboardViewService

    monkeypatch.setattr(apps_tools, "build_dashboard_payload_with_progress", fake_dashboard_payload_with_progress)
    now = [1_000_000.0]
    clock = lambda: now[0]  # noqa: E731
    server = create_apps_server(
        DashboardViewService(MemoryAppStateStore(clock=clock), ttl_seconds=120, clock=clock)
    )

    async def call(name: str, arguments: dict | None = None):
        async with Client(server) as client:
            return await client.call_tool(name, arguments or {}, raise_on_error=False)

    opened = anyio.run(call, "get_dashboard")
    handle = opened.structured_content["view"]["handle"]
    assert opened.structured_content["view"]["expires_at"] == 1_000_120
    now[0] += 121
    expired = anyio.run(call, "patch_state", {"view_handle": handle, "view": "day"})
    reopened = anyio.run(call, "get_dashboard", {"view_handle": handle})

    for result in (expired, reopened):
        assert result.is_error is True
        assert result.structured_content["code"] == "view_handle_invalid"
        assert result.structured_content["details"] == {"reason": "unknown_or_expired"}
        assert result.structured_content["required_action"] == {
            "action": "open_new_view",
            "tool": "apps_get_dashboard",
            "arguments": {},
        }
        assert "[code: view_handle_invalid]" in result.content[0].text
    fresh = anyio.run(call, "get_dashboard")
    assert fresh.is_error is False
    assert fresh.structured_content["view"]["handle"] != handle


def test_detail_tools_validate_an_optional_view_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apps_tools, "build_dashboard_payload_with_progress", fake_dashboard_payload_with_progress)
    monkeypatch.setattr(apps_tools, "_fetch_email_detail", lambda message_id, tz: {"message_id": message_id})

    async def scenario():
        handle = (await _call("get_dashboard")).structured_content["view"]["handle"]
        with_handle = await _call("get_email_detail", {"message_id": "m1", "view_handle": handle})
        without = await _call("get_email_detail", {"message_id": "m1"})
        rejected = await _call("get_email_detail", {"message_id": "m1", "view_handle": "bogus"})
        return with_handle, without, rejected

    with_handle, without, rejected = anyio.run(scenario)
    assert with_handle.structured_content == {"message_id": "m1"}
    assert without.structured_content == {"message_id": "m1"}
    assert rejected.is_error is True
    assert rejected.structured_content["details"] == {"reason": "malformed"}


def test_state_tools_require_a_view_handle_and_publish_closed_schemas() -> None:
    async def catalog():
        async with Client(apps_mcp) as client:
            return {tool.name: tool for tool in await client.list_tools()}

    tools = anyio.run(catalog)
    for name in ("get_state", "set_state", "patch_state", "next_range", "prev_range", "today"):
        schema = tools[name].input_schema
        assert "view_handle" in schema["required"], name
        assert "session_id" not in schema["properties"], name
        output = tools[name].output_schema
        assert output["additionalProperties"] is False
        assert set(output["required"]) == {"state", "view"}
    for name in ("get_dashboard", "get_weekly_calendar_view"):
        schema = tools[name].input_schema
        assert "view_handle" not in schema["required"]
        assert "session_id" not in schema["properties"]
        assert tools[name].output_schema["properties"]["view"]["required"] == [
            "handle",
            "revision",
            "expires_at",
            "ttl_seconds",
        ]
    for name in ("set_state", "patch_state", "next_range", "prev_range", "today"):
        assert tools[name].input_schema["properties"]["expected_revision"]["anyOf"][0]["minimum"] == 1
