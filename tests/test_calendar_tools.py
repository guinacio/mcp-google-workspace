import asyncio
from typing import Any

from fastmcp import Client
from googleapiclient.errors import HttpError
from httplib2 import Response
from jsonschema import Draft202012Validator

import mcp_google_workspace.calendar.tools as calendar_tools
from mcp_google_workspace.calendar.tools import (
    _check_time_slot_conflicts,
    _idempotent_event_id,
    _validate_and_fix_datetime,
)
from mcp_google_workspace.calendar.presentation import event_envelope
from mcp_google_workspace.calendar.server import calendar_mcp
from mcp_google_workspace.server import workspace_mcp


def test_validate_and_fix_datetime_adds_timezone():
    result = _validate_and_fix_datetime("2026-02-28T10:30:00", "UTC")
    assert result is not None
    assert result.endswith("+00:00")


def test_validate_and_fix_datetime_date_only():
    result = _validate_and_fix_datetime("2026-02-28", "UTC")
    assert result is not None
    assert "T00:00:00" in result


def test_idempotent_event_id_is_stable_and_google_calendar_safe() -> None:
    first = _idempotent_event_id("create-123")

    assert first == _idempotent_event_id("create-123")
    assert first != _idempotent_event_id("create-456")
    assert len(first) >= 5
    assert set(first) <= set("0123456789abcdefghijklmnopqrstuv")


def test_event_envelope_surfaces_rsvp_and_meeting_link():
    result = event_envelope(
        {
            "id": "event-1", "summary": "Planning", "start": {"dateTime": "2026-07-10T10:00:00Z"},
            "end": {"dateTime": "2026-07-10T11:00:00Z"}, "attendees": [{"self": True, "responseStatus": "needsAction"}],
            "hangoutLink": "https://meet.google.com/abc-defg-hij",
        },
        account_timezone="America/Sao_Paulo",
    )
    assert result["requires_response"] is True
    assert result["meeting_url"].startswith("https://meet")


class _CalendarRequest:
    def __init__(
        self,
        kind: str,
        event_id: str | None = None,
        body: dict | None = None,
    ) -> None:
        self.kind = kind
        self.event_id = event_id
        self.body = body


class _CalendarEvents:
    def get(self, *, eventId: str, **kwargs) -> _CalendarRequest:
        return _CalendarRequest("get", eventId)

    def patch(self, *, eventId: str, body: dict, **kwargs) -> _CalendarRequest:
        return _CalendarRequest("patch", eventId, body)


class _ExecutedRequest:
    def __init__(self, result: dict) -> None:
        self.result = result

    def execute(self) -> dict:
        return self.result


class _ConflictEvents:
    def __init__(self, events: list[dict]) -> None:
        self.events = events

    def list(self, **kwargs) -> _ExecutedRequest:
        return _ExecutedRequest({"items": self.events})


class _ConflictService:
    def __init__(self, events: list[dict]) -> None:
        self._events = events

    def events(self) -> _ConflictEvents:
        return _ConflictEvents(self._events)


class _CalendarService:
    def events(self) -> _CalendarEvents:
        return _CalendarEvents()


def test_read_events_batches_details_and_isolates_missing_ids(monkeypatch) -> None:
    async def fake_timezone() -> str:
        return "UTC"

    async def fake_execute(request: _CalendarRequest) -> dict:
        if request.event_id == "missing":
            raise HttpError(Response({"status": "404"}), b'{"error":{"message":"Not found"}}')
        return {
            "id": request.event_id,
            "summary": "Planning",
            "start": {"dateTime": "2026-07-12T10:00:00Z"},
            "end": {"dateTime": "2026-07-12T11:00:00Z"},
            "attendees": [{"email": "me@example.com", "self": True}],
            "attachments": [{"fileId": "file-1", "title": "Agenda"}],
            "description": "Discuss launch",
        }

    monkeypatch.setattr(calendar_tools, "build_calendar_service", _CalendarService)
    monkeypatch.setattr(calendar_tools, "resolve_user_timezone", fake_timezone)
    monkeypatch.setattr(calendar_tools, "execute_google_request", fake_execute)

    async def scenario() -> dict:
        async with Client(calendar_mcp) as client:
            result = await client.call_tool(
                "read_events",
                {"event_ids": ["event-1", "missing"]},
            )
            return result.structured_content or result.data

    result = asyncio.run(scenario())

    assert result["missing_event_ids"] == ["missing"]
    assert result["missing_count"] == 1
    assert result["events"][0]["id"] == "event-1"
    assert result["events"][0]["attachments"][0]["fileId"] == "file-1"


