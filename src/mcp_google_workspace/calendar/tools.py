"""FastMCP calendar tools migrated from raw MCP SDK style."""

from __future__ import annotations

import re
from hashlib import sha256
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

import pytz
from fastmcp import Context, FastMCP
from googleapiclient.errors import HttpError

from ..auth import build_calendar_service, build_drive_service
from ..common.async_ops import (
    confirm_destructive_action,
    execute_google_request,
    require_elicitation_context,
    run_blocking,
)
from ..common.downloads import stream_google_download
from .presentation import event_envelope
from ..common.timezone import resolve_user_timezone, user_now
from .schemas import (
    AddEventAttachmentRequest,
    CreateEventRequest,
    DeleteEventRequest,
    DownloadEventAttachmentRequest,
    EventAttachmentInput,
    EventAttendeeInput,
    CalendarIdInput,
    FindCommonFreeSlotsRequest,
    FreeBusyRequest,
    ReadEventsRequest,
    RespondToEventRequest,
    ListEventsRequest,
    RemoveEventAttachmentRequest,
    UpdateEventRequest,
)


def _validate_and_fix_datetime(dt_string: str | None, timezone_name: str) -> str | None:
    if not dt_string:
        return dt_string
    if dt_string.endswith("Z") or "+" in dt_string[-6:]:
        return dt_string
    if "T" in dt_string and len(dt_string) == 19:
        dt = datetime.fromisoformat(dt_string)
        tz = pytz.timezone(timezone_name)
        return tz.localize(dt).isoformat()
    if len(dt_string) == 10:
        dt = datetime.fromisoformat(f"{dt_string}T00:00:00")
        tz = pytz.timezone(timezone_name)
        return tz.localize(dt).isoformat()
    return dt_string



GOOGLE_EXPORT_MIME_DEFAULTS = {
    "application/vnd.google-apps.document": "application/pdf",
    "application/vnd.google-apps.spreadsheet": "application/pdf",
    "application/vnd.google-apps.presentation": "application/pdf",
    "application/vnd.google-apps.drawing": "image/png",
}


def _extract_drive_file_id(file_url: str | None) -> str | None:
    if not file_url:
        return None
    # Common Drive URL forms: /d/{id}/..., ?id={id}
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", file_url)
    if match:
        return match.group(1)
    match = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", file_url)
    if match:
        return match.group(1)
    return None


def _idempotent_event_id(key: str) -> str:
    """Build a valid deterministic Google Calendar event ID from an opaque key."""
    return f"mcp{sha256(key.encode('utf-8')).hexdigest()}"


def _check_time_slot_conflicts(
    service: Any,
    calendar_id: str,
    start_time: str,
    end_time: str,
    *,
    exclude_event_id: str | None = None,
) -> dict[str, Any]:
    try:
        if exclude_event_id is not None:
            result = service.events().list(
                calendarId=calendar_id,
                timeMin=start_time,
                timeMax=end_time,
                singleEvents=True,
            ).execute()
            conflicts = [
                {
                    "id": event.get("id"),
                    "summary": event.get("summary"),
                    "start": event.get("start"),
                    "end": event.get("end"),
                }
                for event in result.get("items", [])
                if event.get("id") != exclude_event_id
                and event.get("recurringEventId") != exclude_event_id
                and event.get("status") != "cancelled"
                and event.get("transparency") != "transparent"
            ]
            return {
                "has_conflicts": bool(conflicts),
                "conflicts": conflicts,
                "error": None,
            }
        result = service.freebusy().query(
            body={
                "timeMin": start_time,
                "timeMax": end_time,
                "items": [{"id": calendar_id}],
            }
        ).execute()
        busy_slots = result.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        return {"has_conflicts": len(busy_slots) > 0, "conflicts": busy_slots, "error": None}
    except Exception as exc:  # pragma: no cover - defensive API error handling
        return {
            "has_conflicts": False,
            "conflicts": [],
            "error": f"Could not check for conflicts: {exc}",
        }


def _resolve_relative_range(
    range_preset: str | None,
    timezone_name: str,
) -> tuple[str | None, str | None]:
    if not range_preset:
        return None, None
    now = user_now(timezone_name)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if range_preset == "today":
        start, end = start_of_day, start_of_day + timedelta(days=1)
    elif range_preset == "tomorrow":
        start = start_of_day + timedelta(days=1)
        end = start + timedelta(days=1)
    elif range_preset == "this_week":
        week_start = start_of_day - timedelta(days=start_of_day.weekday())
        start, end = week_start, week_start + timedelta(days=7)
    elif range_preset == "next_7_days":
        start, end = now, now + timedelta(days=7)
    else:  # pragma: no cover - schema restricts this value
        raise ValueError(f"Unsupported range_preset: {range_preset}")
    return start.isoformat(), end.isoformat()


