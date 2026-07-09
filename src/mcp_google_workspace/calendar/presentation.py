"""Compact Calendar event representations for scheduling decisions."""

from __future__ import annotations

from typing import Any


def _when(value: dict[str, Any] | None) -> str | None:
    value = value or {}
    return value.get("dateTime") or value.get("date")


def event_envelope(event: dict[str, Any]) -> dict[str, Any]:
    attendees = event.get("attendees", [])
    mine = next((attendee for attendee in attendees if attendee.get("self")), {})
    entry_points = event.get("conferenceData", {}).get("entryPoints", [])
    meeting_url = event.get("hangoutLink") or next(
        (entry.get("uri") for entry in entry_points if entry.get("entryPointType") in {"video", "more"}), None
    )
    organizer = event.get("organizer", {})
    return {
        "id": event.get("id"),
        "title": event.get("summary") or "(untitled event)",
        "start": _when(event.get("start")),
        "end": _when(event.get("end")),
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
