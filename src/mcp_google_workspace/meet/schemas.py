"""Pydantic models for Meet tools."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from ..common.request_model import ToolRequestModel


class CreateSpaceRequest(ToolRequestModel):
    config: dict[str, Any] | None = Field(default=None)


class GetSpaceRequest(ToolRequestModel):
    space_name: str = Field(description="Space resource name, e.g. spaces/abc.")


class UpdateSpaceRequest(ToolRequestModel):
    space_name: str = Field(description="Space resource name.")
    config: dict[str, Any] = Field(description="Replacement space config.")
    update_mask: str | None = Field(default=None)


class EndActiveConferenceRequest(ToolRequestModel):
    space_name: str = Field(description="Space resource name.")


class ListConferenceRecordsRequest(ToolRequestModel):
    page_size: int = Field(default=50, ge=1, le=100)
    page_token: str | None = Field(default=None)
    filter: str | None = Field(default=None)


class GetConferenceRecordRequest(ToolRequestModel):
    conference_record_name: str = Field(description="Conference record resource name.")


class ListConferenceParticipantsRequest(ToolRequestModel):
    conference_record_name: str = Field(description="Conference record resource name.")
    page_size: int = Field(default=50, ge=1, le=100)
    page_token: str | None = Field(default=None)
    filter: str | None = Field(default=None)


class ListConferenceRecordingsRequest(ToolRequestModel):
    conference_record_name: str = Field(description="Conference record resource name.")
    page_size: int = Field(default=50, ge=1, le=100)
    page_token: str | None = Field(default=None)


class ListConferenceTranscriptsRequest(ToolRequestModel):
    conference_record_name: str = Field(description="Conference record resource name.")
    page_size: int = Field(default=50, ge=1, le=100)
    page_token: str | None = Field(default=None)
