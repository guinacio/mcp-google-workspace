"""FastMCP calendar tools migrated from raw MCP SDK style."""

from __future__ import annotations

import logging

from hashlib import sha256
from datetime import datetime, timedelta
from datetime import time as dt_time
from pydantic import Field
from typing import Annotated, Any, Literal, NoReturn
from uuid import uuid4

import pytz
from fastmcp import Context, FastMCP
from googleapiclient.errors import HttpError

from ..auth import build_calendar_service
from ..common.async_ops import (
    execute_google_request,
    run_blocking,
)
from ..common.errors import RecoverableToolError
from .presentation import (
    calendar_entry,
    drive_file_id_from_url,
    event_detail,
    event_envelope,
    free_busy,
)
from ..common.timezone import resolve_user_timezone, user_now
from .schemas import (
    SendUpdates,
    CreateEventRequest,
    DeleteEventRequest,
    EventAttachmentInput,
    EventAttachmentSelector,
    EventAttendeeInput,
    EventReminderInput,
    FindFreeTimeRequest,
    ReadEventsRequest,
    RespondToEventRequest,
    ListEventsRequest,
    SetEventAttachmentsRequest,
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



def _meet_conference_request(request_key: str | None) -> dict[str, Any]:
    """conferenceData asking Google to create a new Meet link.

    Google deduplicates creation by requestId; a create retried with the same
    idempotency_key reuses it, every other call gets a fresh one.
    """
    request_id = (
        sha256(f"meet:{request_key}".encode("utf-8")).hexdigest()
        if request_key
        else uuid4().hex
    )
    return {
        "createRequest": {
            "requestId": request_id,
            "conferenceSolutionKey": {"type": "hangoutsMeet"},
        }
    }


def _popup_reminders(minutes: list[int]) -> dict[str, Any]:
    """reminders for popup alerts at these minutes before start; [] means none."""
    return {
        "useDefault": False,
        "overrides": [
            {"method": "popup", "minutes": value} for value in sorted(set(minutes))
        ],
    }


def _event_reminders(reminders: list[EventReminderInput]) -> dict[str, Any]:
    """reminders with each reminder's method, in minutes order; [] means none."""
    unique = sorted({(item.minutes, item.method) for item in reminders})
    return {
        "useDefault": False,
        "overrides": [{"method": method, "minutes": minutes} for minutes, method in unique],
    }


def _reminders_body(request: CreateEventRequest | UpdateEventRequest) -> dict[str, Any] | None:
    """The Event reminders field for whichever reminder parameter was given, else None."""
    if request.reminders is not None:
        return _event_reminders(request.reminders)
    if request.reminder_minutes is not None:
        return _popup_reminders(request.reminder_minutes)
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
    zone: Any = pytz.UTC,
) -> list[tuple[datetime, datetime]]:
    """Clamp free ranges to the working hours of each local day in ``zone`` (a pytz zone).

    Each day's bounds are localized on their own date, so a window across a DST
    change keeps the hours at local time instead of the first day's offset.
    """
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
        current_day = free_start.astimezone(zone).date()
        last_day = free_end.astimezone(zone).date()
        while current_day <= last_day:
            day_start = zone.localize(datetime.combine(current_day, dt_time(start_hour, start_minute)))
            day_end = zone.localize(datetime.combine(current_day, dt_time(end_hour, end_minute)))
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
            int,
            Field(ge=1, le=2500, description="Events per page; next_page_token continues the list."),
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
        page_token: Annotated[
            str | None,
            (
                "The next_page_token from the previous response of this same call; omit for the first "
                "page. Keep every other argument the same; range_preset 'next_7_days' moves with the "
                "clock, so page with the effective_time_min/effective_time_max of the first response."
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
            page_token=page_token,
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
        if request.page_token:
            list_kwargs["pageToken"] = request.page_token
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
        """Read the full details of one to 100 events from one calendar by ID. Use it after calendar_search_events or calendar_get_calendar_digest when you need the description, guest list or attachments, which those compact results leave out. Returns events with the compact fields plus url, description, attendees (name, email, response_status, optional, organizer, is_self, is_resource), attachments (title, file_url, mime_type and the Drive file_id that drive_download_file takes), recurrence, recurring_event_id, visibility and transparency, and account_timezone (the zone of start and end); IDs that do not exist are listed in missing_event_ids instead of failing the call."""
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

    @server.tool(name="get_calendar_context")
    async def get_calendar_context(
        include_calendars: Annotated[
            bool,
            (
                "Also list every calendar in the user's calendar list (all pages). False returns "
                "only the clock and time zone, with calendars null, and skips that lookup."
            ),
        ] = True,
    ) -> dict[str, Any]:
        """Get the account's Calendar time zone, the current date and time in it, and the user's calendars. Call it before turning relative phrases such as "tomorrow at 3pm" into the times other calendar tools take, or to find a calendar_id other than 'primary'. Returns current_date, current_time, day_of_week, timezone and timezone_name (both the IANA name), utc_offset, current_local_time, current_utc_time (also as utc_datetime), count, has_more (false), next_page_token, fetched_at and calendars: each with id (the calendar_id), name, description, primary, access_role (owner, writer, reader or freeBusyReader), time_zone and hidden."""
        user_tz = await resolve_user_timezone()
        now_local = user_now(user_tz)
        now_utc = now_local.astimezone(pytz.UTC)
        calendars: list[dict[str, Any]] = []
        if include_calendars:
            service = build_calendar_service()
            page_token: str | None = None
            while True:
                page = await execute_google_request(
                    service.calendarList().list(pageToken=page_token)
                )
                calendars.extend(calendar_entry(entry) for entry in page.get("items", []))
                page_token = page.get("nextPageToken")
                if not page_token:
                    break
        return {
            "current_date": now_local.strftime("%Y-%m-%d"),
            "current_time": now_local.strftime("%H:%M:%S"),
            "timezone": user_tz,
            "day_of_week": now_local.strftime("%A"),
            "current_local_time": now_local.isoformat(),
            "current_utc_time": now_utc.isoformat(),
            # Same value as current_utc_time, kept for 2.0.0 callers.
            "utc_datetime": now_utc.isoformat(),
            "utc_offset": now_local.strftime("%z"),
            # The IANA name, like timezone; tzname() gave abbreviations such as "-03".
            "timezone_name": user_tz,
            # null, not [], when the list was not requested: nothing was looked up.
            "calendars": list(calendars) if include_calendars else None,
            # Every page was read, so the list envelope's has_more is false and next_page_token null.
            "count": len(calendars),
        }

    @server.tool(name="find_free_time")
    async def find_free_time(
        participants: Annotated[
            list[str],
            (
                "Calendar IDs ('primary', or an id from calendar_get_calendar_context) or attendee "
                "email addresses whose busy times are checked, 1 to 50, as a JSON array."
            ),
        ],
        time_min: Annotated[
            str,
            (
                "Start of the window: RFC3339 with offset, a naive 'YYYY-MM-DDTHH:MM:SS' or a "
                "'YYYY-MM-DD' (midnight), the last two read in time_zone. To test one chosen slot, "
                "pass its start here."
            ),
        ],
        time_max: Annotated[
            str,
            (
                "End of the window, in the same formats as time_min; must be after it. To test one "
                "chosen slot, pass its end here."
            ),
        ],
        time_zone: Annotated[
            str | None,
            (
                "IANA time zone name, e.g. 'Europe/Berlin', used to read naive time_min/time_max, to "
                "apply working hours and for every time returned; defaults to the account's calendar "
                "time zone."
            ),
        ] = None,
        slot_duration_minutes: Annotated[
            int, "Length of each suggested slot in minutes, 5 to 480."
        ] = 30,
        granularity_minutes: Annotated[
            int, "Minutes between candidate start times inside a free period, 5 to 240."
        ] = 15,
        max_results: Annotated[int, "Maximum slots to suggest, 1 to 100, earliest first."] = 10,
        working_hours_start: Annotated[
            str, "Earliest time of day a suggested slot may start, HH:MM in 24-hour format."
        ] = "08:00",
        working_hours_end: Annotated[
            str,
            "Latest time of day a suggested slot may end, HH:MM in 24-hour format; must be after working_hours_start.",
        ] = "17:00",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Check the free/busy times of up to 50 calendars or people over one window. To test a chosen time, pass it as the window and read all_free; to find meeting times, read suggested_slots, then book with calendar_create_event. Returns all_free (true only when every calendar was read and is free for the whole window), calendars in request order (id, free, null when unreadable, busy periods, errors such as notFound), suggested_slots (start/end within working hours when every readable calendar is free; unreadable ones count as free), total_suggestions and count (both the number of suggested_slots), timezone (the zone of every time) and the window and slot settings used."""
        request = FindFreeTimeRequest(
            participants=participants,
            time_min=time_min,
            time_max=time_max,
            time_zone=time_zone,
            slot_duration_minutes=slot_duration_minutes,
            granularity_minutes=granularity_minutes,
            max_results=max_results,
            working_hours_start=working_hours_start,
            working_hours_end=working_hours_end,
        )
        query_timezone = request.time_zone or await resolve_user_timezone()
        zone = pytz.timezone(query_timezone)
        fixed_min = _validate_and_fix_datetime(request.time_min, query_timezone)
        fixed_max = _validate_and_fix_datetime(request.time_max, query_timezone)
        if not fixed_min or not fixed_max:
            raise ValueError("time_min and time_max are required.")
        # Slots and working hours are computed in the result zone, whatever
        # offset the caller wrote the window in.
        window_start = _parse_rfc3339_datetime(fixed_min).astimezone(zone)
        window_end = _parse_rfc3339_datetime(fixed_max).astimezone(zone)
        if window_end <= window_start:
            raise ValueError("time_max must be greater than time_min.")

        service = build_calendar_service()
        LOGGER.debug(f"Checking free/busy for {len(request.participants)} participant(s).")
        response = await execute_google_request(
            service.freebusy().query(
                body={
                    "timeMin": fixed_min,
                    "timeMax": fixed_max,
                    "timeZone": query_timezone,
                    "items": [{"id": participant} for participant in request.participants],
                }
            )
        )
        availability = free_busy(
            response,
            requested_ids=request.participants,
            account_timezone=query_timezone,
        )

        all_busy_ranges: list[tuple[datetime, datetime]] = []
        raw_calendars = response.get("calendars", {})
        for participant in request.participants:
            for busy in raw_calendars.get(participant, {}).get("busy", []):
                start_raw = busy.get("start")
                end_raw = busy.get("end")
                if not start_raw or not end_raw:
                    continue
                busy_start = _parse_rfc3339_datetime(start_raw).astimezone(zone)
                busy_end = _parse_rfc3339_datetime(end_raw).astimezone(zone)
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

        suggestions = _build_slot_candidates(
            _apply_working_hours(
                free_ranges,
                request.working_hours_start,
                request.working_hours_end,
                zone,
            ),
            request.slot_duration_minutes,
            request.granularity_minutes,
            request.max_results,
        )

        return {
            "all_free": bool(availability["all_free"]),
            "calendars": list(availability["calendars"]),
            "suggested_slots": suggestions,
            "total_suggestions": len(suggestions),
            "count": len(suggestions),
            "timezone": query_timezone,
            "time_min": window_start.isoformat(),
            "time_max": window_end.isoformat(),
            "slot_duration_minutes": request.slot_duration_minutes,
            "granularity_minutes": request.granularity_minutes,
            "working_hours_start": request.working_hours_start,
            "working_hours_end": request.working_hours_end,
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
        add_meet_link: Annotated[
            bool,
            "True creates a new Google Meet link for the event, returned as the event's meeting_url.",
        ] = False,
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
        reminder_minutes: Annotated[
            list[int] | None,
            (
                "Popup reminders, in minutes before the start (each 0 to 40320, at most 5), e.g. "
                "[10, 60]. An empty list sets no reminders; omit it to use the calendar's default "
                "reminders."
            ),
        ] = None,
        reminders: Annotated[
            list[EventReminderInput] | None,
            (
                "Reminders with their method, at most 5, each {minutes (0 to 40320, minutes before the "
                "start), method ('popup' or 'email', an email to the calendar's owner)}, e.g. "
                "[{\"minutes\": 1440, \"method\": \"email\"}, {\"minutes\": 10, \"method\": \"popup\"}]. An "
                "empty list sets no reminders; omit it to use the calendar's default reminders. Use "
                "instead of reminder_minutes; passing both is an error."
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
        """Create an event on one calendar, optionally with guests, a Meet link, reminders, attachments or a recurrence rule. The time is first checked against that calendar's busy times only, and an overlap fails with a conflict error that creates nothing; to plan around other people's calendars use calendar_find_free_time first. Returns status 'ok', event (id, title, start and end in the event's timezone, url, meeting_url, attendees and the other calendar_read_events fields), deduplicated (true when idempotency_key matched an existing event) and warnings, non-empty when the conflict check could not run. An event with guests asks the user to confirm first; they are emailed only as send_updates says."""
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
            add_meet_link=add_meet_link,
            attendees=attendees,
            attachments=attachments,
            supports_attachments=supports_attachments,
            send_updates=send_updates,
            reminder_minutes=reminder_minutes,
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
        if request.add_meet_link:
            body["conferenceData"] = _meet_conference_request(request.idempotency_key)
        if request.attendees:
            body["attendees"] = [item.model_dump(by_alias=True, exclude_none=True) for item in request.attendees]
        if request.attachments is not None:
            body["attachments"] = [a.to_api() for a in request.attachments]
        if (reminders_body := _reminders_body(request)) is not None:
            body["reminders"] = reminders_body
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
        add_meet_link: Annotated[
            bool,
            (
                "True creates a new Google Meet link for the event, replacing any conference it has; "
                "false leaves its conferencing unchanged."
            ),
        ] = False,
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
                "icon_link, file_id}; it replaces the old list. To add or remove single files use "
                "calendar_set_event_attachments."
            ),
        ] = None,
        supports_attachments: Annotated[
            bool,
            "Tells Google this call handles event attachments (its supportsAttachments flag); leave true, attachments are only changed when it is true.",
        ] = True,
        reminder_minutes: Annotated[
            list[int] | None,
            (
                "New popup reminders, in minutes before the start (each 0 to 40320, at most 5), "
                "replacing the event's reminders, e.g. [10, 60]. An empty list removes all "
                "reminders; omit it to leave them unchanged."
            ),
        ] = None,
        reminders: Annotated[
            list[EventReminderInput] | None,
            (
                "New reminders with their method, replacing the event's reminders, at most 5, each "
                "{minutes (0 to 40320, minutes before the start), method ('popup' or 'email', an email "
                "to the calendar's owner)}, e.g. [{\"minutes\": 1440, \"method\": \"email\"}]. An empty "
                "list removes all reminders; omit it to leave them unchanged. Use instead of "
                "reminder_minutes; passing both is an error."
            ),
        ] = None,
        recurrence: Annotated[
            list[str] | None,
            (
                "New RFC 5545 recurrence lines replacing the old ones, e.g. "
                "['RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=10']. Passing it, even as an empty list, asks the "
                "user to confirm first, because it changes every occurrence."
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
        """Change an existing event: only the fields you pass change, but attendees, attachments and recurrence each replace the whole existing list. When both start_datetime and end_datetime are given, the new time is checked against the calendar's other events and an overlap fails with a conflict error that changes nothing. Changing the guest list or recurrence, or a send_updates that emails guests, asks the user to confirm first. Returns status 'ok', event (the updated event in the calendar_read_events shape, times in timezone or else the account's zone) and warnings, non-empty when the conflict check could not run; guests are notified as send_updates says."""
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
            add_meet_link=add_meet_link,
            attendees=attendees,
            attachments=attachments,
            supports_attachments=supports_attachments,
            reminder_minutes=reminder_minutes,
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
        if (reminders_body := _reminders_body(request)) is not None:
            patch_data["reminders"] = reminders_body
        if request.add_meet_link:
            patch_data["conferenceData"] = _meet_conference_request(None)
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
        send_updates: SendUpdates | None = "all",
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

    @server.tool(name="set_event_attachments")
    async def set_event_attachments(
        event_id: str,
        calendar_id: str = "primary",
        add: Annotated[
            list[EventAttachmentInput] | None,
            (
                "Files to attach, each {file_url, title, mime_type, icon_link, file_id}; a file whose "
                "file_url is already attached is skipped. Google allows at most 25 attachments per event."
            ),
        ] = None,
        remove: Annotated[
            list[EventAttachmentSelector] | None,
            (
                "Attachments to detach, each {file_url, file_id} with at least one of the two, as listed "
                "by calendar_read_events; an attachment matching either is removed. Removals apply "
                "before additions, and the files stay in Drive."
            ),
        ] = None,
        send_updates: SendUpdates | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Add files to and remove files from an existing event's attachments in one change, keeping the others; give add, remove or both. To replace the whole list use calendar_update_event, and to save an attached file use drive_download_file with its file_id. Returns status 'ok', event (the updated event in the calendar_read_events shape), added_count and removed_count (0 when nothing matched). Removing attachments, or a send_updates that emails guests, asks the user to confirm first; declining returns status 'cancelled' with nothing changed."""
        request = SetEventAttachmentsRequest(
            calendar_id=calendar_id,
            event_id=event_id,
            add=add,
            remove=remove,
            send_updates=send_updates,
        )
        service = build_calendar_service()
        LOGGER.debug(f"Setting attachments on event {request.event_id}.")
        event = await execute_google_request(
            service.events()
            .get(calendarId=request.calendar_id, eventId=request.event_id)
        )
        existing = list(event.get("attachments", []))
        remove_urls = {item.file_url for item in request.remove or [] if item.file_url}
        remove_ids = {item.file_id for item in request.remove or [] if item.file_id}
        attachments: list[dict[str, Any]] = []
        for item in existing:
            if not isinstance(item, dict):
                continue
            # Match the file_id calendar_read_events shows, which falls back
            # to the ID in a Drive URL when the attachment has no fileId.
            item_file_id = item.get("fileId") or drive_file_id_from_url(item.get("fileUrl"))
            if item.get("fileUrl") in remove_urls or item_file_id in remove_ids:
                continue
            attachments.append(item)
        removed_count = len(existing) - len(attachments)
        added_count = 0
        for attachment in request.add or []:
            candidate = attachment.to_api()
            if any(item.get("fileUrl") == candidate["fileUrl"] for item in attachments):
                continue
            attachments.append(candidate)
            added_count += 1
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
            "added_count": added_count,
            "removed_count": removed_count,
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
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Delete an event from a calendar. The user is asked to confirm first, and declining returns status 'cancelled' with nothing deleted. Returns status 'ok' and event_id once the event is deleted. Guests are notified of the cancellation as send_updates says."""
        request = DeleteEventRequest(
            calendar_id=calendar_id,
            event_id=event_id,
            send_updates=send_updates,
        )
        service = build_calendar_service()
        await execute_google_request(
            service.events().delete(
                calendarId=request.calendar_id,
                eventId=request.event_id,
                sendUpdates=request.send_updates,
            )
        )
        return {"status": "ok", "event_id": request.event_id}
