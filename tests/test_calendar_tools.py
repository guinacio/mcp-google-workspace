import asyncio
from typing import Any

from fastmcp import Client
from googleapiclient.errors import HttpError
from httplib2 import Response
from jsonschema import Draft202012Validator

import mcp_google_workspace.calendar.tools as calendar_tools
from mcp_google_workspace.common import confirmation
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
        **kwargs: Any,
    ) -> None:
        self.kind = kind
        self.event_id = event_id
        self.body = body
        self.kwargs = kwargs


class _CalendarEvents:
    def get(self, *, eventId: str, **kwargs) -> _CalendarRequest:
        return _CalendarRequest("get", eventId, **kwargs)

    def patch(self, *, eventId: str, body: dict, **kwargs) -> _CalendarRequest:
        return _CalendarRequest("patch", eventId, body, **kwargs)


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
    assert result["events"][0]["attachments"][0] == {
        "title": "Agenda",
        "file_url": None,
        "file_id": "file-1",
        "mime_type": None,
    }


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

    async def fake_timezone() -> str:
        return "UTC"

    monkeypatch.setattr(calendar_tools, "build_calendar_service", _CalendarService)
    monkeypatch.setattr(calendar_tools, "execute_google_request", fake_execute)
    monkeypatch.setattr(calendar_tools, "resolve_user_timezone", fake_timezone)

    async def scenario() -> dict:
        async with Client(calendar_mcp) as client:
            result = await client.call_tool(
                "respond_to_event",
                {"event_id": "event-1", "response_status": "accepted"},
            )
            return result.structured_content or result.data

    result = asyncio.run(scenario())

    assert result["status"] == "ok"
    assert result["response_status"] == "accepted"
    assert result["event"]["my_response"] == "accepted"
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


class _CreateEvents:
    def insert(self, *, body: dict, **kwargs) -> _CalendarRequest:
        return _CalendarRequest("insert", body=body, **kwargs)


class _FreeBusy:
    def __init__(self, busy: list[dict] | Exception) -> None:
        self._busy = busy

    def query(self, *, body: dict) -> _ExecutedRequest:
        if isinstance(self._busy, Exception):
            raise self._busy
        calendar_id = body["items"][0]["id"]
        return _ExecutedRequest({"calendars": {calendar_id: {"busy": self._busy}}})


class _CreateService:
    def __init__(self, busy: list[dict] | Exception) -> None:
        self._busy = busy

    def events(self) -> _CreateEvents:
        return _CreateEvents()

    def freebusy(self) -> _FreeBusy:
        return _FreeBusy(self._busy)


def _create_event_scenario(
    monkeypatch, busy: list[dict] | Exception, **arguments
) -> tuple[Any, list[dict], list[dict]]:
    inserted: list[dict] = []
    insert_kwargs: list[dict] = []

    async def fake_timezone() -> str:
        return "UTC"

    async def fake_execute(request: _CalendarRequest) -> dict:
        assert request.kind == "insert"
        inserted.append(request.body or {})
        insert_kwargs.append(request.kwargs)
        return {"id": "event-new", **(request.body or {})}

    monkeypatch.setattr(calendar_tools, "build_calendar_service", lambda: _CreateService(busy))
    monkeypatch.setattr(calendar_tools, "resolve_user_timezone", fake_timezone)
    monkeypatch.setattr(calendar_tools, "execute_google_request", fake_execute)

    async def scenario() -> Any:
        async with Client(calendar_mcp) as client:
            return await client.call_tool(
                "create_event",
                {
                    "summary": "Planning",
                    "start_datetime": "2026-07-12T10:00:00",
                    "end_datetime": "2026-07-12T11:00:00",
                    **arguments,
                },
                raise_on_error=False,
            )

    return asyncio.run(scenario()), inserted, insert_kwargs


BUSY = [{"start": "2026-07-12T10:30:00Z", "end": "2026-07-12T11:30:00Z"}]


