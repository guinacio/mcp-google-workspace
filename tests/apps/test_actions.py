from __future__ import annotations

from mcp_google_workspace.apps.actions import (
    cancel_meeting,
    create_meeting_from_slot,
    find_meeting_slots,
    reschedule_meeting,
)
from mcp_google_workspace.apps.schemas import (
    CancelMeetingRequest,
    CreateMeetingFromSlotRequest,
    FindMeetingSlotsRequest,
    RescheduleMeetingRequest,
)


class _Exec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _EventsApi:
    def __init__(self):
        self.insert_calls = 0
        self.patch_calls = 0
        self.delete_calls = 0

    def insert(self, **kwargs):
        self.insert_calls += 1
        return _Exec({"id": "evt-created", "request": kwargs})

    def patch(self, **kwargs):
        self.patch_calls += 1
        return _Exec({"id": kwargs.get("eventId", "evt-updated"), "request": kwargs})

    def delete(self, **kwargs):
        self.delete_calls += 1
        return _Exec({"ok": True, "request": kwargs})


class _FreeBusyApi:
    def query(self, body):
        payload = {
            "calendars": {
                "primary": {
                    "busy": [
                        {"start": "2026-03-01T10:00:00+00:00", "end": "2026-03-01T10:30:00+00:00"}
                    ]
                }
            }
        }
        return _Exec(payload)


class _CalendarService:
    def __init__(self):
        self._events = _EventsApi()
        self._freebusy = _FreeBusyApi()

    def events(self):
        return self._events

    def freebusy(self):
        return self._freebusy


def test_find_meeting_slots(monkeypatch):
    service = _CalendarService()
    monkeypatch.setattr("mcp_google_workspace.apps.actions.build_calendar_service", lambda: service)

    result = find_meeting_slots(
        FindMeetingSlotsRequest(
            participants=["primary"],
            time_min="2026-03-01T09:00:00+00:00",
            time_max="2026-03-01T12:00:00+00:00",
            time_zone="UTC",
            slot_duration_minutes=30,
            granularity_minutes=15,
            max_results=5,
        )
    )

    assert result["total_suggestions"] > 0
    assert result["participants"] == ["primary"]


def test_create_meeting_from_slot_idempotent(monkeypatch):
    service = _CalendarService()
    monkeypatch.setattr("mcp_google_workspace.apps.actions.build_calendar_service", lambda: service)

    request = CreateMeetingFromSlotRequest(
        session_id="session-actions",
        calendar_id="primary",
        title="Design Review",
        start="2026-03-02T09:00:00+00:00",
        end="2026-03-02T09:30:00+00:00",
        timezone="UTC",
        idempotency_key="create-1",
    )
    first = create_meeting_from_slot("session-actions", request)
    second = create_meeting_from_slot("session-actions", request)

    assert first.status == "ok"
    assert second.event_id == first.event_id
    assert service.events().insert_calls == 1


def test_reschedule_and_cancel(monkeypatch):
    service = _CalendarService()
    monkeypatch.setattr("mcp_google_workspace.apps.actions.build_calendar_service", lambda: service)

    rescheduled = reschedule_meeting(
        "session-actions",
        RescheduleMeetingRequest(
            session_id="session-actions",
            calendar_id="primary",
            event_id="evt-1",
            start="2026-03-02T10:00:00+00:00",
            end="2026-03-02T10:30:00+00:00",
            timezone="UTC",
            idempotency_key="resched-1",
        ),
    )
    assert rescheduled.status == "ok"

    cancelled = cancel_meeting(
        "session-actions",
        CancelMeetingRequest(
            session_id="session-actions",
            calendar_id="primary",
            event_id="evt-1",
            confirm=True,
            idempotency_key="cancel-1",
        ),
    )
    assert cancelled.status == "ok"
    assert service.events().delete_calls == 1
