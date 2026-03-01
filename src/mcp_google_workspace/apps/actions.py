"""App action flows for meeting operations and scheduling."""

from __future__ import annotations

from datetime import datetime
from threading import Lock
from typing import Any

from ..auth import build_calendar_service
from ..calendar.tools import (
    _apply_working_hours,
    _build_slot_candidates,
    _merge_time_ranges,
    _parse_rfc3339_datetime,
    _validate_and_fix_datetime,
)
from .schemas import (
    AppActionResult,
    CancelMeetingRequest,
    CreateMeetingFromSlotRequest,
    FindMeetingSlotsRequest,
    RescheduleMeetingRequest,
)

_IDEMPOTENCY_RESULTS: dict[tuple[str, str], AppActionResult] = {}
_LOCK = Lock()


def _from_cache(session_id: str, key: str) -> AppActionResult | None:
    with _LOCK:
        return _IDEMPOTENCY_RESULTS.get((session_id, key))


def _to_cache(session_id: str, key: str, result: AppActionResult) -> AppActionResult:
    with _LOCK:
        _IDEMPOTENCY_RESULTS[(session_id, key)] = result
        return result


def _compute_open_ranges(
    *,
    participants: list[str],
    time_min: str,
    time_max: str,
    time_zone: str,
    working_hours_start: str,
    working_hours_end: str,
) -> tuple[list[tuple[datetime, datetime]], dict[str, Any]]:
    service = build_calendar_service()
    fixed_min = _validate_and_fix_datetime(time_min, time_zone)
    fixed_max = _validate_and_fix_datetime(time_max, time_zone)
    if not fixed_min or not fixed_max:
        raise ValueError("time_min and time_max are required")

    window_start = _parse_rfc3339_datetime(fixed_min)
    window_end = _parse_rfc3339_datetime(fixed_max)
    if window_end <= window_start:
        raise ValueError("time_max must be greater than time_min")

    freebusy_result = service.freebusy().query(
        body={
            "timeMin": fixed_min,
            "timeMax": fixed_max,
            "timeZone": time_zone,
            "items": [{"id": participant} for participant in participants],
        }
    ).execute()

    calendars = freebusy_result.get("calendars", {})
    all_busy_ranges: list[tuple[datetime, datetime]] = []
    calendar_errors: dict[str, Any] = {}
    for participant in participants:
        details = calendars.get(participant, {})
        if details.get("errors"):
            calendar_errors[participant] = details["errors"]
        for busy in details.get("busy", []):
            start_raw = busy.get("start")
            end_raw = busy.get("end")
            if not start_raw or not end_raw:
                continue
            busy_start = _parse_rfc3339_datetime(start_raw)
            busy_end = _parse_rfc3339_datetime(end_raw)
            if busy_end <= busy_start:
                continue
            all_busy_ranges.append((max(window_start, busy_start), min(window_end, busy_end)))

    merged_busy = _merge_time_ranges(all_busy_ranges)
    free_ranges: list[tuple[datetime, datetime]] = []
    cursor = window_start
    for busy_start, busy_end in merged_busy:
        if busy_end <= cursor:
            continue
        if busy_start > cursor:
            free_ranges.append((cursor, busy_start))
        cursor = max(cursor, busy_end)
    if cursor < window_end:
        free_ranges.append((cursor, window_end))

    return (
        _apply_working_hours(free_ranges, working_hours_start, working_hours_end),
        calendar_errors,
    )


def find_meeting_slots(request: FindMeetingSlotsRequest) -> dict[str, Any]:
    participants = request.participants or ["primary"]
    free_ranges, calendar_errors = _compute_open_ranges(
        participants=participants,
        time_min=request.time_min,
        time_max=request.time_max,
        time_zone=request.time_zone,
        working_hours_start=request.working_hours_start,
        working_hours_end=request.working_hours_end,
    )
    suggestions = _build_slot_candidates(
        free_ranges,
        request.slot_duration_minutes,
        request.granularity_minutes,
        request.max_results,
    )
    return {
        "participants": participants,
        "time_min": request.time_min,
        "time_max": request.time_max,
        "slot_duration_minutes": request.slot_duration_minutes,
        "total_suggestions": len(suggestions),
        "suggested_slots": suggestions,
        "calendar_errors": calendar_errors,
    }


def create_meeting_from_slot(session_id: str, request: CreateMeetingFromSlotRequest) -> AppActionResult:
    cached = _from_cache(session_id, request.idempotency_key)
    if cached is not None:
        return cached
    service = build_calendar_service()
    start = _validate_and_fix_datetime(request.start, request.timezone)
    end = _validate_and_fix_datetime(request.end, request.timezone)
    body: dict[str, Any] = {
        "summary": request.title,
        "start": {"dateTime": start, "timeZone": request.timezone},
        "end": {"dateTime": end, "timeZone": request.timezone},
    }
    if request.description:
        body["description"] = request.description
    if request.attendees:
        body["attendees"] = [{"email": email} for email in request.attendees]
    if request.create_conference:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": request.idempotency_key,
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
    event = (
        service.events()
        .insert(
            calendarId=request.calendar_id,
            body=body,
            conferenceDataVersion=1 if request.create_conference else 0,
            sendUpdates="all",
        )
        .execute()
    )
    return _to_cache(
        session_id,
        request.idempotency_key,
        AppActionResult(
            status="ok",
            message="Meeting created.",
            event_id=event.get("id"),
            payload={"event": event},
        ),
    )


def reschedule_meeting(session_id: str, request: RescheduleMeetingRequest) -> AppActionResult:
    cached = _from_cache(session_id, request.idempotency_key)
    if cached is not None:
        return cached
    service = build_calendar_service()
    start = _validate_and_fix_datetime(request.start, request.timezone)
    end = _validate_and_fix_datetime(request.end, request.timezone)
    event = (
        service.events()
        .patch(
            calendarId=request.calendar_id,
            eventId=request.event_id,
            body={
                "start": {"dateTime": start, "timeZone": request.timezone},
                "end": {"dateTime": end, "timeZone": request.timezone},
            },
            sendUpdates="all",
        )
        .execute()
    )
    return _to_cache(
        session_id,
        request.idempotency_key,
        AppActionResult(
            status="ok",
            message="Meeting rescheduled.",
            event_id=event.get("id"),
            payload={"event": event},
        ),
    )


def cancel_meeting(session_id: str, request: CancelMeetingRequest) -> AppActionResult:
    cached = _from_cache(session_id, request.idempotency_key)
    if cached is not None:
        return cached
    if not request.confirm:
        return AppActionResult(status="cancelled", message="Cancellation requires confirm=true.")
    service = build_calendar_service()
    service.events().delete(
        calendarId=request.calendar_id,
        eventId=request.event_id,
        sendUpdates=request.send_updates,
    ).execute()
    return _to_cache(
        session_id,
        request.idempotency_key,
        AppActionResult(
            status="ok",
            message="Meeting cancelled.",
            event_id=request.event_id,
        ),
    )
