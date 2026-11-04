"""Compact Calendar event representations for scheduling decisions."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import pytz

from ..common.resources import clean_web_url
from ..common.timezone import in_account_timezone


def _when(value: dict[str, Any] | None, *, account_timezone: str) -> str | None:
    value = value or {}
    if date_value := value.get("date"):
        return date_value
    date_time = value.get("dateTime")
    if not date_time:
        return None
    parsed = datetime.fromisoformat(date_time.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Calendar API returned a timed event without a timezone offset.")
    return parsed.astimezone(pytz.timezone(account_timezone)).isoformat()


def event_envelope(event: dict[str, Any], *, account_timezone: str) -> dict[str, Any]:
    attendees = event.get("attendees", [])
    mine: dict[str, Any] = next(
        (attendee for attendee in attendees if attendee.get("self")), {}
    )
    entry_points = event.get("conferenceData", {}).get("entryPoints", [])
    meeting_url = event.get("hangoutLink") or next(
        (entry.get("uri") for entry in entry_points if entry.get("entryPointType") in {"video", "more"}), None
    )
    organizer = event.get("organizer", {})
    scheduled_zone = (event.get("start") or {}).get("timeZone")
    return {
        "id": event.get("id"),
        "title": event.get("summary") or "(untitled event)",
        "start": _when(event.get("start"), account_timezone=account_timezone),
        "end": _when(event.get("end"), account_timezone=account_timezone),
        # The zone the event was scheduled in when it differs from the
        # account's (a flight booked in another city); start and end are
        # always converted to the account zone.
        "event_timezone": scheduled_zone if scheduled_zone != account_timezone else None,
        "all_day": "date" in event.get("start", {}),
        "status": event.get("status"),
        "organizer": {"name": organizer.get("displayName"), "email": organizer.get("email")},
        "attendee_count": len(attendees),
        "my_response": mine.get("responseStatus"),
        "requires_response": bool(mine and mine.get("responseStatus") == "needsAction"),
        "meeting_url": meeting_url,
        "location": event.get("location"),
        "is_recurring": bool(event.get("recurringEventId") or event.get("recurrence")),
        "updated_at": in_account_timezone(event.get("updated"), account_timezone),
    }


def _attendee(attendee: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": attendee.get("displayName"),
        "email": attendee.get("email"),
        "response_status": attendee.get("responseStatus"),
        "optional": bool(attendee.get("optional", False)),
        "organizer": bool(attendee.get("organizer", False)),
        "is_self": bool(attendee.get("self", False)),
        "is_resource": bool(attendee.get("resource", False)),
    }


_DRIVE_HOSTS = frozenset({"drive.google.com", "docs.google.com"})
_DRIVE_PATH_ID = re.compile(r"/d/([a-zA-Z0-9_-]+)")
_DRIVE_QUERY_ID = re.compile(r"[?&]id=([a-zA-Z0-9_-]+)")


def drive_file_id_from_url(file_url: str | None) -> str | None:
    """The Drive file ID in a Drive, Docs, Sheets or Slides URL, if it has one."""
    if not file_url:
        return None
    if (urlparse(file_url).hostname or "").lower() not in _DRIVE_HOSTS:
        return None
    match = _DRIVE_PATH_ID.search(file_url) or _DRIVE_QUERY_ID.search(file_url)
    return match.group(1) if match else None


def _attachment(attachment: dict[str, Any]) -> dict[str, Any]:
    file_url = attachment.get("fileUrl")
    return {
        "title": attachment.get("title"),
        "file_url": file_url,
        # Attachments added by URL alone carry no fileId; the ID in a Drive
        # URL is the same file, and is what drive_download_file takes.
        "file_id": attachment.get("fileId") or drive_file_id_from_url(file_url),
        "mime_type": attachment.get("mimeType"),
    }


def event_detail(event: dict[str, Any], *, account_timezone: str) -> dict[str, Any]:
    """The compact envelope plus what reading or changing one event needs."""
    envelope = event_envelope(event, account_timezone=account_timezone)
    return {
        **envelope,
        "url": clean_web_url(event.get("htmlLink")),
        "description": event.get("description"),
        "attendees": [_attendee(attendee) for attendee in event.get("attendees", [])],
        "attachments": [_attachment(attachment) for attachment in event.get("attachments", [])],
        "recurrence": event.get("recurrence", []),
        "recurring_event_id": event.get("recurringEventId"),
        "visibility": event.get("visibility"),
        "transparency": event.get("transparency"),
    }


def calendar_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """One calendarList entry: what selecting a calendar_id needs."""
    return {
        "id": entry.get("id"),
        "name": entry.get("summaryOverride") or entry.get("summary"),
        "description": entry.get("description") or None,
        "primary": bool(entry.get("primary", False)),
        "access_role": entry.get("accessRole"),
        "time_zone": entry.get("timeZone"),
        "hidden": bool(entry.get("hidden", False)),
    }


def free_busy(
    response: dict[str, Any], *, requested_ids: list[str], account_timezone: str
) -> dict[str, Any]:
    """Google's free/busy answer as one entry per requested calendar, in request order."""
    calendars = response.get("calendars", {})
    entries = []
    for calendar_id in requested_ids:
        details = calendars.get(calendar_id, {})
        busy = [
            {
                "start": in_account_timezone(period.get("start"), account_timezone),
                "end": in_account_timezone(period.get("end"), account_timezone),
            }
            for period in details.get("busy", [])
        ]
        errors = [str(error.get("reason") or error) for error in details.get("errors", [])]
        entries.append(
            {
                "id": calendar_id,
                # Unknown when Google could not read the calendar.
                "free": None if errors else not busy,
                "busy": busy,
                "errors": errors,
            }
        )
    return {
        "time_min": in_account_timezone(response.get("timeMin"), account_timezone),
        "time_max": in_account_timezone(response.get("timeMax"), account_timezone),
        "all_free": all(entry["free"] is True for entry in entries),
        "calendars": entries,
        "account_timezone": account_timezone,
    }


def calendars_page(payload: dict[str, Any]) -> dict[str, Any]:
    """calendarList.list as one page of calendar entries."""
    calendars = [calendar_entry(entry) for entry in payload.get("items", [])]
    return {
        "calendars": calendars,
        "count": len(calendars),
        "next_page_token": payload.get("nextPageToken"),
    }
