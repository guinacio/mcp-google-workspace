"""Pydantic models for Google Keep tools."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class ChecklistItem(BaseModel):
    text: str = Field(description="Checklist item text.")
    checked: bool = Field(default=False, description="Whether item is checked.")


class ToolRequestModel(BaseModel):
    """Base input model for MCP tools expecting object payloads."""

    model_config = {
        "json_schema_extra": {
            "description": (
                "Pass this as a JSON object payload to the tool. "
                "Do not pass a raw string for the full request."
            )
        }
    }


class CreateNoteRequest(ToolRequestModel):
    title: str | None = Field(default=None, description="Optional note title.")
    text_body: str | None = Field(default=None, description="Optional plain text body.")
    checklist_items: list[ChecklistItem] = Field(default_factory=list, description="Optional checklist items.")
    collaborator_emails: list[EmailStr] = Field(default_factory=list, description="Optional collaborators to share with.")
    confirm_create: bool = Field(default=False, description="Ask for confirmation before creating the note.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "title": "Morning priorities",
                    "text_body": "Review launch checklist and customer emails.",
                    "checklist_items": [{"text": "Review calendar", "checked": False}],
                    "collaborator_emails": [],
                    "confirm_create": False,
                }
            ]
        }
    }


class GetNoteRequest(ToolRequestModel):
    note_name: str = Field(description="Google Keep note resource name.")


class ListNotesRequest(ToolRequestModel):
    filter: str | None = Field(default=None, description="Optional filter expression.")
    page_size: int = Field(default=20, ge=1, le=100, description="Maximum notes to return.")
    page_token: str | None = Field(default=None, description="Pagination token from previous response.")


class DeleteNoteRequest(ToolRequestModel):
    note_name: str = Field(description="Google Keep note resource name.")
    confirm_delete: bool = Field(default=True, description="Ask for confirmation before deleting.")


class UpdateNoteRequest(ToolRequestModel):
    note_name: str = Field(description="Google Keep note resource name.")
    title: str | None = Field(default=None, description="Updated title.")
    text_body: str | None = Field(default=None, description="Updated plain text body.")
    checklist_items: list[ChecklistItem] = Field(default_factory=list, description="Updated checklist item set.")


class ShareNoteRequest(ToolRequestModel):
    note_name: str = Field(description="Google Keep note resource name.")
    collaborator_emails: list[EmailStr] = Field(description="Collaborator email addresses to grant access.")


class UnshareNoteRequest(ToolRequestModel):
    note_name: str = Field(description="Google Keep note resource name.")
    permission_names: list[str] = Field(description="Permission resource names to remove from the note.")
