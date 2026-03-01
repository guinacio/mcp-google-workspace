"""Pydantic models for Google Keep tools."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class ChecklistItem(BaseModel):
    text: str
    checked: bool = False


class CreateNoteRequest(BaseModel):
    title: str | None = None
    text_body: str | None = None
    checklist_items: list[ChecklistItem] = Field(default_factory=list)
    collaborator_emails: list[EmailStr] = Field(default_factory=list)
    confirm_create: bool = False


class GetNoteRequest(BaseModel):
    note_name: str


class ListNotesRequest(BaseModel):
    filter: str | None = None
    page_size: int = Field(default=20, ge=1, le=100)
    page_token: str | None = None


class DeleteNoteRequest(BaseModel):
    note_name: str
    confirm_delete: bool = True


class UpdateNoteRequest(BaseModel):
    note_name: str
    title: str | None = None
    text_body: str | None = None
    checklist_items: list[ChecklistItem] = Field(default_factory=list)


class ShareNoteRequest(BaseModel):
    note_name: str
    collaborator_emails: list[EmailStr]


class UnshareNoteRequest(BaseModel):
    note_name: str
    permission_names: list[str]