def test_create_event_time_slot_conflict_is_a_conflict_tool_error(monkeypatch) -> None:
    """An occupied slot is an isError result with code ``conflict``, not a success."""
    result, inserted, _ = _create_event_scenario(monkeypatch, BUSY)

    assert result.is_error is True
    envelope = result.structured_content
    assert envelope["code"] == "conflict"
    assert envelope["retryable"] is False
    assert envelope["details"]["conflicting_events"] == BUSY
    assert envelope["details"]["calendar_id"] == "primary"
    assert "suggested_slots" not in envelope["details"]
    assert envelope["required_action"]["action"] == "choose_a_free_time_slot"
    assert "[code: conflict]" in result.content[0].text
    assert inserted == []


def test_create_event_conflict_carries_suggested_slots_when_asked(monkeypatch) -> None:
    result, inserted, _ = _create_event_scenario(monkeypatch, BUSY, on_conflict="suggest_next_slot")

    assert result.is_error is True
    details = result.structured_content["details"]
    assert details["conflicting_events"] == BUSY
    assert isinstance(details["suggested_slots"], list) and details["suggested_slots"]
    assert all({"start", "end"} <= set(slot) for slot in details["suggested_slots"])
    assert result.structured_content["required_action"]["action"] == "retry_with_one_of_suggested_slots"
    assert inserted == []


def test_create_event_reports_a_failed_conflict_check_instead_of_hiding_it(monkeypatch) -> None:
    result, inserted, _ = _create_event_scenario(monkeypatch, RuntimeError("freebusy down"))

    assert result.is_error is False, result.content
    assert len(inserted) == 1
    payload = result.structured_content
    assert payload["status"] == "ok"
    assert payload["warnings"] == ["Could not check for conflicts: freebusy down"]


def test_create_event_without_conflict_has_no_warnings(monkeypatch) -> None:
    result, inserted, _ = _create_event_scenario(monkeypatch, [])

    assert result.is_error is False
    assert len(inserted) == 1
    assert result.structured_content["warnings"] == []


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


# --- read_events: attachments carry what drive_download_file needs ---------------


def test_read_events_derives_drive_file_id_from_attachment_url(monkeypatch) -> None:
    async def fake_timezone() -> str:
        return "UTC"

    async def fake_execute(request: _CalendarRequest) -> dict:
        return {
            "id": request.event_id,
            "start": {"dateTime": "2026-07-12T10:00:00Z"},
            "end": {"dateTime": "2026-07-12T11:00:00Z"},
            "attachments": [
                {
                    "fileUrl": "https://drive.google.com/file/d/abc_123-XYZ/view?usp=drive_web",
                    "title": "Deck",
                    "mimeType": "application/vnd.google-apps.presentation",
                },
                {"fileUrl": "https://docs.google.com/open?id=doc-456", "title": "Notes"},
                {"fileUrl": "https://example.com/file?id=not-drive", "title": "External"},
            ],
        }

    monkeypatch.setattr(calendar_tools, "build_calendar_service", _CalendarService)
    monkeypatch.setattr(calendar_tools, "resolve_user_timezone", fake_timezone)
    monkeypatch.setattr(calendar_tools, "execute_google_request", fake_execute)

    async def scenario() -> dict:
        async with Client(calendar_mcp) as client:
            result = await client.call_tool("read_events", {"event_ids": ["event-1"]})
            return result.structured_content or result.data

    attachments = asyncio.run(scenario())["events"][0]["attachments"]

    assert attachments[0] == {
        "title": "Deck",
        "file_url": "https://drive.google.com/file/d/abc_123-XYZ/view?usp=drive_web",
        "file_id": "abc_123-XYZ",
        "mime_type": "application/vnd.google-apps.presentation",
    }
    assert attachments[1]["file_id"] == "doc-456"
    # A non-Drive link carries no Drive file ID.
    assert attachments[2]["file_id"] is None


