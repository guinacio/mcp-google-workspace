from mcp_google_workspace.calendar.tools import _validate_and_fix_datetime
from mcp_google_workspace.calendar.presentation import event_envelope


def test_validate_and_fix_datetime_adds_timezone():
    result = _validate_and_fix_datetime("2026-02-28T10:30:00", "UTC")
    assert result is not None
    assert result.endswith("+00:00")


def test_validate_and_fix_datetime_date_only():
    result = _validate_and_fix_datetime("2026-02-28", "UTC")
    assert result is not None
    assert "T00:00:00" in result


def test_event_envelope_surfaces_rsvp_and_meeting_link():
    result = event_envelope(
        {
            "id": "event-1", "summary": "Planning", "start": {"dateTime": "2026-07-10T10:00:00Z"},
            "end": {"dateTime": "2026-07-10T11:00:00Z"}, "attendees": [{"self": True, "responseStatus": "needsAction"}],
            "hangoutLink": "https://meet.google.com/abc-defg-hij",
        },
        account_timezone="America/Sao_Paulo",
    )
    assert result["requires_response"] is True
    assert result["meeting_url"].startswith("https://meet")
