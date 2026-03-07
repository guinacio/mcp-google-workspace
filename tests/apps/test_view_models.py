from __future__ import annotations

from datetime import date

from mcp_google_workspace.apps.schemas import DashboardState
from mcp_google_workspace.apps.view_models import (
    build_dashboard_view_model,
    build_email_detail_view_model,
    build_event_detail_view_model,
    build_weekly_calendar_view_model,
)


def test_dashboard_view_model_includes_required_sections():
    state = DashboardState(
        session_id="vm-test", anchor_date=date(2026, 3, 1), view="week"
    )
    events = [
        {
            "id": "evt-1",
            "summary": "Team Sync",
            "start": {"dateTime": "2026-03-01T09:00:00+00:00"},
            "end": {"dateTime": "2026-03-01T09:30:00+00:00"},
        }
    ]
    inbox_messages = [{"id": "msg-1", "subject": "Hello", "from": "a@example.com"}]

    model = build_dashboard_view_model(
        state=state,
        calendar_events=events,
        unread_count=3,
        inbox_messages=inbox_messages,
    )

    section_ids = [section.id for section in model.sections]
    assert "schedule" in section_ids
    assert "communications" in section_ids
    assert model.sections[0].cards


def test_weekly_calendar_view_groups_events_by_day():
    events = [
        {
            "id": "evt-all-day",
            "summary": "Company Holiday",
            "start": {"date": "2026-03-02"},
            "end": {"date": "2026-03-03"},
            "calendar_id": "primary",
        },
        {
            "id": "evt-timed",
            "summary": "Design Review",
            "start": {"dateTime": "2026-03-03T14:00:00+00:00"},
            "end": {"dateTime": "2026-03-03T15:00:00+00:00"},
            "calendar_id": "primary",
        },
    ]
    weekly = build_weekly_calendar_view_model(
        anchor_date=date(2026, 3, 3),
        timezone_name="UTC",
        events=events,
        include_weekend=True,
    )
    assert weekly.week_start == date(2026, 3, 1)
    assert len(weekly.days) == 7
    tuesday = next(day for day in weekly.days if day.date == date(2026, 3, 3))
    monday = next(day for day in weekly.days if day.date == date(2026, 3, 2))
    assert len(monday.all_day_events) == 1
    assert len(tuesday.timed_events) == 1


def test_event_detail_includes_self_rsvp_status():
    event = {
        "id": "evt-42",
        "summary": "Planning",
        "start": {"dateTime": "2026-03-03T14:00:00+00:00"},
        "end": {"dateTime": "2026-03-03T15:00:00+00:00"},
        "attendees": [
            {"email": "me@example.com", "self": True, "responseStatus": "tentative"},
            {"email": "peer@example.com", "responseStatus": "accepted"},
        ],
    }
    detail = build_event_detail_view_model(event, "primary")
    assert detail.self_response_status == "tentative"


def test_email_detail_includes_attachment_metadata():
    message = {
        "id": "msg-1",
        "threadId": "thr-1",
        "snippet": "Attachment inside",
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Report"},
                {"name": "From", "value": "team@example.com"},
            ],
            "parts": [
                {
                    "mimeType": "application/pdf",
                    "filename": "report.pdf",
                    "body": {"attachmentId": "att-123", "size": 1024},
                }
            ],
        },
    }
    detail = build_email_detail_view_model(message)
    assert detail.is_unread is True
    assert len(detail.attachments) == 1
    assert detail.attachments[0]["attachment_id"] == "att-123"


def test_dashboard_inbox_unread_infers_from_labels():
    state = DashboardState(
        session_id="vm-test", anchor_date=date(2026, 3, 1), view="week"
    )
    model = build_dashboard_view_model(
        state=state,
        calendar_events=[],
        unread_count=1,
        inbox_messages=[
            {
                "id": "msg-2",
                "subject": "Unread by label",
                "from": "noreply@example.com",
                "label_ids": ["INBOX", "UNREAD"],
                "is_unread": False,
            }
        ],
    )
    communications = next(
        section for section in model.sections if section.id == "communications"
    )
    inbox = next(card for card in communications.cards if card.card_type == "inbox")
    messages = inbox.data["messages"]
    assert isinstance(messages, list)
    assert messages[0]["is_unread"] is True


def test_dashboard_inbox_unread_infers_from_unread_ids():
    state = DashboardState(
        session_id="vm-test", anchor_date=date(2026, 3, 1), view="week"
    )
    model = build_dashboard_view_model(
        state=state,
        calendar_events=[],
        unread_count=1,
        inbox_messages=[
            {
                "id": "msg-3",
                "subject": "Unread by id list",
                "from": "noreply@example.com",
                "label_ids": ["INBOX"],
                "is_unread": False,
            }
        ],
        unread_message_ids=["msg-3"],
    )
    communications = next(
        section for section in model.sections if section.id == "communications"
    )
    inbox = next(card for card in communications.cards if card.card_type == "inbox")
    messages = inbox.data["messages"]
    assert isinstance(messages, list)
    assert messages[0]["is_unread"] is True


def test_weekly_calendar_hides_weekend_as_monday_through_friday():
    weekly = build_weekly_calendar_view_model(
        anchor_date=date(2026, 3, 4),
        timezone_name="UTC",
        events=[],
        include_weekend=False,
    )

    assert weekly.week_start == date(2026, 3, 2)
    assert weekly.week_end == date(2026, 3, 6)
    assert [day.date for day in weekly.days] == [
        date(2026, 3, 2),
        date(2026, 3, 3),
        date(2026, 3, 4),
        date(2026, 3, 5),
        date(2026, 3, 6),
    ]