# --- calendar_get_calendar_context ------------------------------------------------


class _CalendarListResource:
    def list(self, **kwargs) -> _CalendarRequest:
        return _CalendarRequest("calendarList.list", **kwargs)


class _CalendarListService:
    def calendarList(self) -> _CalendarListResource:
        return _CalendarListResource()


CALENDAR_PAGES = {
    None: {
        "items": [
            {
                "id": "me@example.com",
                "summary": "me@example.com",
                "primary": True,
                "accessRole": "owner",
                "timeZone": "Europe/Berlin",
                "colorId": "14",
                "etag": "x",
            }
        ],
        "nextPageToken": "page-2",
    },
    "page-2": {
        "items": [
            {
                "id": "team@group.calendar.google.com",
                "summary": "Team",
                "summaryOverride": "My team",
                "description": "Shared team calendar",
                "accessRole": "reader",
                "timeZone": "UTC",
                "hidden": True,
            }
        ],
        "nextSyncToken": "sync",
    },
}


def _calendar_context(monkeypatch, **arguments) -> tuple[dict, list[dict]]:
    list_calls: list[dict] = []

    async def fake_timezone() -> str:
        return "Europe/Berlin"

    async def fake_execute(request: _CalendarRequest) -> dict:
        assert request.kind == "calendarList.list"
        list_calls.append(request.kwargs)
        return CALENDAR_PAGES[request.kwargs.get("pageToken")]

    monkeypatch.setattr(calendar_tools, "build_calendar_service", _CalendarListService)
    monkeypatch.setattr(calendar_tools, "resolve_user_timezone", fake_timezone)
    monkeypatch.setattr(calendar_tools, "execute_google_request", fake_execute)

    async def scenario() -> dict:
        async with Client(calendar_mcp) as client:
            result = await client.call_tool("get_calendar_context", arguments)
            return result.structured_content or result.data

    return asyncio.run(scenario()), list_calls


def test_get_calendar_context_lists_every_calendar_across_pages(monkeypatch) -> None:
    payload, list_calls = _calendar_context(monkeypatch)

    assert [call.get("pageToken") for call in list_calls] == [None, "page-2"]
    assert payload["timezone"] == "Europe/Berlin"
    assert {
        "current_date", "current_time", "day_of_week", "utc_offset", "current_local_time", "current_utc_time",
    } <= set(payload)
    assert payload["calendars"] == [
        {
            "id": "me@example.com",
            "name": "me@example.com",
            "description": None,
            "primary": True,
            "access_role": "owner",
            "time_zone": "Europe/Berlin",
            "hidden": False,
        },
        {
            "id": "team@group.calendar.google.com",
            "name": "My team",
            "description": "Shared team calendar",
            "primary": False,
            "access_role": "reader",
            "time_zone": "UTC",
            "hidden": True,
        },
    ]


def test_get_calendar_context_without_calendars_skips_the_calendar_list(monkeypatch) -> None:
    payload, list_calls = _calendar_context(monkeypatch, include_calendars=False)

    assert list_calls == []
    assert payload["calendars"] is None
    assert payload["timezone"] == "Europe/Berlin"
    assert payload["current_date"]


# --- calendar_create_event / calendar_update_event: Meet links and reminders ------


def test_create_event_add_meet_link_requests_a_meet_conference(monkeypatch) -> None:
    result, inserted, insert_kwargs = _create_event_scenario(monkeypatch, [], add_meet_link=True)

    assert result.is_error is False, result.content
    create_request = inserted[0]["conferenceData"]["createRequest"]
    assert create_request["conferenceSolutionKey"] == {"type": "hangoutsMeet"}
    assert create_request["requestId"]
    assert insert_kwargs[0]["conferenceDataVersion"] == 1


def test_create_event_without_meet_link_sends_no_conference(monkeypatch) -> None:
    _, inserted, insert_kwargs = _create_event_scenario(monkeypatch, [])

    assert "conferenceData" not in inserted[0]
    assert insert_kwargs[0]["conferenceDataVersion"] == 0
    # Omitted reminder_minutes keeps the calendar's default reminders.
    assert "reminders" not in inserted[0]


