"""FastMCP calendar tools migrated from raw MCP SDK style."""

from __future__ import annotations

import io
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytz
from fastmcp import Context, FastMCP
from googleapiclient.http import MediaIoBaseDownload

from ..auth import build_calendar_service, build_drive_service
from .schemas import (
    AddEventAttachmentRequest,
    CreateEventRequest,
    DeleteEventRequest,
    DownloadEventAttachmentRequest,
    FindCommonFreeSlotsRequest,
    FreeBusyRequest,
    GetEventRequest,
    ListEventAttachmentsRequest,
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


def _check_time_slot_conflicts(
    service: Any,
    calendar_id: str,
    start_time: str,
    end_time: str,
) -> dict[str, Any]:
    try:
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
    tz = pytz.timezone(timezone_name)
    now = datetime.now(tz)
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
    @server.tool(name="get_events")
    async def get_events(request: ListEventsRequest, ctx: Context) -> dict[str, Any]:
        """List calendar events for a time window.

        Input must be an object shaped like:
        {"calendar_id":"primary","time_min":"2026-03-01T00:00:00Z","time_max":"2026-03-08T00:00:00Z"}
        """
        service = build_calendar_service()
        await ctx.info(f"Listing events for calendar {request.calendar_id}.")
        user_timezone = service.settings().get(setting="timezone").execute().get("value", "UTC")
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
        result = (
            service.events()
            .list(**list_kwargs)
            .execute()
        )
        if request.range_preset:
            result["range_preset"] = request.range_preset
            result["effective_time_min"] = effective_time_min
            result["effective_time_max"] = effective_time_max
        return result

    @server.tool(name="get_event")
    async def get_event(request: GetEventRequest, ctx: Context) -> dict[str, Any]:
        """Fetch a single event by ID for deterministic state verification."""
        service = build_calendar_service()
        await ctx.info(f"Reading event {request.event_id}.")
        event = (
            service.events()
            .get(
                calendarId=request.calendar_id,
                eventId=request.event_id,
                timeZone=request.time_zone,
                maxAttendees=request.max_attendees,
            )
            .execute()
        )
        return {"event": event}

    @server.tool(name="list_calendars")
    async def list_calendars() -> dict[str, Any]:
        """List calendars visible to the authenticated account."""
        service = build_calendar_service()
        return service.calendarList().list().execute()

    @server.tool(name="get_timezone_info")
    async def get_timezone_info() -> dict[str, Any]:
        """Return user's calendar timezone and current localized time details."""
        service = build_calendar_service()
        settings = service.settings().get(setting="timezone").execute()
        user_tz = settings.get("value", "UTC")
        now_utc = datetime.now(pytz.UTC)
        now_local = now_utc.astimezone(pytz.timezone(user_tz))
        return {
            "timezone": user_tz,
            "current_utc_time": now_utc.isoformat(),
            "current_local_time": now_local.isoformat(),
            "utc_offset": now_local.strftime("%z"),
            "timezone_name": now_local.tzname(),
        }

    @server.tool(name="get_current_date")
    async def get_current_date() -> dict[str, Any]:
        """Return current date/time using the account's calendar timezone."""
        service = build_calendar_service()
        settings = service.settings().get(setting="timezone").execute()
        user_tz = settings.get("value", "UTC")
        now_utc = datetime.now(pytz.UTC)
        now_local = now_utc.astimezone(pytz.timezone(user_tz))
        return {
            "current_date": now_local.strftime("%Y-%m-%d"),
            "current_time": now_local.strftime("%H:%M:%S"),
            "timezone": user_tz,
            "day_of_week": now_local.strftime("%A"),
            "utc_datetime": now_utc.isoformat(),
        }

    @server.tool(name="check_availability")
    async def check_availability(request: FreeBusyRequest) -> dict[str, Any]:
        """Run a FreeBusy query for one or more calendars."""
        service = build_calendar_service()
        return service.freebusy().query(body=request.model_dump(exclude_none=True)).execute()

    @server.tool(name="find_common_free_slots")
    async def find_common_free_slots(
        request: FindCommonFreeSlotsRequest,
        ctx: Context,
    ) -> dict[str, Any]:
        """Suggest common available meeting slots for all participants in a time window."""
        service = build_calendar_service()
        if not request.participants:
            raise ValueError("participants list cannot be empty.")

        query_timezone = request.time_zone or "UTC"
        fixed_min = _validate_and_fix_datetime(request.time_min, query_timezone)
        fixed_max = _validate_and_fix_datetime(request.time_max, query_timezone)
        if not fixed_min or not fixed_max:
            raise ValueError("time_min and time_max are required.")

        window_start = _parse_rfc3339_datetime(fixed_min)
        window_end = _parse_rfc3339_datetime(fixed_max)
        if window_end <= window_start:
            raise ValueError("time_max must be greater than time_min.")

        await ctx.info(f"Checking common availability for {len(request.participants)} participant(s).")
        body: dict[str, Any] = {
            "timeMin": fixed_min,
            "timeMax": fixed_max,
            "items": [{"id": participant} for participant in request.participants],
        }
        if request.time_zone:
            body["timeZone"] = request.time_zone
        freebusy_result = service.freebusy().query(body=body).execute()

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
            "total_suggestions": len(suggestions),
            "suggested_slots": suggestions,
            "calendar_errors": calendar_errors,
        }

    @server.tool(name="create_event")
    async def create_event(request: CreateEventRequest, ctx: Context) -> dict[str, Any]:
        """Create a calendar event with optional attendees, reminders, and recurrence."""
        service = build_calendar_service()
        timezone = request.timezone or "UTC"
        start = _validate_and_fix_datetime(request.start_datetime, timezone)
        end = _validate_and_fix_datetime(request.end_datetime, timezone)
        body: dict[str, Any] = {
            "summary": request.summary,
            "start": {"dateTime": start, "timeZone": timezone},
            "end": {"dateTime": end, "timeZone": timezone},
        }
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
            body["attendees"] = request.attendees
        if request.attachments is not None:
            body["attachments"] = [a.to_api() for a in request.attachments]
        if request.reminders:
            body["reminders"] = request.reminders
        if request.recurrence:
            body["recurrence"] = request.recurrence
        conflict_check = _check_time_slot_conflicts(
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
                response["suggested_slots"] = _suggest_next_available_slots(
                    service=service,
                    calendar_id=request.calendar_id,
                    requested_start=start,
                    requested_end=end,
                )
            return response
        await ctx.info(f"Creating event '{request.summary}'.")
        event = (
            service.events()
            .insert(
                calendarId=request.calendar_id,
                body=body,
                sendUpdates=request.send_updates,
                supportsAttachments=request.supports_attachments,
                conferenceDataVersion=1 if body.get("conferenceData") else 0,
            )
            .execute()
        )
        return {"success": True, "event": event}

    @server.tool(name="update_event")
    async def update_event(request: UpdateEventRequest, ctx: Context) -> dict[str, Any]:
        """Patch an existing calendar event with the provided fields."""
        service = build_calendar_service()
        timezone = request.timezone or "UTC"
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
                patch_data[key] = value
        if request.color_id is not None:
            patch_data["colorId"] = request.color_id
        if request.conference_data is not None:
            patch_data["conferenceData"] = request.conference_data
        if request.attachments is not None:
            patch_data["attachments"] = [a.to_api() for a in request.attachments]
        if request.start_datetime:
            patch_data["start"] = {
                "dateTime": _validate_and_fix_datetime(request.start_datetime, timezone),
                "timeZone": timezone,
            }
        if request.end_datetime:
            patch_data["end"] = {
                "dateTime": _validate_and_fix_datetime(request.end_datetime, timezone),
                "timeZone": timezone,
            }
        if patch_data.get("start") and patch_data.get("end"):
            conflict_check = _check_time_slot_conflicts(
                service,
                request.calendar_id,
                patch_data["start"]["dateTime"],
                patch_data["end"]["dateTime"],
            )
            if conflict_check["has_conflicts"]:
                response: dict[str, Any] = {
                    "error": "New time slot is not available - there are overlapping events",
                    "status": "CONFLICT",
                    "conflicting_events": conflict_check["conflicts"],
                    "conflict_check_error": conflict_check["error"],
                }
                if request.on_conflict == "suggest_next_slot":
                    response["suggested_slots"] = _suggest_next_available_slots(
                        service=service,
                        calendar_id=request.calendar_id,
                        requested_start=patch_data["start"]["dateTime"],
                        requested_end=patch_data["end"]["dateTime"],
                    )
                return response
        await ctx.info(f"Updating event {request.event_id}.")
        event = (
            service.events()
            .patch(
                calendarId=request.calendar_id,
                eventId=request.event_id,
                body=patch_data,
                sendUpdates=request.send_updates,
                supportsAttachments=request.supports_attachments,
                conferenceDataVersion=1 if patch_data.get("conferenceData") else 0,
            )
            .execute()
        )
        return {"success": True, "event": event}

    @server.tool(name="list_event_attachments")
    async def list_event_attachments(request: ListEventAttachmentsRequest, ctx: Context) -> dict[str, Any]:
        """List attachment metadata from a specific calendar event."""
        service = build_calendar_service()
        await ctx.info(f"Listing attachments for event {request.event_id}.")
        event = (
            service.events()
            .get(calendarId=request.calendar_id, eventId=request.event_id)
            .execute()
        )
        attachments = event.get("attachments", [])
        return {"calendar_id": request.calendar_id, "event_id": request.event_id, "attachments": attachments}

    @server.tool(name="add_event_attachment")
    async def add_event_attachment(request: AddEventAttachmentRequest, ctx: Context) -> dict[str, Any]:
        """Add a single attachment to an existing event (deduplicates by fileUrl)."""
        service = build_calendar_service()
        await ctx.info(f"Adding attachment to event {request.event_id}.")
        event = (
            service.events()
            .get(calendarId=request.calendar_id, eventId=request.event_id)
            .execute()
        )
        attachments = list(event.get("attachments", []))
        candidate = request.attachment.to_api()
        if not any(isinstance(a, dict) and a.get("fileUrl") == candidate.get("fileUrl") for a in attachments):
            attachments.append(candidate)
        updated = (
            service.events()
            .patch(
                calendarId=request.calendar_id,
                eventId=request.event_id,
                body={"attachments": attachments},
                sendUpdates=request.send_updates,
                supportsAttachments=True,
            )
            .execute()
        )
        return {"event": updated}

    @server.tool(name="remove_event_attachment")
    async def remove_event_attachment(request: RemoveEventAttachmentRequest, ctx: Context) -> dict[str, Any]:
        """Remove event attachment(s) by fileUrl or fileId."""
        service = build_calendar_service()
        if not request.file_url and not request.file_id:
            raise ValueError("Provide at least one of file_url or file_id.")
        await ctx.info(f"Removing attachment from event {request.event_id}.")
        event = (
            service.events()
            .get(calendarId=request.calendar_id, eventId=request.event_id)
            .execute()
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
        updated = (
            service.events()
            .patch(
                calendarId=request.calendar_id,
                eventId=request.event_id,
                body={"attachments": filtered},
                sendUpdates=request.send_updates,
                supportsAttachments=True,
            )
            .execute()
        )
        return {"event": updated, "removed_count": len(attachments) - len(filtered)}

    @server.tool(name="download_event_attachment")
    async def download_event_attachment(
        request: DownloadEventAttachmentRequest,
        ctx: Context,
    ) -> dict[str, Any]:
        """Download/export an event attachment to local filesystem using Drive API."""
        service = build_calendar_service()
        drive = build_drive_service()
        await ctx.info(f"Resolving attachment for event {request.event_id}.")
        event = (
            service.events()
            .get(calendarId=request.calendar_id, eventId=request.event_id)
            .execute()
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

        file_url = request.file_url or (selected.get("fileUrl") if selected else None)
        file_id = request.file_id or (selected.get("fileId") if selected else None) or _extract_drive_file_id(file_url)
        if not file_id:
            raise ValueError("Could not resolve Drive file_id from attachment metadata.")

        meta = drive.files().get(fileId=file_id, fields="id,name,mimeType").execute()
        mime_type = meta.get("mimeType", "")
        name = meta.get("name", file_id)
        out_path = Path(request.output_path)
        if out_path.exists() and not request.overwrite:
            raise FileExistsError(f"Output path already exists: {out_path}")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if mime_type.startswith("application/vnd.google-apps"):
            export_mime = request.export_mime_type or GOOGLE_EXPORT_MIME_DEFAULTS.get(mime_type)
            if not export_mime:
                raise ValueError(
                    "Google-native file requires export_mime_type for this mimeType: "
                    f"{mime_type}"
                )
            req = drive.files().export_media(fileId=file_id, mimeType=export_mime)
            mode = "export"
        else:
            req = drive.files().get_media(fileId=file_id)
            export_mime = None
            mode = "download"

        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, req)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status is not None:
                await ctx.report_progress(
                    int(status.progress() * 100),
                    100,
                    "Downloading attachment bytes",
                )

        data = buffer.getvalue()
        out_path.write_bytes(data)
        await ctx.report_progress(100, 100, "Attachment saved")
        return {
            "status": "ok",
            "calendar_id": request.calendar_id,
            "event_id": request.event_id,
            "file_id": file_id,
            "file_name": name,
            "file_mime_type": mime_type,
            "mode": mode,
            "export_mime_type": export_mime,
            "saved_to": str(out_path),
            "bytes_written": len(data),
        }

    @server.tool(name="delete_event")
    async def delete_event(request: DeleteEventRequest, ctx: Context) -> dict[str, Any]:
        """Delete a calendar event, with optional interactive confirmation."""
        if not request.force:
            response = await ctx.elicit(
                f"Delete event {request.event_id} from calendar {request.calendar_id}?",
                response_type=bool,  # type: ignore[arg-type]
            )
            if response.action != "accept" or not bool(response.data):
                return {"status": "cancelled"}
        service = build_calendar_service()
        service.events().delete(
            calendarId=request.calendar_id,
            eventId=request.event_id,
            sendUpdates=request.send_updates,
        ).execute()
        return {"success": True, "event_id": request.event_id}
