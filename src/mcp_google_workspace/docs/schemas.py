"""Pydantic models for Docs tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ..common.request_model import ToolRequestModel


SuggestionsViewMode = Literal[
    "DEFAULT_FOR_CURRENT_ACCESS",
    "SUGGESTIONS_INLINE",
    "PREVIEW_SUGGESTIONS_ACCEPTED",
    "PREVIEW_WITHOUT_SUGGESTIONS",
]


class ReadDocumentRequest(ToolRequestModel):
    document_id: str = Field(description="Google Docs document file ID.")
    format: Literal["text", "markdown", "raw"] = Field(default="text")
    offset: int = Field(default=0, ge=0)
    include_tabs_content: bool = Field(default=True)
    suggestions_view_mode: SuggestionsViewMode | None = Field(default=None)


class CreateDocumentRequest(ToolRequestModel):
    title: str = Field(description="Document title.")


class AppendDocumentTextRequest(ToolRequestModel):
    document_id: str = Field(description="Google Docs document file ID.")
    text: str = Field(description="Text appended to the end of the document.")


class ReplaceDocumentTextRequest(ToolRequestModel):
    document_id: str = Field(description="Google Docs document file ID.")
    contains_text: str = Field(description="Literal text to match.")
    replace_text: str = Field(description="Replacement text.")
    match_case: bool = Field(default=False)


class BatchUpdateDocumentRequest(ToolRequestModel):
    document_id: str = Field(description="Google Docs document file ID.")
    requests: list[dict[str, Any]] = Field(description="Raw Docs batchUpdate requests.")
    write_control: dict[str, Any] | None = Field(default=None)
