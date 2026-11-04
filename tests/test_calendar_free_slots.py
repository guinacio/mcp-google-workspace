import asyncio
from datetime import datetime
from typing import Any

import pytz
import pytest
from fastmcp import Client
from pydantic import ValidationError

import mcp_google_workspace.calendar.tools as calendar_tools
from mcp_google_workspace.calendar.schemas import FindFreeTimeRequest
from mcp_google_workspace.calendar.server import calendar_mcp
from mcp_google_workspace.calendar.tools import (
    _apply_working_hours,
    _build_slot_candidates,
    _merge_time_ranges,
)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(pytz.UTC)


def test_merge_time_ranges_merges_overlap_and_touching() -> None:
    ranges = [
        (_dt("2026-03-01T10:00:00+00:00"), _dt("2026-03-01T11:00:00+00:00")),
        (_dt("2026-03-01T10:30:00+00:00"), _dt("2026-03-01T12:00:00+00:00")),
        (_dt("2026-03-01T12:00:00+00:00"), _dt("2026-03-01T12:30:00+00:00")),
    ]

    merged = _merge_time_ranges(ranges)

    assert len(merged) == 1
    assert merged[0][0].isoformat() == "2026-03-01T10:00:00+00:00"
    assert merged[0][1].isoformat() == "2026-03-01T12:30:00+00:00"


def test_build_slot_candidates_respects_duration_step_and_limit() -> None:
    free_ranges = [
        (_dt("2026-03-01T09:00:00+00:00"), _dt("2026-03-01T11:00:00+00:00")),
    ]

    slots = _build_slot_candidates(
        free_ranges=free_ranges,
        slot_duration_minutes=60,
        granularity_minutes=30,
        max_results=2,
    )

    assert len(slots) == 2
    assert slots[0] == {
        "start": "2026-03-01T09:00:00+00:00",
        "end": "2026-03-01T10:00:00+00:00",
    }
    assert slots[1] == {
        "start": "2026-03-01T09:30:00+00:00",
        "end": "2026-03-01T10:30:00+00:00",
    }


def test_apply_working_hours_uses_defaults_window() -> None:
    free_ranges = [
        (_dt("2026-03-01T06:00:00+00:00"), _dt("2026-03-01T20:00:00+00:00")),
    ]

    clamped = _apply_working_hours(
        free_ranges=free_ranges,
        working_hours_start="08:00",
        working_hours_end="17:00",
    )

    assert len(clamped) == 1
    assert clamped[0][0].isoformat() == "2026-03-01T08:00:00+00:00"
    assert clamped[0][1].isoformat() == "2026-03-01T17:00:00+00:00"


def test_apply_working_hours_allows_custom_window() -> None:
    free_ranges = [
        (_dt("2026-03-01T06:00:00+00:00"), _dt("2026-03-01T20:00:00+00:00")),
    ]

    clamped = _apply_working_hours(
        free_ranges=free_ranges,
        working_hours_start="09:30",
        working_hours_end="18:30",
    )

    assert len(clamped) == 1
    assert clamped[0][0].isoformat() == "2026-03-01T09:30:00+00:00"
    assert clamped[0][1].isoformat() == "2026-03-01T18:30:00+00:00"


@pytest.mark.parametrize(
    "participants",
    [
        '["rodrigo@example.com", "bruno@example.com"]',
        "rodrigo@example.com, bruno@example.com",
    ],
)
def test_find_free_time_rejects_string_participants(participants: str) -> None:
    with pytest.raises(ValidationError):
        FindFreeTimeRequest(
            participants=participants,  # type: ignore[arg-type]
            time_min="2026-03-02T00:00:00Z",
            time_max="2026-03-03T00:00:00Z",
        )


def test_find_free_time_request_bounds_participants_to_one_through_fifty() -> None:
    for participants in ([], [f"person{index}@example.com" for index in range(51)]):
        with pytest.raises(ValidationError):
            FindFreeTimeRequest(
                participants=participants,
                time_min="2026-03-02T00:00:00Z",
                time_max="2026-03-03T00:00:00Z",
            )


def test_find_free_time_request_uses_canonical_duration_field() -> None:
    request = FindFreeTimeRequest(
        participants=["primary"],
        time_min="2026-03-02T00:00:00Z",
        time_max="2026-03-03T00:00:00Z",
        slot_duration_minutes=45,
    )

    assert request.slot_duration_minutes == 45


