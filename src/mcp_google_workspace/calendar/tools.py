"""FastMCP calendar tools migrated from raw MCP SDK style."""

from __future__ import annotations

import logging

import re
from hashlib import sha256
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn

import pytz
from fastmcp import Context, FastMCP
from googleapiclient.errors import HttpError

from ..auth import build_calendar_service, build_drive_service
from ..common.async_ops import (
    execute_google_request,
    run_blocking,
)
from ..common.confirmation import confirm_destructive_action
from ..common.downloads import stream_google_download
from ..common.errors import RecoverableToolError
from .presentation import calendars_page, event_detail, event_envelope, free_busy
from ..common.timezone import resolve_user_timezone, user_now
from .schemas import (
    SendUpdates,
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

LOGGER = logging.getLogger(__name__)


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
    except Exception as exc:  # noqa: BLE001 - reported to the caller, never fatal
        # The event is still written; the caller learns the slot was not
        # verified through ``warnings`` on the result instead of silently.
        LOGGER.warning("Calendar conflict check failed for %s: %s", calendar_id, exc)
        return {
            "has_conflicts": False,
            "conflicts": [],
            "error": f"Could not check for conflicts: {exc}",
        }


def _conflict_warnings(conflict_check: dict[str, Any]) -> list[str]:
    error = conflict_check.get("error")
    return [str(error)] if error else []


async def _raise_time_slot_conflict(
    service: Any,
    *,
    calendar_id: str,
    start: str,
    end: str,
    conflict_check: dict[str, Any],
    suggest_next_slot: bool,
    message: str,
) -> NoReturn:
    """Raise the ``conflict`` tool error for an occupied time slot.

    Carries the overlapping events (and, when asked for, free alternatives)
    in ``details`` so the caller can pick another time. Raised before any
    write, so nothing has changed on the calendar.
    """
    details: dict[str, Any] = {
        "calendar_id": calendar_id,
        "requested_start": start,
        "requested_end": end,
        "conflicting_events": conflict_check["conflicts"],
    }
    action: dict[str, Any] = {"action": "choose_a_free_time_slot"}
    if suggest_next_slot:
        details["suggested_slots"] = await run_blocking(
            _suggest_next_available_slots,
            service=service,
            calendar_id=calendar_id,
            requested_start=start,
            requested_end=end,
        )
        action = {"action": "retry_with_one_of_suggested_slots"}
    else:
        action["hint"] = "Call again with on_conflict='suggest_next_slot' to receive free alternatives."
    raise RecoverableToolError(
        "conflict",
        message,
        required_action=action,
        details=details,
    )


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
        time_min: Annotated[
            str | None,
            (
                "RFC3339 time with offset, e.g. '2026-03-01T00:00:00Z'; returns events that end after it. "
                "Overrides the start of range_preset; with no time_min, time_max or range_preset the "
                "list is unbounded and starts at the calendar's oldest events."
            ),
        ] = None,
        time_max: Annotated[
            str | None,
            (
                "RFC3339 time with offset, e.g. '2026-03-08T00:00:00Z'; returns events that start "
                "before it. Overrides the end of range_preset."
            ),
        ] = None,
        max_results: Annotated[
            int, "Maximum events to return, 1 to 2500; this tool takes no page token, so narrow the window instead of paging."
        ] = 25,
        single_events: Annotated[
            bool,
            (
                "True expands recurring events into their individual occurrences (instance IDs). "
                "False returns each series once, as its master event."
            ),
        ] = True,
        order_by: Annotated[
            Literal["startTime", "updated"],
            (
                "'startTime' sorts by start (applied only when single_events is true; otherwise no "
                "order is requested) or 'updated' sorts by last modification time."
            ),
        ] = "startTime",
        range_preset: Annotated[
            Literal["today", "tomorrow", "this_week", "next_7_days"] | None,
            (
                "Window in the account's time zone: 'today' or 'tomorrow' (midnight to midnight), "
                "'this_week' (Monday 00:00 for 7 days) or 'next_7_days' (from now). Fills only the "
                "bound that time_min or time_max leaves unset."
            ),
        ] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Find events on one calendar in a time window, given as a range_preset or explicit time_min/time_max. For the user's upcoming week plus pending invitations use calendar_get_calendar_digest, and for full details of known events use calendar_read_events. Returns compact events (id, title, start and end in the account time zone, all_day, status, organizer, my_response, requires_response, meeting_url, location, is_recurring, updated_at), count, next_page_token, account_timezone and the effective_time_min/effective_time_max that were applied."""
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
        LOGGER.debug(f"Listing events for calendar {request.calendar_id}.")
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
            "account_timezone": user_timezone,
        }

    @server.tool(name="read_events")
    async def read_events(
        event_ids: list[str],
        calendar_id: str = "primary",
        time_zone: Annotated[
            str | None,
            "IANA time zone name, e.g. 'Europe/Berlin', in which start and end are returned; defaults to the account's calendar time zone.",
        ] = None,
        max_attendees: Annotated[
            int | None, "Return at most this many attendees per event (at least 1); omit for the full guest list."
        ] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read the full details of one to 100 events from one calendar by ID. Use it after calendar_search_events or calendar_get_calendar_digest when you need the description, guest list or attachments, which those compact results leave out. Returns events with the compact fields plus url, description, attendees (name, email, response_status, optional, organizer, is_self, is_resource), attachments (title, file_url, file_id, mime_type), recurrence, recurring_event_id, visibility and transparency, and account_timezone (the zone of start and end); IDs that do not exist are listed in missing_event_ids instead of failing the call."""
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
            events.append(event_detail(event, account_timezone=effective_time_zone))
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
        days: Annotated[int, "Length of the window in days, starting now."] = 7,
        max_results: Annotated[
            int, "Maximum events to return, earliest first; events past this count are left out."
        ] = 50,
    ) -> dict[str, Any]:
        """Summarize the user's upcoming schedule: events on the primary calendar from now until the given number of days ahead, with recurring events expanded. Use it for "what is on my calendar" and pending invitations; for another calendar or a specific window use calendar_search_events. Returns events (compact, earliest first), count, account_timezone, and requires_response: the events whose invitation the user has not answered yet, which calendar_respond_to_event can answer."""
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
        """List the calendars in the user's calendar list: their own, shared and subscribed calendars. Use it to find a calendar_id other than 'primary' for the other calendar tools. Returns calendars, each with id (the calendar_id), name (the user's own name for it when set), description, primary (true for the main calendar), access_role (owner, writer, reader or freeBusyReader), time_zone and hidden, plus count and next_page_token."""
        service = build_calendar_service()
        return calendars_page(await execute_google_request(service.calendarList().list()))

    @server.tool(name="get_calendar_context")
    async def get_calendar_context() -> dict[str, Any]:
        """Get the account's Calendar time zone and the current date and time in it. Call it before turning relative phrases such as "tomorrow at 3pm" into the times other calendar tools take. Returns current_date, current_time, day_of_week, timezone (IANA name), utc_offset, current_local_time and current_utc_time."""
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
        time_min: Annotated[str, "Start of the period to check, RFC3339 with offset, e.g. '2026-03-02T15:00:00+01:00'."],
        time_max: Annotated[str, "End of the period to check, RFC3339 with offset; must be after time_min."],
        items: Annotated[
            list[CalendarIdInput],
            "Calendars/attendees to check, 1 to 50, as a list of {id: calendar ID or attendee email address}.",
        ],
        time_zone: Annotated[
            str | None,
            "IANA time zone name, e.g. 'Europe/Berlin', in which busy periods are returned; defaults to the account's calendar time zone.",
        ] = None,
    ) -> dict[str, Any]:
        """Check whether one known time period is free on up to 50 calendars or people. To find open times instead of testing one, use calendar_find_common_free_slots. Returns all_free, time_min, time_max, account_timezone (the zone of every time returned) and calendars in request order, each with id, free (null when it could not be read), busy (start/end periods; empty means free) and errors (Google's reasons, e.g. notFound)."""
        request = FreeBusyRequest(
            timeMin=time_min,
            timeMax=time_max,
            items=items,
            timeZone=time_zone,
        )
        effective_time_zone = request.timeZone or await resolve_user_timezone()
        service = build_calendar_service()
        response = await execute_google_request(
            service.freebusy().query(
                body=request.model_copy(update={"timeZone": effective_time_zone}).model_dump()
            )
        )
        return free_busy(
            response,
            requested_ids=[item.id for item in request.items],
            account_timezone=effective_time_zone,
        )

    @server.tool(name="find_common_free_slots")
    async def find_common_free_slots(
        participants: Annotated[
            list[str],
            "Calendar IDs or attendee email addresses (at least one) whose busy times must all be avoided, as a JSON array.",
        ],
        time_min: Annotated[
            str,
            (
                "Start of the search window: RFC3339 with offset, a naive 'YYYY-MM-DDTHH:MM:SS' or a "
                "'YYYY-MM-DD' (midnight), the last two read in time_zone. Working hours are applied at "
                "this value's UTC offset, so prefer naive local times."
            ),
        ],
        time_max: Annotated[
            str, "End of the search window, in the same formats as time_min; must be after time_min."
        ],
        slot_duration_minutes: Annotated[
            int, "Length of each suggested slot in minutes, 5 to 480."
        ] = 30,
        granularity_minutes: Annotated[
            int, "Minutes between candidate start times inside a free period, 5 to 240."
        ] = 15,
        max_results: Annotated[int, "Maximum slots to suggest, 1 to 100, earliest first."] = 10,
        time_zone: Annotated[
            str | None,
            "IANA time zone name, e.g. 'Europe/Berlin', used to read naive time_min/time_max; defaults to the account's calendar time zone.",
        ] = None,
        working_hours_start: Annotated[
            str, "Earliest time of day a slot may start, HH:MM in 24-hour format."
        ] = "08:00",
        working_hours_end: Annotated[
            str, "Latest time of day a slot may end, HH:MM in 24-hour format; must be after working_hours_start."
        ] = "17:00",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Find meeting times when every participant is free, within working hours on each day of a window. To test one already-chosen time use calendar_check_time_availability, then book with calendar_create_event. Returns suggested_slots (start/end pairs, earliest first), total_suggestions, the timezone used, and calendar_errors for participants whose availability could not be read; those participants are treated as free."""
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

        LOGGER.debug(f"Checking common availability for {len(request.participants)} participant(s).")
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
        summary: Annotated[str, "Event title shown on the calendar."],
        start_datetime: Annotated[
            str,
            (
                "Start time: RFC3339 with offset, e.g. '2026-03-02T15:00:00-05:00', or a naive "
                "'YYYY-MM-DDTHH:MM:SS' read in timezone. A bare 'YYYY-MM-DD' becomes midnight in "
                "timezone; all-day events cannot be created."
            ),
        ],
        end_datetime: Annotated[
            str, "End time, in the same formats as start_datetime; must be after it."
        ],
        calendar_id: str = "primary",
        idempotency_key: Annotated[
            str | None,
            (
                "Caller-chosen key, 1 to 256 characters, that makes a retry safe: the event gets an ID "
                "derived from the key, so a repeat call with the same key returns the event already "
                "created (deduplicated true) instead of creating another, even if other arguments differ."
            ),
        ] = None,
        timezone: Annotated[
            str | None,
            (
                "IANA time zone, e.g. 'America/New_York', used to read naive start/end values and "
                "stored as the event's time zone (a recurrence repeats in it); defaults to the "
                "account's calendar time zone."
            ),
        ] = None,
        description: Annotated[str | None, "Event notes shown in the event details."] = None,
        location: Annotated[str | None, "Free-text location, e.g. an address or a room name."] = None,
        color_id: str | None = None,
        visibility: Annotated[
            Literal["default", "public", "private", "confidential"],
            "Who can see the event's details: 'default' (the calendar's setting), 'public', 'private' or 'confidential'.",
        ] = "default",
        transparency: Annotated[
            Literal["opaque", "transparent"],
            "'opaque' shows the time as busy; 'transparent' leaves it free in availability checks.",
        ] = "opaque",
        conference_data: Annotated[
            dict[str, Any] | None,
            (
                "Raw Google conferenceData object. For a new Google Meet link pass {'createRequest': "
                "{'requestId': '<unique string>', 'conferenceSolutionKey': {'type': 'hangoutsMeet'}}}; "
                "the link is returned in the event's hangoutLink and conferenceData.entryPoints."
            ),
        ] = None,
        attendees: Annotated[
            list[EventAttendeeInput] | None,
            "Guests to invite, each {email, displayName, optional, comment, additionalGuests}; whether they are emailed depends on send_updates.",
        ] = None,
        attachments: Annotated[
            list[EventAttachmentInput] | None,
            "Files to attach, up to 25, each {file_url, title, mime_type, icon_link, file_id}; saved only while supports_attachments is true.",
        ] = None,
        supports_attachments: Annotated[
            bool,
            "Tells Google this call handles event attachments (its supportsAttachments flag); leave true, attachments are only saved when it is true.",
        ] = True,
        send_updates: Annotated[
            SendUpdates | None,
            (
                "Who Google emails the invitation: 'all' guests, 'externalOnly' (only guests who do not "
                "use Google Calendar) or 'none'. When omitted, Google's default of sending no "
                "notification applies."
            ),
        ] = None,
        reminders: Annotated[
            dict[str, Any] | None,
            (
                "Raw Google reminders object, e.g. {'useDefault': false, 'overrides': [{'method': "
                "'popup', 'minutes': 10}, {'method': 'email', 'minutes': 60}]}; omit to keep the "
                "calendar's default reminders."
            ),
        ] = None,
        recurrence: Annotated[
            list[str] | None,
            "RFC 5545 recurrence lines, e.g. ['RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10']; EXDATE and RDATE lines are also accepted.",
        ] = None,
        on_conflict: Annotated[
            Literal["fail", "suggest_next_slot"],
            (
                "Both values fail with a 'conflict' error and create nothing when the time overlaps busy "
                "time on calendar_id; the error's details.conflicting_events lists the busy periods. "
                "'suggest_next_slot' also returns up to 5 free slots of the same length within 72 hours "
                "in details.suggested_slots."
            ),
        ] = "fail",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create an event on one calendar, optionally with guests, a Meet link, reminders, attachments or a recurrence rule. The time is first checked against that calendar's busy times only, and an overlap fails with a conflict error that creates nothing; to plan around other people's calendars use calendar_find_common_free_slots first. Returns status 'ok', event (id, title, start and end in the event's timezone, url, meeting_url, attendees, attachments, recurrence and the other calendar_read_events fields), deduplicated (true when idempotency_key matched an existing event) and warnings, non-empty when the conflict check could not run. Guests receive invitations only as send_updates says."""
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
        # The result's times are shown in the zone the event was written in.
        account_timezone = timezone_val
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
                return {
                    "status": "ok",
                    "event": event_detail(existing, account_timezone=account_timezone),
                    "deduplicated": True,
                    "warnings": [],
                }
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
            await _raise_time_slot_conflict(
                service,
                calendar_id=request.calendar_id,
                start=start,
                end=end,
                conflict_check=conflict_check,
                suggest_next_slot=request.on_conflict == "suggest_next_slot",
                message="Time slot is not available: it overlaps existing events. No event was created.",
            )
        warnings = _conflict_warnings(conflict_check)
        LOGGER.debug(f"Creating event '{request.summary}'.")
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
            return {
                "status": "ok",
                "event": event_detail(event, account_timezone=account_timezone),
                "deduplicated": False,
                "warnings": warnings,
            }
        except HttpError as exc:
            if deterministic_event_id is None or getattr(exc.resp, "status", None) != 409:
                raise
            existing = await execute_google_request(
                service.events().get(
                    calendarId=request.calendar_id,
                    eventId=deterministic_event_id,
                )
            )
            return {
                "status": "ok",
                "event": event_detail(existing, account_timezone=account_timezone),
                "deduplicated": True,
                "warnings": warnings,
            }

    @server.tool(name="update_event")
    async def update_event(
        event_id: Annotated[
            str,
            (
                "Calendar event ID, from calendar_search_events, calendar_get_calendar_digest or "
                "calendar_read_events results. An occurrence's instance ID changes only that "
                "occurrence; the series ID changes every occurrence."
            ),
        ],
        calendar_id: str = "primary",
        summary: Annotated[str | None, "New event title."] = None,
        start_datetime: Annotated[
            str | None,
            (
                "New start time: RFC3339 with offset or a naive 'YYYY-MM-DDTHH:MM:SS' read in timezone. "
                "Pass end_datetime too when moving the event; the conflict check runs only when both are given."
            ),
        ] = None,
        end_datetime: Annotated[
            str | None, "New end time, in the same formats as start_datetime; must end up after the start."
        ] = None,
        timezone: Annotated[
            str | None,
            (
                "IANA time zone, e.g. 'America/New_York', used to read naive start/end values and stored "
                "with them; applies only to the start/end you pass. Defaults to the account's calendar time zone."
            ),
        ] = None,
        description: Annotated[
            str | None, "New event notes, replacing the old ones; an empty string clears them."
        ] = None,
        location: Annotated[
            str | None, "New free-text location; an empty string clears it."
        ] = None,
        color_id: str | None = None,
        visibility: Annotated[
            Literal["default", "public", "private", "confidential"] | None,
            "Who can see the event's details: 'default' (the calendar's setting), 'public', 'private' or 'confidential'.",
        ] = None,
        transparency: Annotated[
            Literal["opaque", "transparent"] | None,
            "'opaque' shows the time as busy; 'transparent' leaves it free in availability checks.",
        ] = None,
        conference_data: Annotated[
            dict[str, Any] | None,
            (
                "Raw Google conferenceData object. To add a Google Meet link pass {'createRequest': "
                "{'requestId': '<unique string>', 'conferenceSolutionKey': {'type': 'hangoutsMeet'}}}."
            ),
        ] = None,
        attendees: Annotated[
            list[EventAttendeeInput] | None,
            (
                "The complete new guest list, each {email, displayName, optional, comment, "
                "additionalGuests}; it replaces the old list, so include every guest to keep "
                "(read them with calendar_read_events)."
            ),
        ] = None,
        attachments: Annotated[
            list[EventAttachmentInput] | None,
            (
                "The complete new attachment list, up to 25, each {file_url, title, mime_type, "
                "icon_link, file_id}; it replaces the old list. To add or remove one file use "
                "calendar_add_event_attachment or calendar_remove_event_attachment."
            ),
        ] = None,
        supports_attachments: Annotated[
            bool,
            "Tells Google this call handles event attachments (its supportsAttachments flag); leave true, attachments are only changed when it is true.",
        ] = True,
        reminders: Annotated[
            dict[str, Any] | None,
            (
                "Raw Google reminders object, e.g. {'useDefault': false, 'overrides': [{'method': "
                "'popup', 'minutes': 10}, {'method': 'email', 'minutes': 60}]}."
            ),
        ] = None,
        recurrence: Annotated[
            list[str] | None,
            (
                "New RFC 5545 recurrence lines replacing the old ones, e.g. "
                "['RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=10']. Passing it, even as an empty list, "
                "requires prepare_workspace_action and commit_workspace_action."
            ),
        ] = None,
        send_updates: Annotated[
            SendUpdates | None,
            (
                "Who Google emails about the change: 'all' guests, 'externalOnly' (only guests who do "
                "not use Google Calendar) or 'none'. When omitted, Google's default of sending no "
                "notification applies."
            ),
        ] = None,
        on_conflict: Annotated[
            Literal["fail", "suggest_next_slot"],
            (
                "Both values fail with a 'conflict' error and change nothing when the new time overlaps "
                "other events on calendar_id; the error's details.conflicting_events lists them. "
                "'suggest_next_slot' also returns up to 5 free slots of the same length within 72 hours "
                "in details.suggested_slots."
            ),
        ] = "fail",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Change an existing event: only the fields you pass change, but attendees, attachments and recurrence each replace the whole existing list. When both start_datetime and end_datetime are given, the new time is checked against the calendar's other events and an overlap fails with a conflict error that changes nothing. Passing recurrence fails with prepare_required until the call goes through prepare_workspace_action and commit_workspace_action. Returns status 'ok', event (the updated event in the calendar_read_events shape, times in timezone or else the account's zone) and warnings, non-empty when the conflict check could not run; guests are notified as send_updates says."""
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
                await _raise_time_slot_conflict(
                    service,
                    calendar_id=request.calendar_id,
                    start=patch_data["start"]["dateTime"],
                    end=patch_data["end"]["dateTime"],
                    conflict_check=conflict_check,
                    suggest_next_slot=request.on_conflict == "suggest_next_slot",
                    message="New time slot is not available: it overlaps existing events. The event was not changed.",
                )
            warnings = _conflict_warnings(conflict_check)
        else:
            warnings = []
        LOGGER.debug(f"Updating event {request.event_id}.")
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
        return {
            "status": "ok",
            "event": event_detail(event, account_timezone=timezone_val),
            "warnings": warnings,
        }

    @server.tool(name="respond_to_event")
    async def respond_to_event(
        event_id: str,
        response_status: Annotated[
            Literal["accepted", "tentative", "declined"],
            "The user's answer: 'accepted' (yes), 'tentative' (maybe) or 'declined' (no).",
        ],
        calendar_id: str = "primary",
        send_updates: Literal["all", "externalOnly", "none"] | None = "all",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Answer an event invitation as the signed-in user: accept, decline or mark tentative. Find unanswered invitations in the requires_response list of calendar_get_calendar_digest; the call fails if the user is not on the event's guest list. Returns status 'ok', event_id, response_status and event (the updated event in the calendar_read_events shape). With the default send_updates 'all', Google notifies the event's guests of the answer."""
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
        LOGGER.debug(f"RSVP updated to {request.response_status}.")
        return {
            "status": "ok",
            "event_id": request.event_id,
            "response_status": request.response_status,
            "event": event_detail(updated, account_timezone=await resolve_user_timezone()),
        }

    @server.tool(name="add_event_attachment")
    async def add_event_attachment(
        event_id: str,
        attachment: Annotated[
            EventAttachmentInput,
            "Attachment to add: file_url plus optional title, mime_type, icon_link and Drive file_id.",
        ],
        calendar_id: str = "primary",
        send_updates: SendUpdates | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Attach one file to an existing event, keeping its current attachments. If an attachment with the same file_url is already there nothing is added; to replace the whole list use calendar_update_event. Returns status 'ok' and event, the updated event in the calendar_read_events shape with its attachments (Google allows at most 25 per event)."""
        request = AddEventAttachmentRequest(
            calendar_id=calendar_id,
            event_id=event_id,
            attachment=attachment,
            send_updates=send_updates,
        )
        service = build_calendar_service()
        LOGGER.debug(f"Adding attachment to event {request.event_id}.")
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
        return {
            "status": "ok",
            "event": event_detail(updated, account_timezone=await resolve_user_timezone()),
        }

    @server.tool(name="remove_event_attachment")
    async def remove_event_attachment(
        event_id: str,
        calendar_id: str = "primary",
        file_url: Annotated[
            str | None,
            "Remove attachments whose fileUrl equals this, as listed by calendar_read_events; give file_url, file_id or both.",
        ] = None,
        file_id: Annotated[
            str | None, "Remove attachments whose Drive fileId equals this; give file_url, file_id or both."
        ] = None,
        send_updates: SendUpdates | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Detach files from an event, matching by file_url or Drive file_id; the files themselves are not deleted. The user is asked to confirm first: declining returns status 'cancelled', and a client that cannot ask gets a confirmation_required error, both with nothing changed. Returns status 'ok', event (the updated event in the calendar_read_events shape) and removed_count, which is 0 when nothing matched."""
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
        LOGGER.debug(f"Removing attachment from event {request.event_id}.")
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
        return {
            "status": "ok",
            "event": event_detail(updated, account_timezone=await resolve_user_timezone()),
            "removed_count": len(attachments) - len(filtered),
        }

    @server.tool(name="download_event_attachment", task=True)
    async def download_event_attachment(
        event_id: str,
        output_path: Annotated[
            str, "Destination file path on the server's machine, including the file name; missing folders are created."
        ],
        calendar_id: str = "primary",
        file_url: Annotated[
            str | None,
            "fileUrl of the attachment to save, as listed by calendar_read_events; omit both selectors when the event has exactly one attachment.",
        ] = None,
        file_id: Annotated[
            str | None,
            "Drive file ID of the attachment to save; omit both selectors when the event has exactly one attachment.",
        ] = None,
        export_mime_type: Annotated[
            str | None,
            (
                "Export format for a Google Docs, Sheets, Slides or Drawings attachment, e.g. "
                "'application/pdf' or 'text/csv'; defaults to PDF (PNG for Drawings) and is ignored for other files."
            ),
        ] = None,
        overwrite: Annotated[
            bool, "Replace an existing file at output_path; when false an existing file makes the call fail."
        ] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Save one of an event's attachments to a file on the machine running this server, fetching it from Drive and exporting Google Docs, Sheets, Slides and Drawings. Choose the attachment with file_url or file_id; with neither, the event must have exactly one attachment. It only works when the server runs locally; over a remote connection it fails with picker_required. Returns saved_to, bytes_written, file_name, file_mime_type, mode ('download' or 'export') and export_mime_type."""
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
        LOGGER.debug(f"Resolving attachment for event {request.event_id}.")
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
        event_id: Annotated[
            str,
            (
                "Calendar event ID, from calendar_search_events, calendar_get_calendar_digest or "
                "calendar_read_events results. An occurrence's instance ID deletes only that "
                "occurrence; the series ID deletes the whole series."
            ),
        ],
        calendar_id: str = "primary",
        send_updates: SendUpdates | None = None,
        force: Annotated[
            bool,
            (
                "Skip the interactive confirmation when true. Default false: the deletion "
                "is confirmed with the user first (clients that cannot confirm get a "
                "confirmation_required error and nothing is deleted)."
            ),
        ] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Delete an event from a calendar. Unless force is true the user is asked to confirm first: declining returns status 'cancelled', and a client that cannot ask gets a confirmation_required error, both with nothing deleted. Returns status 'ok' and event_id once the event is deleted. Guests are notified of the cancellation as send_updates says."""
        request = DeleteEventRequest(
            calendar_id=calendar_id,
            event_id=event_id,
            send_updates=send_updates,
            force=force,
        )
        if not request.force:
            if not await confirm_destructive_action(
                ctx,
                "delete_event",
                f"Delete event {request.event_id} from calendar {request.calendar_id}?",
            ):
                return {"status": "cancelled"}
        service = build_calendar_service()
        await execute_google_request(
            service.events().delete(
                calendarId=request.calendar_id,
                eventId=request.event_id,
                sendUpdates=request.send_updates,
            )
        )
        return {"status": "ok", "event_id": request.event_id}