def _suggest_next_available_slots(
    service: Any,
    calendar_id: str,
    requested_start: str,
    requested_end: str,
    *,
    max_results: int = 5,
    granularity_minutes: int = 15,
    horizon_hours: int = 72,
) -> list[dict[str, str]]:
    desired_start = _parse_rfc3339_datetime(requested_start)
    desired_end = _parse_rfc3339_datetime(requested_end)
    if desired_end <= desired_start:
        return []
    duration = desired_end - desired_start
    search_end = desired_start + timedelta(hours=horizon_hours)
    freebusy = service.freebusy().query(
        body={
            "timeMin": desired_start.isoformat(),
            "timeMax": search_end.isoformat(),
            "items": [{"id": calendar_id}],
        }
    ).execute()
    busy_ranges: list[tuple[datetime, datetime]] = []
    for busy in freebusy.get("calendars", {}).get(calendar_id, {}).get("busy", []):
        start_raw = busy.get("start")
        end_raw = busy.get("end")
        if not start_raw or not end_raw:
            continue
        busy_start = _parse_rfc3339_datetime(start_raw)
        busy_end = _parse_rfc3339_datetime(end_raw)
        if busy_end <= busy_start:
            continue
        busy_ranges.append((busy_start, busy_end))
    merged_busy = _merge_time_ranges(busy_ranges)
    free_ranges: list[tuple[datetime, datetime]] = []
    cursor = desired_start
    for busy_start, busy_end in merged_busy:
        if busy_end <= cursor:
            continue
        if busy_start > cursor:
            free_ranges.append((cursor, busy_start))
        cursor = max(cursor, busy_end)
    if cursor < search_end:
        free_ranges.append((cursor, search_end))
    slot_minutes = max(int(duration.total_seconds() // 60), 1)
    return _build_slot_candidates(
        free_ranges=free_ranges,
        slot_duration_minutes=slot_minutes,
        granularity_minutes=granularity_minutes,
        max_results=max_results,
    )


def _parse_rfc3339_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return pytz.UTC.localize(parsed)
    return parsed


def _merge_time_ranges(ranges: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda x: x[0])
    merged: list[tuple[datetime, datetime]] = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _build_slot_candidates(
    free_ranges: list[tuple[datetime, datetime]],
    slot_duration_minutes: int,
    granularity_minutes: int,
    max_results: int,
) -> list[dict[str, str]]:
    slot_delta = timedelta(minutes=slot_duration_minutes)
    step_delta = timedelta(minutes=granularity_minutes)
    suggestions: list[dict[str, str]] = []
    for free_start, free_end in free_ranges:
        cursor = free_start
        while cursor + slot_delta <= free_end:
            suggestions.append(
                {
                    "start": cursor.isoformat(),
                    "end": (cursor + slot_delta).isoformat(),
                }
            )
            if len(suggestions) >= max_results:
                return suggestions
            cursor += step_delta
    return suggestions


def _parse_hhmm(value: str) -> tuple[int, int]:
    hours, minutes = value.split(":", 1)
    return int(hours), int(minutes)


def _apply_working_hours(
    free_ranges: list[tuple[datetime, datetime]],
    working_hours_start: str,
    working_hours_end: str,
) -> list[tuple[datetime, datetime]]:
    if not free_ranges:
        return []
    start_hour, start_minute = _parse_hhmm(working_hours_start)
    end_hour, end_minute = _parse_hhmm(working_hours_end)
    start_total = start_hour * 60 + start_minute
    end_total = end_hour * 60 + end_minute
    if end_total <= start_total:
        raise ValueError("working_hours_end must be later than working_hours_start.")

    clamped: list[tuple[datetime, datetime]] = []
    for free_start, free_end in free_ranges:
        current_day = free_start.date()
        last_day = free_end.date()
        while current_day <= last_day:
            day_start = free_start.replace(
                year=current_day.year,
                month=current_day.month,
                day=current_day.day,
                hour=start_hour,
                minute=start_minute,
                second=0,
                microsecond=0,
            )
            day_end = free_start.replace(
                year=current_day.year,
                month=current_day.month,
                day=current_day.day,
                hour=end_hour,
                minute=end_minute,
                second=0,
                microsecond=0,
            )
            interval_start = max(free_start, day_start)
            interval_end = min(free_end, day_end)
            if interval_end > interval_start:
                clamped.append((interval_start, interval_end))
            current_day = current_day + timedelta(days=1)
    return clamped


def register_tools(server: FastMCP) -> None:
    @server.tool(name="search_events")
    async def search_events(
        calendar_id: str = "primary",
        time_min: str | None = None,
        time_max: str | None = None,
        max_results: int = 25,
        single_events: bool = True,
        order_by: str = "startTime",
        range_preset: Literal["today", "tomorrow", "this_week", "next_7_days"] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List calendar events for a time window.

        Input must be an object shaped like:
        {"calendar_id":"primary","time_min":"2026-03-01T00:00:00Z","time_max":"2026-03-08T00:00:00Z"}
        """
        request = ListEventsRequest(
            calendar_id=calendar_id,
            time_min=time_min,
            time_max=time_max,
            max_results=max_results,
            single_events=single_events,
            order_by=order_by,
            range_preset=range_preset,
        )
        service = build_calendar_service()
        if ctx is not None:
            await ctx.info(f"Listing events for calendar {request.calendar_id}.")
        user_timezone = await resolve_user_timezone()
        preset_min, preset_max = _resolve_relative_range(request.range_preset, user_timezone)
        effective_time_min = request.time_min or preset_min
        effective_time_max = request.time_max or preset_max
        list_kwargs: dict[str, Any] = {
            "calendarId": request.calendar_id,
            "timeMin": effective_time_min,
            "timeMax": effective_time_max,
            "maxResults": request.max_results,
            "singleEvents": request.single_events,
        }
        # Google Calendar only supports orderBy=startTime when singleEvents=True.
        if request.order_by and (request.single_events or request.order_by == "updated"):
            list_kwargs["orderBy"] = request.order_by
        result = await execute_google_request(
            service.events()
            .list(**list_kwargs)
        )
        return {
            "events": [
                event_envelope(event, account_timezone=user_timezone)
                for event in result.get("items", [])
            ],
            "next_page_token": result.get("nextPageToken"),
            "count": len(result.get("items", [])),
            "range_preset": request.range_preset,
            "effective_time_min": effective_time_min,
            "effective_time_max": effective_time_max,
        }

    @server.tool(name="read_events")
    async def read_events(
        event_ids: list[str],
        calendar_id: str = "primary",
        time_zone: str | None = None,
        max_attendees: int | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read one to 100 events with full scheduling and attachment details."""
        request = ReadEventsRequest(
            calendar_id=calendar_id,
            event_ids=event_ids,
            time_zone=time_zone,
            max_attendees=max_attendees,
        )
        service = build_calendar_service()
        effective_time_zone = request.time_zone or await resolve_user_timezone()
        events: list[dict[str, Any]] = []
        missing_event_ids: list[str] = []
        for index, event_id in enumerate(request.event_ids, start=1):
            try:
                event = await execute_google_request(
                    service.events().get(
                        calendarId=request.calendar_id,
                        eventId=event_id,
                        timeZone=effective_time_zone,
                        maxAttendees=request.max_attendees,
                    )
                )
            except HttpError as exc:
                if getattr(exc.resp, "status", None) != 404:
                    raise
                missing_event_ids.append(event_id)
                continue
            detail = event_envelope(event, account_timezone=effective_time_zone)
            detail.update(
                {
                    "description": event.get("description"),
                    "attendees": event.get("attendees", []),
                    "attachments": event.get("attachments", []),
                    "recurrence": event.get("recurrence", []),
                    "visibility": event.get("visibility"),
                    "transparency": event.get("transparency"),
                }
            )
            events.append(detail)
            if ctx is not None:
                await ctx.report_progress(index, len(request.event_ids), "Events loaded")
        return {
            "events": events,
            "missing_count": len(missing_event_ids),
            "missing_event_ids": missing_event_ids,
            "calendar_id": request.calendar_id,
            "account_timezone": effective_time_zone,
        }

    @server.tool(name="get_calendar_digest")
    async def get_calendar_digest(
        days: Annotated[int, "Number of days ahead from now to include upcoming events for."] = 7,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Return upcoming events and those needing the user's RSVP in one call."""
        service = build_calendar_service()
        user_timezone = await resolve_user_timezone()
        now = user_now(user_timezone)
        result = await execute_google_request(
            service.events().list(
                calendarId="primary",
                timeMin=now.isoformat(),
                timeMax=(now + timedelta(days=days)).isoformat(),
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
        )
        events = [
            event_envelope(event, account_timezone=user_timezone)
            for event in result.get("items", [])
        ]
        return {
            "window_day_count": days,
            "events": events,
            "requires_response": [event for event in events if event["requires_response"]],
            "count": len(events),
            "account_timezone": user_timezone,
        }

    @server.tool(name="list_calendars")
    async def list_calendars() -> dict[str, Any]:
        """List calendars visible to the authenticated account."""
        service = build_calendar_service()
        return await execute_google_request(service.calendarList().list())

    @server.tool(name="get_calendar_context")
    async def get_calendar_context() -> dict[str, Any]:
        """Return the account timezone and current local/UTC calendar context."""
        user_tz = await resolve_user_timezone()
        now_local = user_now(user_tz)
        now_utc = now_local.astimezone(pytz.UTC)
        return {
            "current_date": now_local.strftime("%Y-%m-%d"),
            "current_time": now_local.strftime("%H:%M:%S"),
            "timezone": user_tz,
            "day_of_week": now_local.strftime("%A"),
            "current_local_time": now_local.isoformat(),
            "current_utc_time": now_utc.isoformat(),
            "utc_datetime": now_utc.isoformat(),
            "utc_offset": now_local.strftime("%z"),
            "timezone_name": now_local.tzname(),
        }

    @server.tool(name="check_time_availability")
    async def check_time_availability(
        time_min: str,
        time_max: str,
        items: Annotated[
            list[CalendarIdInput],
            "Calendars/attendees to check, as a list of {id: calendar ID or attendee email address}.",
        ],
        time_zone: str | None = None,
    ) -> dict[str, Any]:
        """Check whether known start/end times are free for the specified calendars."""
        request = FreeBusyRequest(
            timeMin=time_min,
            timeMax=time_max,
            items=items,
            timeZone=time_zone,
        )
        effective_time_zone = request.timeZone or await resolve_user_timezone()
        service = build_calendar_service()
        return await execute_google_request(
            service.freebusy().query(
                body=request.model_copy(update={"timeZone": effective_time_zone}).model_dump()
            )
        )

    @server.tool(name="find_common_free_slots")
    async def find_common_free_slots(
        participants: Annotated[
            list[str], "Calendar IDs or attendee email addresses to find common availability for."
        ],
        time_min: str,
        time_max: str,
        slot_duration_minutes: Annotated[
            int, "Desired duration for each suggested meeting slot, in minutes."
        ] = 30,
        granularity_minutes: Annotated[
            int, "Step size in minutes used to generate candidate slots inside each free range."
        ] = 15,
        max_results: int = 10,
        time_zone: str | None = None,
        working_hours_start: Annotated[
            str, "Daily working-hours start in HH:MM 24-hour format."
        ] = "08:00",
        working_hours_end: Annotated[
            str, "Daily working-hours end in HH:MM 24-hour format."
        ] = "17:00",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Suggest common available meeting slots for all participants in a time window.

        Notes:
        - `participants` must be a native JSON array of calendar IDs or email addresses.
        """
        request = FindCommonFreeSlotsRequest(
            participants=participants,
            time_min=time_min,
            time_max=time_max,
            slot_duration_minutes=slot_duration_minutes,
            granularity_minutes=granularity_minutes,
            max_results=max_results,
            time_zone=time_zone,
            working_hours_start=working_hours_start,
            working_hours_end=working_hours_end,
        )
        service = build_calendar_service()
        if not request.participants:
            raise ValueError("participants list cannot be empty.")

        query_timezone = request.time_zone or await resolve_user_timezone()
        fixed_min = _validate_and_fix_datetime(request.time_min, query_timezone)
        fixed_max = _validate_and_fix_datetime(request.time_max, query_timezone)
        if not fixed_min or not fixed_max:
            raise ValueError("time_min and time_max are required.")

        window_start = _parse_rfc3339_datetime(fixed_min)
        window_end = _parse_rfc3339_datetime(fixed_max)
        if window_end <= window_start:
            raise ValueError("time_max must be greater than time_min.")

        if ctx is not None:
            await ctx.info(f"Checking common availability for {len(request.participants)} participant(s).")
        body: dict[str, Any] = {
            "timeMin": fixed_min,
            "timeMax": fixed_max,
            "items": [{"id": participant} for participant in request.participants],
        }
        body["timeZone"] = query_timezone
        freebusy_result = await execute_google_request(service.freebusy().query(body=body))

        calendars = freebusy_result.get("calendars", {})
        all_busy_ranges: list[tuple[datetime, datetime]] = []
        calendar_errors: dict[str, Any] = {}

        for participant in request.participants:
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
                all_busy_ranges.append(
                    (max(window_start, busy_start), min(window_end, busy_end))
                )

        merged_busy = _merge_time_ranges(all_busy_ranges)
        free_ranges: list[tuple[datetime, datetime]] = []
        cursor = window_start
        for busy_start, busy_end in merged_busy:
            if busy_end <= cursor:
                continue
            if busy_start > cursor:
                free_ranges.append((cursor, busy_start))
            cursor = max(cursor, busy_end)
            if cursor >= window_end:
                break
        if cursor < window_end:
            free_ranges.append((cursor, window_end))

        free_ranges_with_working_hours = _apply_working_hours(
            free_ranges,
            request.working_hours_start,
            request.working_hours_end,
        )

        suggestions = _build_slot_candidates(
            free_ranges_with_working_hours,
            request.slot_duration_minutes,
            request.granularity_minutes,
            request.max_results,
        )

        return {
            "participants": request.participants,
            "time_min": fixed_min,
            "time_max": fixed_max,
            "slot_duration_minutes": request.slot_duration_minutes,
            "granularity_minutes": request.granularity_minutes,
            "working_hours_start": request.working_hours_start,
            "working_hours_end": request.working_hours_end,
            "timezone": query_timezone,
            "total_suggestions": len(suggestions),
            "suggested_slots": suggestions,
            "calendar_errors": calendar_errors,
        }

    @server.tool(name="create_event")
    async def create_event(
        summary: Annotated[str, "Event title/summary."],
        start_datetime: Annotated[str, "Event start datetime in RFC3339 or ISO format."],
        end_datetime: Annotated[str, "Event end datetime in RFC3339 or ISO format."],
        calendar_id: str = "primary",
        idempotency_key: str | None = None,
        timezone: str | None = None,
        description: str | None = None,
        location: Annotated[str | None, "Optional event location."] = None,
        color_id: str | None = None,
        visibility: Annotated[
            Literal["default", "public", "private", "confidential"], "Event visibility mode."
        ] = "default",
        transparency: Annotated[
            Literal["opaque", "transparent"], "Whether the event blocks time on the calendar."
        ] = "opaque",
        conference_data: Annotated[
            dict[str, Any] | None, "Optional Google conference data payload (for Meet links)."
        ] = None,
        attendees: Annotated[list[EventAttendeeInput] | None, "Event attendees to invite."] = None,
        attachments: list[EventAttachmentInput] | None = None,
        supports_attachments: bool = True,
        send_updates: str | None = None,
        reminders: Annotated[
            dict[str, Any] | None, "Reminder override configuration (Google Calendar reminders shape)."
        ] = None,
        recurrence: Annotated[list[str] | None, "Recurrence rules as RRULE strings."] = None,
        on_conflict: Annotated[
            Literal["fail", "suggest_next_slot"],
            "Conflict strategy when the requested time overlaps existing events.",
        ] = "fail",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create a calendar event with optional attendees, reminders, and recurrence."""
        request = CreateEventRequest(
            calendar_id=calendar_id,
            idempotency_key=idempotency_key,
            summary=summary,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            timezone=timezone,
            description=description,
            location=location,
            color_id=color_id,
            visibility=visibility,
            transparency=transparency,
            conference_data=conference_data,
            attendees=attendees,
            attachments=attachments,
            supports_attachments=supports_attachments,
            send_updates=send_updates,
            reminders=reminders,
            recurrence=recurrence,
            on_conflict=on_conflict,
        )
        service = build_calendar_service()
        timezone_val = request.timezone or await resolve_user_timezone()
        start = _validate_and_fix_datetime(request.start_datetime, timezone_val)
        end = _validate_and_fix_datetime(request.end_datetime, timezone_val)
        if start is None:
            raise ValueError("start_datetime is required and could not be parsed.")
        if end is None:
            raise ValueError("end_datetime is required and could not be parsed.")
        body: dict[str, Any] = {
            "summary": request.summary,
            "start": {"dateTime": start, "timeZone": timezone_val},
            "end": {"dateTime": end, "timeZone": timezone_val},
        }
        deterministic_event_id = (
            _idempotent_event_id(request.idempotency_key)
            if request.idempotency_key
            else None
        )
        if deterministic_event_id is not None:
            body["id"] = deterministic_event_id
            body["extendedProperties"] = {
                "private": {"mcpIdempotencyKey": request.idempotency_key}
            }
            try:
                existing = await run_blocking(
                    service.events().get(
                        calendarId=request.calendar_id,
                        eventId=deterministic_event_id,
                    ).execute
                )
                return {"success": True, "event": existing, "deduplicated": True}
            except HttpError as exc:
                if getattr(exc.resp, "status", None) != 404:
                    raise
        if request.description:
            body["description"] = request.description
        if request.location:
            body["location"] = request.location
        if request.color_id is not None:
            body["colorId"] = request.color_id
        if request.visibility is not None:
            body["visibility"] = request.visibility
        if request.transparency is not None:
            body["transparency"] = request.transparency
        if request.conference_data is not None:
            body["conferenceData"] = request.conference_data
        if request.attendees:
            body["attendees"] = [item.model_dump(by_alias=True, exclude_none=True) for item in request.attendees]
        if request.attachments is not None:
            body["attachments"] = [a.to_api() for a in request.attachments]
        if request.reminders:
            body["reminders"] = request.reminders
        if request.recurrence:
            body["recurrence"] = request.recurrence
        conflict_check = await run_blocking(
            _check_time_slot_conflicts,
            service,
            request.calendar_id,
            start,
            end,
        )
        if conflict_check["has_conflicts"]:
            response: dict[str, Any] = {
                "error": "Time slot is not available - there are overlapping events",
                "status": "CONFLICT",
                "conflicting_events": conflict_check["conflicts"],
                "conflict_check_error": conflict_check["error"],
            }
            if request.on_conflict == "suggest_next_slot":
                response["suggested_slots"] = await run_blocking(
                    _suggest_next_available_slots,
                    service=service,
                    calendar_id=request.calendar_id,
                    requested_start=start,
                    requested_end=end,
                )
            return response
        if ctx is not None:
            await ctx.info(f"Creating event '{request.summary}'.")
        try:
            event = await execute_google_request(
                service.events()
                .insert(
                    calendarId=request.calendar_id,
                    body=body,
                    sendUpdates=request.send_updates,
                    supportsAttachments=request.supports_attachments,
                    conferenceDataVersion=1 if body.get("conferenceData") else 0,
                )
            )
            return {"success": True, "event": event, "deduplicated": False}
        except HttpError as exc:
            if deterministic_event_id is None or getattr(exc.resp, "status", None) != 409:
                raise
            existing = await execute_google_request(
                service.events().get(
                    calendarId=request.calendar_id,
                    eventId=deterministic_event_id,
                )
            )
            return {"success": True, "event": existing, "deduplicated": True}

    @server.tool(name="update_event")
    async def update_event(
        event_id: str,
        calendar_id: str = "primary",
        summary: Annotated[str | None, "Updated title/summary."] = None,
        start_datetime: Annotated[str | None, "Updated start datetime in RFC3339 or ISO format."] = None,
        end_datetime: Annotated[str | None, "Updated end datetime in RFC3339 or ISO format."] = None,
        timezone: str | None = None,
        description: str | None = None,
        location: Annotated[str | None, "Updated event location."] = None,
        color_id: str | None = None,
        visibility: Annotated[
            Literal["default", "public", "private", "confidential"] | None, "Updated event visibility mode."
        ] = None,
        transparency: Annotated[
            Literal["opaque", "transparent"] | None, "Whether the event blocks time on the calendar."
        ] = None,
        conference_data: Annotated[
            dict[str, Any] | None, "Optional Google conference data payload (for Meet links)."
        ] = None,
        attendees: Annotated[
            list[EventAttendeeInput] | None, "Replacement attendee list for the event."
        ] = None,
        attachments: list[EventAttachmentInput] | None = None,
        supports_attachments: bool = True,
        reminders: Annotated[dict[str, Any] | None, "Updated reminder configuration."] = None,
        recurrence: Annotated[list[str] | None, "Updated recurrence rules as RRULE strings."] = None,
        send_updates: str | None = None,
        on_conflict: Annotated[
            Literal["fail", "suggest_next_slot"],
            "Conflict strategy when the updated time overlaps existing events.",
        ] = "fail",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Patch an existing calendar event with the provided fields."""
        request = UpdateEventRequest(
            event_id=event_id,
            calendar_id=calendar_id,
            summary=summary,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            timezone=timezone,
            description=description,
            location=location,
            color_id=color_id,
            visibility=visibility,
            transparency=transparency,
            conference_data=conference_data,
            attendees=attendees,
            attachments=attachments,
            supports_attachments=supports_attachments,
            reminders=reminders,
            recurrence=recurrence,
            send_updates=send_updates,
            on_conflict=on_conflict,
        )
        service = build_calendar_service()
        timezone_val = request.timezone or await resolve_user_timezone()
        patch_data: dict[str, Any] = {}
        for key in (
            "summary",
            "description",
            "location",
            "attendees",
            "reminders",
            "recurrence",
            "visibility",
            "transparency",
        ):
            value = getattr(request, key)
            if value is not None:
                patch_data[key] = (
                    [item.model_dump(by_alias=True, exclude_none=True) for item in value]
                    if key == "attendees"
                    else value
                )
        if request.color_id is not None:
            patch_data["colorId"] = request.color_id
        if request.conference_data is not None:
            patch_data["conferenceData"] = request.conference_data
        if request.attachments is not None:
            patch_data["attachments"] = [a.to_api() for a in request.attachments]
        if request.start_datetime:
            start_datetime_value = _validate_and_fix_datetime(request.start_datetime, timezone_val)
            if start_datetime_value is None:
                raise ValueError("start_datetime is required and could not be parsed.")
            patch_data["start"] = {
                "dateTime": start_datetime_value,
                "timeZone": timezone_val,
            }
        if request.end_datetime:
            end_datetime_value = _validate_and_fix_datetime(request.end_datetime, timezone_val)
            if end_datetime_value is None:
                raise ValueError("end_datetime is required and could not be parsed.")
            patch_data["end"] = {
                "dateTime": end_datetime_value,
                "timeZone": timezone_val,
            }
        if patch_data.get("start") and patch_data.get("end"):
            conflict_check = await run_blocking(
                _check_time_slot_conflicts,
                service,
                request.calendar_id,
                patch_data["start"]["dateTime"],
                patch_data["end"]["dateTime"],
                exclude_event_id=request.event_id,
            )
            if conflict_check["has_conflicts"]:
                response: dict[str, Any] = {
                    "error": "New time slot is not available - there are overlapping events",
                    "status": "CONFLICT",
                    "conflicting_events": conflict_check["conflicts"],
                    "conflict_check_error": conflict_check["error"],
                }
                if request.on_conflict == "suggest_next_slot":
                    response["suggested_slots"] = await run_blocking(
                        _suggest_next_available_slots,
                        service=service,
                        calendar_id=request.calendar_id,
                        requested_start=patch_data["start"]["dateTime"],
                        requested_end=patch_data["end"]["dateTime"],
                    )
                return response
        if ctx is not None:
            await ctx.info(f"Updating event {request.event_id}.")
        event = await execute_google_request(
            service.events()
            .patch(
                calendarId=request.calendar_id,
                eventId=request.event_id,
                body=patch_data,
                sendUpdates=request.send_updates,
                supportsAttachments=request.supports_attachments,
                conferenceDataVersion=1 if patch_data.get("conferenceData") else 0,
            )
        )
        return {"success": True, "event": event}

    @server.tool(name="respond_to_event")
    async def respond_to_event(
        event_id: str,
        response_status: Literal["accepted", "tentative", "declined"],
        calendar_id: str = "primary",
        send_updates: Literal["all", "externalOnly", "none"] | None = "all",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Set the authenticated attendee's RSVP response for an event."""
        request = RespondToEventRequest(
            calendar_id=calendar_id,
            event_id=event_id,
            response_status=response_status,
            send_updates=send_updates,
        )
        service = build_calendar_service()
        event = await execute_google_request(
            service.events().get(
                calendarId=request.calendar_id,
                eventId=request.event_id,
            )
        )
        attendees = list(event.get("attendees", []))
        self_attendee = next(
            (attendee for attendee in attendees if attendee.get("self")),
            None,
        )
        if self_attendee is None:
            raise ValueError(
                "The authenticated account is not an attendee of this event and cannot RSVP."
            )
        self_attendee["responseStatus"] = request.response_status
        updated = await execute_google_request(
            service.events().patch(
                calendarId=request.calendar_id,
                eventId=request.event_id,
                body={"attendees": attendees},
                sendUpdates=request.send_updates,
            )
        )
        if ctx is not None:
            await ctx.info(f"RSVP updated to {request.response_status}.")
        return {
            "success": True,
            "event_id": request.event_id,
            "response_status": request.response_status,
            "event": updated,
        }

    @server.tool(name="add_event_attachment")
    async def add_event_attachment(
        event_id: str,
        attachment: Annotated[
            EventAttachmentInput,
            "Attachment to add: file_url plus optional title, mime_type, icon_link, and Drive file_id.",
        ],
        calendar_id: str = "primary",
        send_updates: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Add a single attachment to an existing event (deduplicates by fileUrl)."""
        request = AddEventAttachmentRequest(
            calendar_id=calendar_id,
            event_id=event_id,
            attachment=attachment,
            send_updates=send_updates,
        )
        service = build_calendar_service()
        if ctx is not None:
            await ctx.info(f"Adding attachment to event {request.event_id}.")
        event = await execute_google_request(
            service.events()
            .get(calendarId=request.calendar_id, eventId=request.event_id)
        )
        attachments = list(event.get("attachments", []))
        candidate = request.attachment.to_api()
        if not any(isinstance(a, dict) and a.get("fileUrl") == candidate.get("fileUrl") for a in attachments):
            attachments.append(candidate)
        updated = await execute_google_request(
            service.events()
            .patch(
                calendarId=request.calendar_id,
                eventId=request.event_id,
                body={"attachments": attachments},
                sendUpdates=request.send_updates,
                supportsAttachments=True,
            )
        )
        return {"event": updated}

    @server.tool(name="remove_event_attachment")
    async def remove_event_attachment(
        event_id: str,
        calendar_id: str = "primary",
        file_url: str | None = None,
        file_id: str | None = None,
        send_updates: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Remove event attachment(s) by fileUrl or fileId."""
        request = RemoveEventAttachmentRequest(
            calendar_id=calendar_id,
            event_id=event_id,
            file_url=file_url,
            file_id=file_id,
            send_updates=send_updates,
        )
        service = build_calendar_service()
        if not request.file_url and not request.file_id:
            raise ValueError("Provide at least one of file_url or file_id.")
        if not await confirm_destructive_action(
            ctx,
            "remove_event_attachment",
            f"Remove the selected attachment from event {request.event_id}?",
        ):
            return {"status": "cancelled", "event_id": request.event_id}
        if ctx is not None:
            await ctx.info(f"Removing attachment from event {request.event_id}.")
        event = await execute_google_request(
            service.events()
            .get(calendarId=request.calendar_id, eventId=request.event_id)
        )
        attachments = list(event.get("attachments", []))
        filtered = []
        for item in attachments:
            if not isinstance(item, dict):
                continue
            same_url = request.file_url and item.get("fileUrl") == request.file_url
            same_id = request.file_id and item.get("fileId") == request.file_id
            if same_url or same_id:
                continue
            filtered.append(item)
        updated = await execute_google_request(
            service.events()
            .patch(
                calendarId=request.calendar_id,
                eventId=request.event_id,
                body={"attachments": filtered},
                sendUpdates=request.send_updates,
                supportsAttachments=True,
            )
        )
        return {"event": updated, "removed_count": len(attachments) - len(filtered)}

    @server.tool(name="download_event_attachment", task=True)
    async def download_event_attachment(
        event_id: str,
        output_path: str,
        calendar_id: str = "primary",
        file_url: str | None = None,
        file_id: str | None = None,
        export_mime_type: Annotated[
            str | None,
            "Desired export MIME type for Google-native attachments (e.g. Docs/Sheets/Slides); ignored for binary files.",
        ] = None,
        overwrite: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Download/export an event attachment to local filesystem using Drive API."""
        request = DownloadEventAttachmentRequest(
            calendar_id=calendar_id,
            event_id=event_id,
            output_path=output_path,
            file_url=file_url,
            file_id=file_id,
            export_mime_type=export_mime_type,
            overwrite=overwrite,
        )
        from ..file_uploads import require_local_filesystem

        require_local_filesystem("Calendar attachment download")
        service = build_calendar_service()
        drive = build_drive_service()
        if ctx is not None:
            await ctx.info(f"Resolving attachment for event {request.event_id}.")
        event = await execute_google_request(
            service.events()
            .get(calendarId=request.calendar_id, eventId=request.event_id)
        )
        attachments = event.get("attachments", [])

        selected: dict[str, Any] | None = None
        if request.file_url or request.file_id:
            for item in attachments:
                if not isinstance(item, dict):
                    continue
                same_url = request.file_url and item.get("fileUrl") == request.file_url
                same_id = request.file_id and item.get("fileId") == request.file_id
                if same_url or same_id:
                    selected = item
                    break
        elif len(attachments) == 1 and isinstance(attachments[0], dict):
            selected = attachments[0]
        else:
            raise ValueError(
                "Ambiguous attachment selection. Provide file_url or file_id, "
                "or ensure event has exactly one attachment."
            )

        file_url_resolved = request.file_url or (selected.get("fileUrl") if selected else None)
        file_id_resolved = request.file_id or (selected.get("fileId") if selected else None) or _extract_drive_file_id(file_url_resolved)
        if not file_id_resolved:
            raise ValueError("Could not resolve Drive file_id from attachment metadata.")

        meta = await execute_google_request(
            drive.files().get(fileId=file_id_resolved, fields="id,name,mimeType")
        )
        mime_type = meta.get("mimeType", "")
        name = meta.get("name", file_id_resolved)
        out_path = Path(request.output_path)
        if mime_type.startswith("application/vnd.google-apps"):
            export_mime = request.export_mime_type or GOOGLE_EXPORT_MIME_DEFAULTS.get(mime_type)
            if not export_mime:
                raise ValueError(
                    "Google-native file requires export_mime_type for this mimeType: "
                    f"{mime_type}"
                )
            req = drive.files().export_media(fileId=file_id_resolved, mimeType=export_mime)
            mode = "export"
        else:
            req = drive.files().get_media(fileId=file_id_resolved)
            export_mime = None
            mode = "download"

        size = await stream_google_download(
            req,
            out_path,
            overwrite=request.overwrite,
            progress_label="Downloading attachment bytes",
            ctx=ctx,
        )
        if ctx is not None:
            await ctx.report_progress(100, 100, "Attachment saved")
        return {
            "status": "ok",
            "calendar_id": request.calendar_id,
            "event_id": request.event_id,
            "file_id": file_id_resolved,
            "file_name": name,
            "file_mime_type": mime_type,
            "mode": mode,
            "export_mime_type": export_mime,
            "saved_to": str(out_path),
            "bytes_written": size,
        }

    @server.tool(name="delete_event")
    async def delete_event(
        event_id: str,
        calendar_id: str = "primary",
        send_updates: str | None = None,
        force: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Delete a calendar event after mandatory interactive confirmation."""
        request = DeleteEventRequest(
            calendar_id=calendar_id,
            event_id=event_id,
            send_updates=send_updates,
            force=force,
        )
        if not request.force:
            confirm_ctx = require_elicitation_context(ctx, "delete_event")
            response = await confirm_ctx.elicit(
                f"Delete event {request.event_id} from calendar {request.calendar_id}?",
                response_type=bool,  # type: ignore[arg-type]
            )
            if response.action != "accept" or not bool(response.data):
                return {"status": "cancelled"}
        service = build_calendar_service()
        await execute_google_request(
            service.events().delete(
                calendarId=request.calendar_id,
                eventId=request.event_id,
                sendUpdates=request.send_updates,
            )
        )
        return {"success": True, "event_id": request.event_id}
