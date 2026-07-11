from __future__ import annotations

import pytest
from pydantic import ValidationError

from mcp_google_workspace.apps.schemas import FindMeetingSlotsRequest
from mcp_google_workspace.calendar.schemas import ListEventsRequest
from mcp_google_workspace.drive.schemas import CreateFolderRequest
from mcp_google_workspace.gmail.schemas import SearchEmailRequest


def test_request_models_require_native_json_objects_and_schema_field_names() -> None:
    with pytest.raises(ValidationError):
        ListEventsRequest.model_validate(
            '{"calendar_id":"primary","max_results":10}'
        )
    with pytest.raises(ValidationError):
        ListEventsRequest.model_validate({"calendarId": "primary"})


def test_collection_fields_reject_string_coercions() -> None:
    with pytest.raises(ValidationError):
        CreateFolderRequest(name="Specs", parent_ids='["parentA"]')
    with pytest.raises(ValidationError):
        SearchEmailRequest(query="alerts", label_ids="INBOX,UNREAD")


def test_unknown_legacy_aliases_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        FindMeetingSlotsRequest.model_validate({
            "participants": ["primary"],
            "time_min": "2026-03-02T12:00:00Z",
            "time_max": "2026-03-02T20:00:00Z",
            "meeting_duration": 45,
        })
