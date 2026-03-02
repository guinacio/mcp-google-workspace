from __future__ import annotations

from mcp_google_workspace.apps.schemas import FindMeetingSlotsRequest
from mcp_google_workspace.calendar.schemas import ListEventsRequest
from mcp_google_workspace.chat.schemas import ListMessagesRequest
from mcp_google_workspace.drive.schemas import CreateFolderRequest
from mcp_google_workspace.gmail.schemas import SearchEmailRequest
from mcp_google_workspace.keep.schemas import CreateNoteRequest


def test_calendar_accepts_json_string_payload_with_camel_case_keys() -> None:
    request = ListEventsRequest.model_validate(
        '{"calendarId":"primary","timeMin":"2026-03-01T00:00:00Z","timeMax":"2026-03-08T00:00:00Z","maxResults":10}'
    )

    assert request.calendar_id == "primary"
    assert request.time_min == "2026-03-01T00:00:00Z"
    assert request.time_max == "2026-03-08T00:00:00Z"
    assert request.max_results == 10


def test_drive_accepts_json_string_for_list_field() -> None:
    request = CreateFolderRequest(
        name="Specs",
        parent_ids='["parentA", "parentB"]',
    )

    assert request.parent_ids == ["parentA", "parentB"]


def test_gmail_accepts_comma_delimited_list_field() -> None:
    request = SearchEmailRequest(
        query="from:alerts@example.com",
        label_ids="INBOX,UNREAD",
    )

    assert request.label_ids == ["INBOX", "UNREAD"]


def test_keep_accepts_json_string_for_nested_models() -> None:
    request = CreateNoteRequest(
        title="Checklist",
        checklist_items='[{"text":"Item 1","checked":true}]',
    )

    assert len(request.checklist_items) == 1
    assert request.checklist_items[0].text == "Item 1"
    assert request.checklist_items[0].checked is True


def test_chat_accepts_camel_case_payload_keys() -> None:
    request = ListMessagesRequest.model_validate(
        {
            "spaceName": "spaces/AAA",
            "pageSize": 12,
            "threadName": "spaces/AAA/threads/xyz",
        }
    )

    assert request.space_name == "spaces/AAA"
    assert request.page_size == 12
    assert request.thread_name == "spaces/AAA/threads/xyz"


def test_apps_accepts_meeting_duration_alias_and_string_participants() -> None:
    request = FindMeetingSlotsRequest.model_validate(
        {
            "participants": "primary, rodrigo@example.com, bruno@example.com",
            "timeMin": "2026-03-02T12:00:00Z",
            "timeMax": "2026-03-02T20:00:00Z",
            "meetingDuration": 45,
        }
    )

    assert request.participants == ["primary", "rodrigo@example.com", "bruno@example.com"]
    assert request.slot_duration_minutes == 45
