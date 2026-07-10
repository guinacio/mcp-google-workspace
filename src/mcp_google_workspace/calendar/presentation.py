"""Compact Calendar event representations for scheduling decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytz


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
    return {
        "id": event.get("id"),
        "title": event.get("summary") or "(untitled event)",
        "start": _when(event.get("start"), account_timezone=account_timezone),
        "end": _when(event.get("end"), account_timezone=account_timezone),
        "timezone": None if "date" in event.get("start", {}) else account_timezone,
        "source_start": (event.get("start") or {}).get("dateTime") or (event.get("start") or {}).get("date"),
        "source_end": (event.get("end") or {}).get("dateTime") or (event.get("end") or {}).get("date"),
        "all_day": "date" in event.get("start", {}),
        "status": event.get("status"),
        "organizer": {"name": organizer.get("displayName"), "email": organizer.get("email")},
        "attendee_count": len(attendees),
        "my_response": mine.get("responseStatus"),
        "requires_response": bool(mine and mine.get("responseStatus") == "needsAction"),
        "meeting_url": meeting_url,
        "location": event.get("location"),
        "is_recurring": bool(event.get("recurringEventId") or event.get("recurrence")),
        "updated_at": event.get("updated"),
    }