# --- calendar_find_free_time over a fake freebusy API ---------------------------


class _FreeBusyQuery:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body


class _FreeBusyResource:
    def query(self, *, body: dict[str, Any]) -> _FreeBusyQuery:
        return _FreeBusyQuery(body)


class _FreeBusyService:
    def freebusy(self) -> _FreeBusyResource:
        return _FreeBusyResource()


def _find_free_time(
    monkeypatch, calendars: dict[str, Any], **arguments: Any
) -> tuple[Any, list[dict[str, Any]]]:
    queries: list[dict[str, Any]] = []

    async def fake_timezone() -> str:
        return "UTC"

    async def fake_execute(request: _FreeBusyQuery) -> dict[str, Any]:
        queries.append(request.body)
        return {
            "timeMin": request.body["timeMin"],
            "timeMax": request.body["timeMax"],
            "calendars": calendars,
        }

    monkeypatch.setattr(calendar_tools, "build_calendar_service", _FreeBusyService)
    monkeypatch.setattr(calendar_tools, "resolve_user_timezone", fake_timezone)
    monkeypatch.setattr(calendar_tools, "execute_google_request", fake_execute)

    async def scenario() -> Any:
        async with Client(calendar_mcp) as client:
            return await client.call_tool("find_free_time", arguments, raise_on_error=False)

    return asyncio.run(scenario()), queries


def test_find_free_time_checks_one_chosen_slot_per_calendar(monkeypatch) -> None:
    """The old check_time_availability use: the slot is the window; read all_free."""
    result, queries = _find_free_time(
        monkeypatch,
        {
            "primary": {"busy": []},
            "bob@example.com": {
                "busy": [{"start": "2026-03-02T10:15:00Z", "end": "2026-03-02T10:45:00Z"}]
            },
        },
        participants=["primary", "bob@example.com"],
        time_min="2026-03-02T10:00:00",
        time_max="2026-03-02T10:30:00",
    )

    assert result.is_error is False, result.content
    payload = result.structured_content
    # One freebusy query, items in request order, result zone sent to Google.
    assert len(queries) == 1
    assert queries[0]["items"] == [{"id": "primary"}, {"id": "bob@example.com"}]
    assert queries[0]["timeZone"] == "UTC"
    assert queries[0]["timeMin"] == "2026-03-02T10:00:00+00:00"
    assert payload["all_free"] is False
    assert payload["calendars"] == [
        {"id": "primary", "free": True, "busy": [], "errors": []},
        {
            "id": "bob@example.com",
            "free": False,
            "busy": [{"start": "2026-03-02T10:15:00+00:00", "end": "2026-03-02T10:45:00+00:00"}],
            "errors": [],
        },
    ]
    assert payload["suggested_slots"] == []
    assert payload["time_min"] == "2026-03-02T10:00:00+00:00"
    assert payload["time_max"] == "2026-03-02T10:30:00+00:00"
    assert payload["timezone"] == "UTC"


def test_find_free_time_all_free_when_every_calendar_is_free(monkeypatch) -> None:
    result, _ = _find_free_time(
        monkeypatch,
        {"primary": {"busy": []}, "bob@example.com": {"busy": []}},
        participants=["primary", "bob@example.com"],
        time_min="2026-03-02T10:00:00Z",
        time_max="2026-03-02T10:30:00Z",
    )

    payload = result.structured_content
    assert payload["all_free"] is True
    assert [entry["free"] for entry in payload["calendars"]] == [True, True]
    assert payload["suggested_slots"] == [
        {"start": "2026-03-02T10:00:00+00:00", "end": "2026-03-02T10:30:00+00:00"}
    ]


def test_find_free_time_suggests_common_slots_within_working_hours(monkeypatch) -> None:
    """The old find_common_free_slots use: a wide window; read suggested_slots."""
    result, _ = _find_free_time(
        monkeypatch,
        {
            "ana@example.com": {
                "busy": [{"start": "2026-03-02T08:00:00Z", "end": "2026-03-02T09:00:00Z"}]
            },
            "bob@example.com": {
                "busy": [{"start": "2026-03-02T09:30:00Z", "end": "2026-03-02T16:30:00Z"}]
            },
        },
        participants=["ana@example.com", "bob@example.com"],
        time_min="2026-03-02",
        time_max="2026-03-03",
        slot_duration_minutes=30,
        granularity_minutes=15,
        max_results=5,
    )

    payload = result.structured_content
    assert payload["all_free"] is False
    assert payload["suggested_slots"] == [
        {"start": "2026-03-02T09:00:00+00:00", "end": "2026-03-02T09:30:00+00:00"},
        {"start": "2026-03-02T16:30:00+00:00", "end": "2026-03-02T17:00:00+00:00"},
    ]
    assert payload["total_suggestions"] == payload["count"] == 2
    assert payload["slot_duration_minutes"] == 30
    assert payload["granularity_minutes"] == 15
    assert payload["working_hours_start"] == "08:00"
    assert payload["working_hours_end"] == "17:00"


