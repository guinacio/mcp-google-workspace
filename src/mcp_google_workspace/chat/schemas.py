"""Pydantic models for Google Chat tools."""

from __future__ import annotations

from pydantic import Field

from ..common.request_model import ToolRequestModel


class ListSpacesRequest(ToolRequestModel):
    page_size: int = Field(default=50, ge=1, le=1000, description="Maximum spaces to return.")
    page_token: str | None = Field(default=None, description="Pagination token from previous response.")
    filter: str | None = Field(default=None, description="Optional Chat API filter expression.")


class GetSpaceRequest(ToolRequestModel):
    space_name: str = Field(description="Chat space resource name, e.g. spaces/AAAA...")


class ListMessagesRequest(ToolRequestModel):
    space_name: str = Field(description="Chat space resource name.")
    page_size: int = Field(default=50, ge=1, le=1000, description="Maximum messages to return.")
    page_token: str | None = Field(default=None, description="Pagination token from previous response.")
    filter: str | None = Field(default=None, description="Optional message filter expression.")
    order_by: str | None = Field(default=None, description="Sort order expression.")
    thread_name: str | None = Field(default=None, description="Optional thread resource name to scope messages.")


class GetMessageRequest(ToolRequestModel):
    message_name: str = Field(description="Chat message resource name, e.g. spaces/.../messages/...")


class CreateMessageRequest(ToolRequestModel):
    space_name: str = Field(description="Chat space resource name.")
    text: str = Field(description="Message text content.")
    thread_key: str | None = Field(default=None, description="Client-generated thread key for thread affinity.")
    request_id: str | None = Field(default=None, description="Idempotency key for message creation.")
    message_id: str | None = Field(default=None, description="Custom message ID when supported.")
    message_reply_option: str | None = Field(default=None, description="Reply mode for thread/message handling.")
    private_message_viewer: str | None = Field(default=None, description="User resource for private message visibility.")
    notify: bool = Field(default=False, description="Whether to notify users immediately.")


class DeleteMessageRequest(ToolRequestModel):
    message_name: str = Field(description="Chat message resource name.")
    force: bool = Field(default=False, description="Skip interactive confirmation when true.")


class UpdateMessageRequest(ToolRequestModel):
    message_name: str = Field(description="Chat message resource name.")
    text: str = Field(description="Updated message text.")
    update_mask: str = Field(default="text", description="Comma-separated field mask for mutable fields.")


class PostSimpleMessageRequest(ToolRequestModel):
    space_name: str = Field(description="Chat space resource name.")
    text: str = Field(description="Message text content.")
    notify: bool = Field(default=False, description="Whether to ask interactive confirmation before posting.")


class ReplyToMessageRequest(ToolRequestModel):
    message_name: str = Field(description="Existing Chat message resource name to reply to.")
    text: str = Field(description="Reply text content.")
    notify: bool = Field(default=False, description="Whether to ask interactive confirmation before posting.")