def test_create_event_reminder_minutes_become_popup_overrides(monkeypatch) -> None:
    _, inserted, _ = _create_event_scenario(monkeypatch, [], reminder_minutes=[60, 10])

    assert inserted[0]["reminders"] == {
        "useDefault": False,
        "overrides": [
            {"method": "popup", "minutes": 10},
            {"method": "popup", "minutes": 60},
        ],
    }


def test_create_event_empty_reminder_minutes_means_no_reminders(monkeypatch) -> None:
    _, inserted, _ = _create_event_scenario(monkeypatch, [], reminder_minutes=[])

    assert inserted[0]["reminders"] == {"useDefault": False, "overrides": []}


def test_create_event_rejects_out_of_range_reminders(monkeypatch) -> None:
    for minutes in ([-1], [40321], [1, 2, 3, 4, 5, 6]):
        result, inserted, _ = _create_event_scenario(monkeypatch, [], reminder_minutes=minutes)
        assert result.is_error is True, minutes
        assert inserted == []


def _update_event(monkeypatch, **arguments) -> tuple[Any, list[_CalendarRequest]]:
    patches: list[_CalendarRequest] = []

    async def fake_timezone() -> str:
        return "UTC"

    async def fake_execute(request: _CalendarRequest) -> dict:
        assert request.kind == "patch"
        patches.append(request)
        return {"id": request.event_id, **(request.body or {})}

    monkeypatch.setattr(calendar_tools, "build_calendar_service", _CalendarService)
    monkeypatch.setattr(calendar_tools, "resolve_user_timezone", fake_timezone)
    monkeypatch.setattr(calendar_tools, "execute_google_request", fake_execute)

    async def scenario() -> Any:
        async with Client(calendar_mcp) as client:
            return await client.call_tool(
                "update_event", {"event_id": "event-1", **arguments}, raise_on_error=False
            )

    return asyncio.run(scenario()), patches


def test_update_event_add_meet_link_and_reminder_minutes(monkeypatch) -> None:
    result, patches = _update_event(monkeypatch, add_meet_link=True, reminder_minutes=[15])

    assert result.is_error is False, result.content
    body = patches[0].body or {}
    assert body["conferenceData"]["createRequest"]["conferenceSolutionKey"] == {"type": "hangoutsMeet"}
    assert patches[0].kwargs["conferenceDataVersion"] == 1
    assert body["reminders"] == {"useDefault": False, "overrides": [{"method": "popup", "minutes": 15}]}


def test_update_event_empty_reminder_minutes_removes_reminders(monkeypatch) -> None:
    _, patches = _update_event(monkeypatch, reminder_minutes=[])

    assert (patches[0].body or {})["reminders"] == {"useDefault": False, "overrides": []}


def test_update_event_leaves_reminders_and_conference_unchanged_by_default(monkeypatch) -> None:
    _, patches = _update_event(monkeypatch, summary="Renamed")

    assert patches[0].body == {"summary": "Renamed"}
    assert patches[0].kwargs["conferenceDataVersion"] == 0


# --- calendar_set_event_attachments -----------------------------------------------

EXISTING_ATTACHMENTS = [
    {"fileUrl": "https://drive.google.com/file/d/keep/view", "fileId": "keep", "title": "Keep"},
    {"fileUrl": "https://drive.google.com/file/d/old/view", "fileId": "old", "title": "Old"},
    {"fileUrl": "https://drive.google.com/file/d/url-only/view", "title": "URL only"},
]


