"""Pydantic models for Google Chat tools."""

from __future__ import annotations

from pydantic import Field

from ..common.request_model import ToolRequestModel


class ListSpacesRequest(ToolRequestModel):
    page_size: int = Field(
        default=50, ge=1, le=1000, description="Maximum number of spaces to return in one page, from 1 to 1000."
    )
    page_token: str | None = Field(
        default=None,
        description="The next_page_token from the previous response of this same call; omit for the first page.",
    )
    filter: str | None = Field(
        default=None,
        description=(
            "Chat API space filter on the space type, e.g. 'spaceType = \"SPACE\"' or "
            "'spaceType = \"GROUP_CHAT\" OR spaceType = \"DIRECT_MESSAGE\"'; omit to list every space."
        ),
    )
    enrich_dms: bool = Field(
        default=True,
        description=(
            "When true, look up the other person in each direct-message space so the result has "
            "their name and email; when false, direct messages carry no peer details."
        ),
    )


class GetSpaceRequest(ToolRequestModel):
    space_name: str = Field(
        description="Chat space resource name 'spaces/AAAA...' or the bare space ID, from chat_list_spaces."
    )


class ListMessagesRequest(ToolRequestModel):
    space_name: str = Field(
        description="Chat space resource name 'spaces/AAAA...' or the bare space ID, from chat_list_spaces."
    )
    page_size: int = Field(
        default=50, ge=1, le=1000, description="Maximum number of messages to return in one page, from 1 to 1000."
    )
    page_token: str | None = Field(
        default=None,
        description="The next_page_token from the previous response of this same call; omit for the first page.",
    )
    filter: str | None = Field(
        default=None,
        description=(
            "Chat API message filter on creation time and thread, e.g. "
            "'create_time > \"2026-01-01T00:00:00+00:00\"' or 'thread.name = spaces/AAAA/threads/BBBB'; "
            "combine with AND; omit for no filter."
        ),
    )
    order_by: str | None = Field(
        default=None,
        description=(
            "Ordering by creation time, e.g. 'createTime DESC' for newest first; omit for the Chat "
            "default, oldest first."
        ),
    )
    thread_name: str | None = Field(
        default=None,
        description=(
            "Thread resource name 'spaces/AAAA/threads/BBBB'. To list one thread's messages, "
            "put 'thread.name = spaces/AAAA/threads/BBBB' in filter instead."
        ),
    )
    enrich_authors: bool = Field(
        default=True,
        description=(
            "When true, return compact messages with each author's name and email resolved; when "
            "false, return the raw Chat message resources with author IDs only."
        ),
    )


class ListSpaceMembersRequest(ToolRequestModel):
    space_name: str = Field(
        description="Chat space resource name 'spaces/AAAA...' or the bare space ID, from chat_list_spaces."
    )
    include_self: bool = Field(
        default=False,
        description="When true, the authenticated user is listed too; by default only the other members are returned.",
    )
    page_token: str | None = Field(
        default=None,
        description="The next_page_token from the previous response of this same call; omit for the first page.",
    )


class GetMessageRequest(ToolRequestModel):
    message_name: str = Field(
        description=(
            "Chat message resource name in the form 'spaces/AAAA/messages/BBBB', the id field of "
            "chat_list_messages results; a bare message ID is rejected."
        )
    )


class CreateMessageRequest(ToolRequestModel):
    space_name: str = Field(
        description="Chat space resource name 'spaces/AAAA...' or the bare space ID, from chat_list_spaces."
    )
    text: str = Field(description="Text of the message to post.")
    thread_key: str | None = Field(
        default=None,
        description=(
            "Caller-chosen thread key: messages posted with the same key in the same space land in "
            "the same thread, and the first one starts it; omit to post outside any thread."
        ),
    )
    request_id: str | None = Field(
        default=None,
        description=(
            "Idempotency key for message creation: Google Chat returns the existing message "
            "instead of posting again when the same request_id is reused, so reuse it when "
            "retrying after an unknown outcome."
        ),
    )
    message_id: str | None = Field(
        default=None,
        description=(
            "Custom message ID, which must start with 'client-' and use only lowercase letters, "
            "digits and hyphens, at most 63 characters in total; unique within the space."
        ),
    )
    message_reply_option: str | None = Field(
        default=None,
        description=(
            "How the message relates to the thread given by thread_key, e.g. "
            "'REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD' or 'REPLY_MESSAGE_OR_FAIL'; "
            "only supported in named spaces. Omit to leave it to Chat."
        ),
    )
    private_message_viewer: str | None = Field(
        default=None,
        description=(
            "User resource name 'users/123456789' of the only person who can see the message; "
            "omit for a message visible to the whole space."
        ),
    )


class DeleteMessageRequest(ToolRequestModel):
    message_name: str = Field(
        description=(
            "Chat message resource name in the form 'spaces/AAAA/messages/BBBB', the id field of "
            "chat_list_messages results; a bare message ID is rejected."
        )
    )


class UpdateMessageRequest(ToolRequestModel):
    message_name: str = Field(
        description=(
            "Chat message resource name in the form 'spaces/AAAA/messages/BBBB', the id field of "
            "chat_list_messages results; a bare message ID is rejected."
        )
    )
    text: str = Field(description="New text; it replaces the message's current text.")
    update_mask: str = Field(
        default="text",
        description=(
            "Comma-separated Chat message field paths to update. Only the text is sent, so leave "
            "the default 'text'."
        ),
    )


class PostSimpleMessageRequest(ToolRequestModel):
    space_name: str = Field(
        description="Chat space resource name 'spaces/AAAA...' or the bare space ID, from chat_list_spaces."
    )
    text: str = Field(description="Text of the message to post.")


class ReplyToMessageRequest(ToolRequestModel):
    message_name: str = Field(
        description=(
            "Chat message resource name in the form 'spaces/AAAA/messages/BBBB' of the message to "
            "reply to, from chat_list_messages; the reply goes into that message's thread."
        )
    )
    text: str = Field(description="Text of the reply.")


class FindDirectMessageRequest(ToolRequestModel):
    user: str = Field(
        description=(
            "The other person: an email address (user@example.com) or a user resource name "
            "(users/123456789)."
        )
    )
