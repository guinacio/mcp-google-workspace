"""FastMCP calendar tools migrated from raw MCP SDK style."""

from __future__ import annotations

import io
import re
from datetime import datetime
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


def register_tools(server: FastMCP) -> None:
    @server.tool(name="get_events")
    async def get_events(request: ListEventsRequest, ctx: Context) -> dict[str, Any]:
        """List calendar events for a time window and ordering preference."""
        service = build_calendar_service()
        await ctx.info(f"Listing events for calendar {request.calendar_id}.")
        list_kwargs: dict[str, Any] = {
            "calendarId": request.calendar_id,
            "timeMin": request.time_min,
            "timeMax": request.time_max,
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
        if request.attendees:
            body["attendees"] = request.attendees
        if request.attachments is not None:
            body["attachments"] = [a.to_api() for a in request.attachments]
        if request.reminders:
            body["reminders"] = request.reminders
        if request.recurrence:
            body["recurrence"] = request.recurrence
        await ctx.info(f"Creating event '{request.summary}'.")
        event = (
            service.events()
            .insert(
                calendarId=request.calendar_id,
                body=body,
                sendUpdates=request.send_updates,
                supportsAttachments=request.supports_attachments,
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
        for key in ("summary", "description", "location", "attendees", "reminders", "recurrence"):
            value = getattr(request, key)
            if value is not None:
                patch_data[key] = value
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
        await ctx.info(f"Updating event {request.event_id}.")
        event = (
            service.events()
            .patch(
                calendarId=request.calendar_id,
                eventId=request.event_id,
                body=patch_data,
                sendUpdates=request.send_updates,
                supportsAttachments=request.supports_attachments,
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