def _set_attachments(
    monkeypatch, *, confirm: bool | None, **arguments
) -> tuple[Any, list[_CalendarRequest], list[tuple[str, str]]]:
    requests: list[_CalendarRequest] = []
    confirmations: list[tuple[str, str]] = []

    async def fake_timezone() -> str:
        return "UTC"

    async def fake_execute(request: _CalendarRequest) -> dict:
        requests.append(request)
        if request.kind == "get":
            return {"id": request.event_id, "attachments": [dict(item) for item in EXISTING_ATTACHMENTS]}
        return {"id": request.event_id, **(request.body or {})}

    async def fake_confirm(ctx, action: str, message: str, **kwargs) -> bool:
        if confirm is None:
            raise AssertionError("this call must not ask for confirmation")
        confirmations.append((action, message))
        return confirm

    monkeypatch.setattr(calendar_tools, "build_calendar_service", _CalendarService)
    monkeypatch.setattr(calendar_tools, "resolve_user_timezone", fake_timezone)
    monkeypatch.setattr(calendar_tools, "execute_google_request", fake_execute)
    # The consent gate around every tool asks through the confirmation adapter.
    monkeypatch.setattr(confirmation, "confirm_destructive_action", fake_confirm)

    async def scenario() -> Any:
        async with Client(calendar_mcp) as client:
            return await client.call_tool(
                "set_event_attachments", {"event_id": "event-1", **arguments}, raise_on_error=False
            )

    return asyncio.run(scenario()), requests, confirmations


def test_set_event_attachments_add_only_keeps_existing_and_dedupes_without_confirming(monkeypatch) -> None:
    result, requests, confirmations = _set_attachments(
        monkeypatch,
        confirm=None,
        add=[
            {"file_url": "https://drive.google.com/file/d/new/view", "title": "New", "file_id": "new"},
            {"file_url": "https://drive.google.com/file/d/keep/view", "title": "Duplicate"},
            {"file_url": "https://drive.google.com/file/d/new/view", "title": "Repeated in add"},
        ],
        send_updates="none",
    )

    assert result.is_error is False, result.content
    assert confirmations == []
    assert [request.kind for request in requests] == ["get", "patch"]
    patch = requests[1]
    assert patch.kwargs["supportsAttachments"] is True
    assert patch.kwargs["sendUpdates"] == "none"
    assert [item["title"] for item in (patch.body or {})["attachments"]] == ["Keep", "Old", "URL only", "New"]
    payload = result.structured_content
    assert payload["status"] == "ok"
    assert payload["added_count"] == 1
    assert payload["removed_count"] == 0
    assert payload["event"]["attachments"][-1]["file_id"] == "new"


def test_set_event_attachments_remove_confirms_then_removes(monkeypatch) -> None:
    result, requests, confirmations = _set_attachments(
        monkeypatch,
        confirm=True,
        remove=[
            {"file_id": "old"},
            # The file_id calendar_read_events derives from a Drive URL also matches.
            {"file_id": "url-only"},
            {"file_url": "https://drive.google.com/file/d/missing/view"},
        ],
    )

    assert result.is_error is False, result.content
    assert confirmations == [
        (
            "remove_event_attachment",
            "Change the attachments of event event-1: remove 3 (old, url-only, "
            "https://drive.google.com/file/d/missing/view), add 0? The files stay in Drive. "
            "Google emails no one (send_updates not set).",
        )
    ]
    assert [item["title"] for item in (requests[1].body or {})["attachments"]] == ["Keep"]
    payload = result.structured_content
    assert payload["status"] == "ok"
    assert payload["removed_count"] == 2
    assert payload["added_count"] == 0


def test_set_event_attachments_remove_by_url(monkeypatch) -> None:
    result, requests, _ = _set_attachments(
        monkeypatch,
        confirm=True,
        remove=[{"file_url": "https://drive.google.com/file/d/keep/view"}],
    )

    assert result.structured_content["removed_count"] == 1
    assert [item["title"] for item in (requests[1].body or {})["attachments"]] == ["Old", "URL only"]