def test_respond_to_event_updates_only_authenticated_attendee(monkeypatch) -> None:
    patched_bodies: list[dict] = []

    async def fake_execute(request: _CalendarRequest) -> dict:
        if request.kind == "get":
            return {
                "id": request.event_id,
                "attendees": [
                    {"email": "me@example.com", "self": True, "responseStatus": "needsAction"},
                    {"email": "other@example.com", "responseStatus": "accepted"},
                ],
            }
        patched_bodies.append(request.body or {})
        return {"id": request.event_id, **(request.body or {})}

    monkeypatch.setattr(calendar_tools, "build_calendar_service", _CalendarService)
    monkeypatch.setattr(calendar_tools, "execute_google_request", fake_execute)

    async def scenario() -> dict:
        async with Client(calendar_mcp) as client:
            result = await client.call_tool(
                "respond_to_event",
                {"event_id": "event-1", "response_status": "accepted"},
            )
            return result.structured_content or result.data

    result = asyncio.run(scenario())

    assert result["success"] is True
    assert result["response_status"] == "accepted"
    assert patched_bodies[0]["attendees"] == [
        {"email": "me@example.com", "self": True, "responseStatus": "accepted"},
        {"email": "other@example.com", "responseStatus": "accepted"},
    ]


def test_update_conflict_check_excludes_the_event_being_rescheduled() -> None:
    result = _check_time_slot_conflicts(
        _ConflictService(
            [
                {"id": "event-1", "summary": "Current event"},
                {"id": "event-2", "summary": "Actual conflict"},
            ]
        ),
        "primary",
        "2026-07-12T10:00:00Z",
        "2026-07-12T11:00:00Z",
        exclude_event_id="event-1",
    )

    assert result["has_conflicts"] is True
    assert [item["id"] for item in result["conflicts"]] == ["event-2"]


class _DigestEvents:
    def list(self, **kwargs) -> _CalendarRequest:
        return _CalendarRequest("list")


class _DigestService:
    def events(self) -> _DigestEvents:
        return _DigestEvents()


def test_get_calendar_digest_payload_matches_declared_output_schema(monkeypatch) -> None:
    """Regression for issue #5: the scalar window value must satisfy the tool schema."""
    raw_event = {
        "id": "event-1",
        "summary": "Planning",
        "start": {"dateTime": "2026-07-18T10:00:00Z"},
        "end": {"dateTime": "2026-07-18T11:00:00Z"},
        "attendees": [
            {"email": "me@example.com", "self": True, "responseStatus": "needsAction"}
        ],
    }

    async def fake_timezone() -> str:
        return "UTC"

    async def fake_execute(request: _CalendarRequest) -> dict:
        return {"items": [raw_event]}

    monkeypatch.setattr(calendar_tools, "build_calendar_service", _DigestService)
    monkeypatch.setattr(calendar_tools, "resolve_user_timezone", fake_timezone)
    monkeypatch.setattr(calendar_tools, "execute_google_request", fake_execute)

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        digest_tool = await workspace_mcp.get_tool("calendar_get_calendar_digest")
        assert digest_tool is not None
        schema = digest_tool.output_schema
        assert schema is not None
        async with Client(workspace_mcp) as client:
            # An output-schema violation surfaces here as a tool/output error.
            call = await client.call_tool(
                "calendar_get_calendar_digest",
                {"days": 3, "max_results": 10},
            )
        return schema, (call.structured_content or call.data)

    schema, payload = asyncio.run(scenario())

    assert payload["window_day_count"] == 3
    assert payload["count"] == 1
    assert payload["requires_response"][0]["id"] == "event-1"
    assert not list(Draft202012Validator(schema).iter_errors(payload))
