"""Pydantic models for Google Chat tools."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ListSpacesRequest(BaseModel):
    page_size: int = Field(default=50, ge=1, le=1000)
    page_token: str | None = None
    filter: str | None = None


class GetSpaceRequest(BaseModel):
    space_name: str


class ListMessagesRequest(BaseModel):
    space_name: str
    page_size: int = Field(default=50, ge=1, le=1000)
    page_token: str | None = None
    filter: str | None = None
    order_by: str | None = None
    thread_name: str | None = None


class GetMessageRequest(BaseModel):
    message_name: str


class CreateMessageRequest(BaseModel):
    space_name: str
    text: str
    thread_key: str | None = None
    request_id: str | None = None
    message_id: str | None = None
    message_reply_option: str | None = None
    private_message_viewer: str | None = None
    notify: bool = False


class DeleteMessageRequest(BaseModel):
    message_name: str
    force: bool = False


class UpdateMessageRequest(BaseModel):
    message_name: str
    text: str
    update_mask: str = "text"