def test_set_event_attachments_declined_changes_nothing_not_even_the_additions(monkeypatch) -> None:
    result, requests, confirmations = _set_attachments(
        monkeypatch,
        confirm=False,
        add=[{"file_url": "https://drive.google.com/file/d/new/view"}],
        remove=[{"file_id": "old"}],
    )

    assert result.is_error is False, result.content
    assert len(confirmations) == 1
    assert requests == []
    assert result.structured_content == {
        "status": "cancelled",
        "action": "remove_event_attachment",
        "reason": "The user declined; nothing was changed.",
    }


def test_set_event_attachments_add_and_remove_in_one_patch(monkeypatch) -> None:
    result, requests, confirmations = _set_attachments(
        monkeypatch,
        confirm=True,
        add=[{"file_url": "https://drive.google.com/file/d/new/view", "title": "New"}],
        remove=[{"file_id": "old"}],
    )

    assert len(confirmations) == 1
    assert [request.kind for request in requests] == ["get", "patch"]
    assert [item["title"] for item in (requests[1].body or {})["attachments"]] == ["Keep", "URL only", "New"]
    payload = result.structured_content
    assert (payload["added_count"], payload["removed_count"]) == (1, 1)


def test_set_event_attachments_remove_fails_closed_without_elicitation(monkeypatch) -> None:
    """Through the real confirmation adapter: a client that cannot confirm changes nothing."""
    requests: list[_CalendarRequest] = []

    async def fake_execute(request: _CalendarRequest) -> dict:
        requests.append(request)
        return {}

    monkeypatch.setattr(calendar_tools, "build_calendar_service", _CalendarService)
    monkeypatch.setattr(calendar_tools, "execute_google_request", fake_execute)

    async def scenario() -> Any:
        async with Client(calendar_mcp) as client:
            return await client.call_tool(
                "set_event_attachments",
                {"event_id": "event-1", "remove": [{"file_id": "old"}]},
                raise_on_error=False,
            )

    result = asyncio.run(scenario())

    assert result.is_error is True
    assert result.structured_content["code"] == "confirmation_required"
    assert requests == []


def test_set_event_attachments_requires_a_change_and_real_selectors(monkeypatch) -> None:
    for arguments in ({}, {"add": [], "remove": []}, {"remove": [{}]}):
        result, requests, confirmations = _set_attachments(monkeypatch, confirm=None, **arguments)
        assert result.is_error is True, arguments
        assert requests == []
        assert confirmations == []


def test_search_events_pages_with_the_next_page_token_it_returns(monkeypatch) -> None:
    sent: list[dict[str, Any]] = []

    class _Events:
        def list(self, **kwargs: Any) -> dict[str, Any]:
            sent.append(kwargs)
            return kwargs

    class _Service:
        def events(self) -> _Events:
            return _Events()

    async def fake_timezone() -> str:
        return "UTC"

    async def fake_execute(kwargs: dict[str, Any]) -> dict:
        page = kwargs.get("pageToken")
        return {"items": [], "nextPageToken": None if page else "page-2"}

    monkeypatch.setattr(calendar_tools, "build_calendar_service", _Service)
    monkeypatch.setattr(calendar_tools, "resolve_user_timezone", fake_timezone)
    monkeypatch.setattr(calendar_tools, "execute_google_request", fake_execute)
    window = {"time_min": "2026-07-01T00:00:00Z", "time_max": "2026-08-01T00:00:00Z"}

    async def scenario() -> list[Any]:
        async with Client(calendar_mcp) as client:
            first = await client.call_tool("search_events", window)
            second = await client.call_tool(
                "search_events", {**window, "page_token": first.structured_content["next_page_token"]}
            )
            return [first.structured_content, second.structured_content]

    first, second = asyncio.run(scenario())

    assert first["next_page_token"] == "page-2" and second["next_page_token"] is None
    assert "pageToken" not in sent[0] and sent[1]["pageToken"] == "page-2"
    assert sent[1]["timeMin"] == window["time_min"] and sent[1]["timeMax"] == window["time_max"]
