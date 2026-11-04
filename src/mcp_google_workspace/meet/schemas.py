"""Pydantic models for Meet tools."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from ..common.parameter_descriptions import SHARED_PARAMETER_DESCRIPTIONS
from ..common.request_model import ToolRequestModel

_SPACE_NAME = "Meet space resource name 'spaces/abc123' or the bare space ID, from meet_create_space."
_RECORD_NAME = (
    "Conference record resource name 'conferenceRecords/abc123' or the bare ID, from "
    "meet_list_conference_records."
)
_PAGE_SIZE = "Maximum number of results to return in one page, from 1 to 100."
_PAGE_TOKEN = SHARED_PARAMETER_DESCRIPTIONS["page_token"]


class CreateSpaceRequest(ToolRequestModel):
    config: dict[str, Any] | None = Field(
        default=None,
        description="Meet API SpaceConfig object for the new space; omit to use the account's default settings.",
    )


class GetSpaceRequest(ToolRequestModel):
    space_name: str = Field(description=_SPACE_NAME)


class UpdateSpaceRequest(ToolRequestModel):
    space_name: str = Field(description=_SPACE_NAME)
    config: dict[str, Any] = Field(description="Meet API SpaceConfig object holding the new values.")
    update_mask: str | None = Field(
        default=None,
        description="Comma-separated Space field paths to update; omit to use 'config'.",
    )


class EndActiveConferenceRequest(ToolRequestModel):
    space_name: str = Field(description=_SPACE_NAME)


class ListConferenceRecordsRequest(ToolRequestModel):
    page_size: int = Field(default=50, ge=1, le=100, description=_PAGE_SIZE)
    page_token: str | None = Field(default=None, description=_PAGE_TOKEN)
    filter: str | None = Field(default=None, description="Meet API filter on space.meeting_code, space.name, start_time or end_time; omit for no filter.")


class GetConferenceRecordRequest(ToolRequestModel):
    conference_record_name: str = Field(description=_RECORD_NAME)


class ListConferenceParticipantsRequest(ToolRequestModel):
    conference_record_name: str = Field(description=_RECORD_NAME)
    page_size: int = Field(default=50, ge=1, le=100, description=_PAGE_SIZE)
    page_token: str | None = Field(default=None, description=_PAGE_TOKEN)
    filter: str | None = Field(default=None, description="Meet API filter on earliest_start_time or latest_end_time; omit for no filter.")


class ListConferenceRecordingsRequest(ToolRequestModel):
    conference_record_name: str = Field(description=_RECORD_NAME)
    page_size: int = Field(default=50, ge=1, le=100, description=_PAGE_SIZE)
    page_token: str | None = Field(default=None, description=_PAGE_TOKEN)


class ListConferenceTranscriptsRequest(ToolRequestModel):
    conference_record_name: str = Field(description=_RECORD_NAME)
    page_size: int = Field(default=50, ge=1, le=100, description=_PAGE_SIZE)
    page_token: str | None = Field(default=None, description=_PAGE_TOKEN)