def test_find_free_time_reports_unreadable_calendars(monkeypatch) -> None:
    """An unreadable calendar is free=null with Google's reason; all_free is not true,
    and slot suggestions treat it as free (as find_common_free_slots did)."""
    result, _ = _find_free_time(
        monkeypatch,
        {
            "primary": {"busy": []},
            "ghost@example.com": {"busy": [], "errors": [{"domain": "global", "reason": "notFound"}]},
        },
        participants=["primary", "ghost@example.com"],
        time_min="2026-03-02T09:00:00Z",
        time_max="2026-03-02T10:00:00Z",
        slot_duration_minutes=60,
    )

    payload = result.structured_content
    assert payload["calendars"][1] == {
        "id": "ghost@example.com",
        "free": None,
        "busy": [],
        "errors": ["notFound"],
    }
    assert payload["all_free"] is False
    assert payload["suggested_slots"] == [
        {"start": "2026-03-02T09:00:00+00:00", "end": "2026-03-02T10:00:00+00:00"}
    ]


def test_find_free_time_returns_times_and_working_hours_in_time_zone(monkeypatch) -> None:
    result, queries = _find_free_time(
        monkeypatch,
        {"primary": {"busy": [{"start": "2026-03-02T08:00:00Z", "end": "2026-03-02T09:00:00Z"}]}},
        participants=["primary"],
        time_min="2026-03-02T00:00:00Z",
        time_max="2026-03-03T00:00:00Z",
        time_zone="Europe/Berlin",
        slot_duration_minutes=60,
        granularity_minutes=60,
        max_results=2,
    )

    payload = result.structured_content
    assert queries[0]["timeZone"] == "Europe/Berlin"
    assert payload["timezone"] == "Europe/Berlin"
    assert payload["time_min"] == "2026-03-02T01:00:00+01:00"
    assert payload["calendars"][0]["busy"] == [
        {"start": "2026-03-02T09:00:00+01:00", "end": "2026-03-02T10:00:00+01:00"}
    ]
    # 08:00 Berlin is free, 09:00-10:00 Berlin is busy.
    assert payload["suggested_slots"] == [
        {"start": "2026-03-02T08:00:00+01:00", "end": "2026-03-02T09:00:00+01:00"},
        {"start": "2026-03-02T10:00:00+01:00", "end": "2026-03-02T11:00:00+01:00"},
    ]


def test_find_free_time_rejects_an_empty_window(monkeypatch) -> None:
    result, queries = _find_free_time(
        monkeypatch,
        {},
        participants=["primary"],
        time_min="2026-03-02T10:00:00Z",
        time_max="2026-03-02T10:00:00Z",
    )

    assert result.is_error is True
    assert result.structured_content["code"] == "invalid_input"
    assert queries == []


def test_working_hours_are_local_time_whatever_the_window_offset(monkeypatch) -> None:
    """A UTC window, and a multi-day window across a DST change, still get 08:00 local each day."""
    result, _ = _find_free_time(
        monkeypatch,
        {"primary": {"busy": []}},
        participants=["primary"],
        # America/New_York leaves EST (-05:00) for EDT (-04:00) on 2026-03-08.
        time_min="2026-03-07T00:00:00Z",
        time_max="2026-03-11T00:00:00Z",
        time_zone="America/New_York",
        slot_duration_minutes=480,
        granularity_minutes=240,
        max_results=100,
    )

    assert result.is_error is False, result.content
    starts = [slot["start"] for slot in result.structured_content["suggested_slots"]]
    assert starts == [
        "2026-03-07T08:00:00-05:00",
        "2026-03-08T08:00:00-04:00",
        "2026-03-09T08:00:00-04:00",
        "2026-03-10T08:00:00-04:00",
    ]
