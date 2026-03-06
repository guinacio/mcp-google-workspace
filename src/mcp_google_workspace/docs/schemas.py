"""Pydantic models for Docs tools."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from ..common.request_model import ToolRequestModel


class GetDocumentRequest(ToolRequestModel):
    document_id: str = Field(description="Google Docs document file ID.")
    include_tabs_content: bool = Field(default=False)
    suggestions_view_mode: str | None = Field(default=None)


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
