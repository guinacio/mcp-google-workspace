"""Pydantic models for Google Keep tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from ..common.request_model import ToolRequestModel

_NOTE_NAME_DESCRIPTION = (
    "Keep note resource name 'notes/ABC123' or the bare note ID, from keep_create_note or "
    "keep_list_notes (the id field of a note)."
)


class ChecklistItem(BaseModel):
    text: str = Field(description="Text of the checklist item.")
    checked: bool = Field(default=False, description="True to create the item already ticked; false (default) for an open item.")


class CreateNoteRequest(ToolRequestModel):
    title: str | None = Field(default=None, description="Note title; omit for an untitled note.")
    text_body: str | None = Field(
        default=None,
        description="Plain-text body of the note. Ignored when checklist_items is not empty, because a note has one body.",
    )
    checklist_items: list[ChecklistItem] = Field(
        default_factory=list,
        description="Items that make the note a checklist, in order; leave empty for a plain-text note.",
    )
    collaborator_emails: list[EmailStr] = Field(
        default_factory=list,
        description="Email addresses to share the note with; each is granted writer access after the note is created.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "title": "Morning priorities",
                    "text_body": "Review launch checklist and customer emails.",
                    "checklist_items": [{"text": "Review calendar", "checked": False}],
                    "collaborator_emails": [],
                }
            ]
        }
    }


class GetNoteRequest(ToolRequestModel):
    note_name: str = Field(description=_NOTE_NAME_DESCRIPTION)


class ListNotesRequest(ToolRequestModel):
    filter: str | None = Field(
        default=None,
        description=(
            "Keep API filter on create_time, update_time, trash_time or trashed, e.g. "
            "'trashed = true'; omit for the Keep default, which lists notes that are not trashed."
        ),
    )
    page_size: int = Field(default=20, ge=1, le=100, description="Maximum number of notes to return in one page, from 1 to 100.")
    page_token: str | None = Field(
        default=None,
        description="The next_page_token from the previous response of this same call; omit for the first page.",
    )


class DeleteNoteRequest(ToolRequestModel):
    note_name: str = Field(description=_NOTE_NAME_DESCRIPTION)


class UpdateNoteRequest(ToolRequestModel):
    note_name: str = Field(description=_NOTE_NAME_DESCRIPTION)
    title: str | None = Field(default=None, description="Replacement title; accepted for compatibility and not applied.")
    text_body: str | None = Field(default=None, description="Replacement plain-text body; accepted for compatibility and not applied.")
    checklist_items: list[ChecklistItem] = Field(
        default_factory=list,
        description="Replacement checklist items; accepted for compatibility and not applied.",
    )


class ShareNoteRequest(ToolRequestModel):
    note_name: str = Field(description=_NOTE_NAME_DESCRIPTION)
    collaborator_emails: list[EmailStr] = Field(description="Email addresses to grant writer access to the note.")


class UnshareNoteRequest(ToolRequestModel):
    note_name: str = Field(description=_NOTE_NAME_DESCRIPTION)
    permission_names: list[str] = Field(
        description=(
            "Permission resource names 'notes/ABC123/permissions/XYZ' to remove, as returned in the "
            "permissions of keep_share_note's result. The owner's permission cannot be removed, and "
            "if any name is invalid the whole call fails and nothing is removed."
        )
    )


class AppendNoteRequest(ToolRequestModel):
    note_name: str = Field(description=_NOTE_NAME_DESCRIPTION)
    text_append: str | None = Field(
        default=None,
        description=(
            "Text to add. On a plain-text note it goes after the existing text on a new line; on a "
            "checklist note, or when checklist_append is also given, it becomes a new unchecked item."
        ),
    )
    checklist_append: list[ChecklistItem] = Field(
        default_factory=list,
        description="Checklist items to add after the existing ones; giving any turns a plain-text note into a checklist.",
    )
    apply_via_replacement: bool = Field(
        default=False,
        description=(
            "When true, create a new note containing the result (Keep cannot edit a note in place); "
            "when false (default), only return a preview of that note and create nothing."
        ),
    )
    delete_original_on_apply: bool = Field(
        default=False,
        description="When true and apply_via_replacement is true, permanently delete the original note after the replacement is created, once the user confirms; ignored otherwise.",
    )


class PatchChecklistItemRequest(ToolRequestModel):
    note_name: str = Field(description=_NOTE_NAME_DESCRIPTION)
    operation: Literal["add", "remove", "set_checked", "set_text"] = Field(
        description=(
            "Edit to make: 'add' appends the item given by text (and checked); 'remove' deletes the "
            "item at index; 'set_checked' sets the checked state of the item at index; 'set_text' "
            "replaces the text of the item at index."
        ),
    )
    index: int | None = Field(
        default=None,
        ge=0,
        description="Zero-based position of the checklist item; required for 'remove', 'set_checked' and 'set_text', unused for 'add'.",
    )
    text: str | None = Field(
        default=None,
        description="Item text; required for 'add' and 'set_text', unused otherwise.",
    )
    checked: bool | None = Field(
        default=None,
        description="Checked state; required for 'set_checked', optional for 'add' (default unchecked), unused otherwise.",
    )
    apply_via_replacement: bool = Field(
        default=False,
        description=(
            "When true, create a new note containing the edited checklist (Keep cannot edit a note "
            "in place); when false (default), only return a preview of that note and create nothing."
        ),
    )
    delete_original_on_apply: bool = Field(
        default=False,
        description="When true and apply_via_replacement is true, permanently delete the original note after the replacement is created, once the user confirms; ignored otherwise.",
    )
