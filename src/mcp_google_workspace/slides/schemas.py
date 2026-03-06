"""Pydantic models for Slides tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ..common.request_model import ToolRequestModel


class GetPresentationRequest(ToolRequestModel):
    presentation_id: str = Field(description="Google Slides presentation file ID.")


class CreatePresentationRequest(ToolRequestModel):
    title: str = Field(description="Presentation title.")


class ReplaceTextInPresentationRequest(ToolRequestModel):
    presentation_id: str = Field(description="Google Slides presentation file ID.")
    contains_text: str = Field(description="Literal text to match.")
    replace_text: str = Field(description="Replacement text.")
    match_case: bool = Field(default=False)


class GetSlidePageRequest(ToolRequestModel):
    presentation_id: str = Field(description="Google Slides presentation file ID.")
    page_object_id: str = Field(description="Slide page object ID.")


class GetSlideThumbnailRequest(ToolRequestModel):
    presentation_id: str = Field(description="Google Slides presentation file ID.")
    page_object_id: str = Field(description="Slide page object ID.")
    mime_type: Literal["PNG", "JPEG"] = Field(default="PNG")
    thumbnail_size: Literal["THUMBNAIL_SIZE_UNSPECIFIED", "LARGE", "MEDIUM", "SMALL"] = Field(default="LARGE")


class BatchUpdatePresentationRequest(ToolRequestModel):
    presentation_id: str = Field(description="Google Slides presentation file ID.")
    requests: list[dict[str, Any]] = Field(description="Raw Slides batchUpdate requests.")
