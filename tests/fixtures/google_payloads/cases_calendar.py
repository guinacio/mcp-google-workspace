"""Calendar response normalizers over recorded-shape payloads."""

from __future__ import annotations

from mcp_google_workspace.calendar.presentation import (
    calendars_page,
    event_detail,
    event_envelope,
    free_busy,
)

from . import TIMEZONE, Case

CASES = [
    Case(
        name="calendar_list_calendars",
        fixture="calendar_list_calendars.json",
        normalize=calendars_page,
        items_key="calendars",
    ),
    Case(
        name="calendar_check_time_availability",
        fixture="calendar_freebusy.json",
        normalize=lambda payload: free_busy(
            payload,
            requested_ids=["ana@example.com", "bruno@example.com", "room-4@resource.calendar.example.com"],
            account_timezone=TIMEZONE,
        ),
        items_key="calendars",
    ),
    Case(
        name="calendar_search_events",
        fixture="calendar_events_list.json",
        normalize=lambda payload: {
            "events": [event_envelope(event, account_timezone=TIMEZONE) for event in payload["items"]],
            "count": len(payload["items"]),
            "next_page_token": payload.get("nextPageToken"),
            "account_timezone": TIMEZONE,
        },
        items_key="events",
    ),
    Case(
        name="calendar_read_events",
        fixture="calendar_events_list.json",
        normalize=lambda payload: {
            "events": [event_detail(event, account_timezone=TIMEZONE) for event in payload["items"]],
            "missing_count": 0,
            "missing_event_ids": [],
            "calendar_id": "primary",
            "account_timezone": TIMEZONE,
        },
        items_key="events",
    ),
    Case(
        name="calendar_create_event",
        fixture="calendar_events_list.json",
        normalize=lambda payload: {
            "status": "ok",
            "event": event_detail(payload["items"][0], account_timezone=TIMEZONE),
            "deduplicated": False,
            "warnings": [],
        },
    ),
]
