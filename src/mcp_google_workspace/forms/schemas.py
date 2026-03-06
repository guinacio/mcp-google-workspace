"""Pydantic models for Forms tools."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from ..common.request_model import ToolRequestModel


class GetFormRequest(ToolRequestModel):
    form_id: str = Field(description="Google Form file ID.")


class CreateFormRequest(ToolRequestModel):
    title: str = Field(description="Form title.")
    document_title: str | None = Field(default=None)
    unpublished: bool = Field(default=False)


class BatchUpdateFormRequest(ToolRequestModel):
    form_id: str = Field(description="Google Form file ID.")
    requests: list[dict[str, Any]] = Field(description="Raw Forms batchUpdate requests.")
    include_form_in_response: bool = Field(default=False)


class SetFormPublishSettingsRequest(ToolRequestModel):
    form_id: str = Field(description="Google Form file ID.")
    publish_settings: dict[str, Any] = Field(description="Publish settings payload.")
    update_mask: str = Field(default="*")


class ListFormResponsesRequest(ToolRequestModel):
    form_id: str = Field(description="Google Form file ID.")
    page_size: int = Field(default=50, ge=1, le=5000)
    page_token: str | None = Field(default=None)
    filter: str | None = Field(default=None)


class GetFormResponseRequest(ToolRequestModel):
    form_id: str = Field(description="Google Form file ID.")
    response_id: str = Field(description="Form response ID.")
