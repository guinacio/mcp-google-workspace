"""Pydantic models for Gmail tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class AttachmentInput(BaseModel):
    file_path: str = Field(description="Local filesystem path to file attachment.")
    mime_type: str | None = Field(default=None, description="Optional MIME type override.")
    filename: str | None = Field(default=None, description="Optional filename override in email.")


class RecipientSet(BaseModel):
    to: list[EmailStr] = Field(default_factory=list, description="Primary recipient addresses.")
    cc: list[EmailStr] = Field(default_factory=list, description="CC recipient addresses.")
    bcc: list[EmailStr] = Field(default_factory=list, description="BCC recipient addresses.")


class SendEmailRequest(BaseModel):
    recipients: RecipientSet = Field(description="Recipient lists for TO/CC/BCC.")
    subject: str = Field(description="Email subject line.")
    text_body: str | None = Field(default=None, description="Plain text email body.")
    html_body: str | None = Field(default=None, description="HTML email body.")
    attachments: list[AttachmentInput] = Field(default_factory=list, description="Optional file attachments.")
    confirm_send: bool = Field(default=True, description="Prompt user confirmation before sending when true.")


class ReadEmailRequest(BaseModel):
    message_id: str = Field(description="Gmail message ID.")


class SearchEmailRequest(BaseModel):
    query: str | None = Field(default=None, description="Gmail search query syntax.")
    label_ids: list[str] = Field(default_factory=list, description="Optional label IDs filter.")
    max_results: int = Field(default=25, ge=1, le=500, description="Maximum messages to return.")
    page_token: str | None = Field(default=None, description="Pagination token from previous call.")
    include_spam_trash: bool = Field(default=False, description="Include spam and trash in search.")


class ListEmailsRequest(BaseModel):
    label_id: str = Field(default="INBOX", description="Single label ID to list messages from.")
    max_results: int = Field(default=25, ge=1, le=500, description="Maximum messages to return.")
    page_token: str | None = Field(default=None, description="Pagination token from previous call.")


class ModifyMessageRequest(BaseModel):
    message_id: str = Field(description="Message ID to modify.")
    add_label_ids: list[str] = Field(default_factory=list, description="Label IDs to add.")
    remove_label_ids: list[str] = Field(default_factory=list, description="Label IDs to remove.")


class BatchModifyRequest(BaseModel):
    message_ids: list[str] = Field(description="Message IDs to modify.")
    add_label_ids: list[str] = Field(default_factory=list, description="Label IDs to add to all messages.")
    remove_label_ids: list[str] = Field(default_factory=list, description="Label IDs to remove from all messages.")


class DeleteMessageRequest(BaseModel):
    message_id: str = Field(description="Message ID to delete.")
    permanent: bool = Field(default=False, description="Permanently delete instead of moving to trash.")


class BatchDeleteRequest(BaseModel):
    message_ids: list[str] = Field(description="Message IDs to delete.")
    permanent: bool = Field(default=False, description="Permanently delete instead of moving to trash.")


class LabelCreateRequest(BaseModel):
    name: str = Field(description="New label display name.")
    message_list_visibility: Literal["show", "hide"] | None = Field(default=None, description="Message list visibility setting.")
    label_list_visibility: Literal["labelShow", "labelShowIfUnread", "labelHide"] | None = Field(default=None, description="Label list visibility setting.")
    background_color: str | None = Field(default=None, description="Hex background color.")
    text_color: str | None = Field(default=None, description="Hex text color.")


class LabelUpdateRequest(BaseModel):
    label_id: str = Field(description="Label ID to update.")
    name: str | None = Field(default=None, description="Updated label name.")
    message_list_visibility: Literal["show", "hide"] | None = Field(default=None, description="Updated message list visibility.")
    label_list_visibility: Literal["labelShow", "labelShowIfUnread", "labelHide"] | None = Field(default=None, description="Updated label list visibility.")
    background_color: str | None = Field(default=None, description="Updated hex background color.")
    text_color: str | None = Field(default=None, description="Updated hex text color.")


class LabelDeleteRequest(BaseModel):
    label_id: str = Field(description="Label ID to delete.")


class ListAttachmentsRequest(BaseModel):
    message_id: str = Field(description="Message ID to inspect for attachments.")


class DownloadAttachmentRequest(BaseModel):
    message_id: str = Field(description="Message ID that contains the attachment.")
    attachment_id: str = Field(description="Attachment ID returned by list_attachments.")
    output_path: str = Field(description="Local output path where bytes will be saved.")


class FilterCriteriaInput(BaseModel):
    from_: str | None = Field(default=None, alias="from", description="Match sender address/name.")
    to: str | None = Field(default=None, description="Match recipient address/name.")
    subject: str | None = Field(default=None, description="Match phrase in subject.")
    query: str | None = Field(default=None, description="Gmail advanced query expression.")
    negated_query: str | None = Field(default=None, description="Query expression to exclude.")
    has_attachment: bool | None = Field(default=None, description="Match only messages with attachments.")
    exclude_chats: bool | None = Field(default=None, description="Exclude chat messages from matching.")
    size: int | None = Field(default=None, ge=0, description="Message size in bytes for size matching.")
    size_comparison: Literal["smaller", "larger"] | None = Field(default=None, description="Size comparison operator.")

    model_config = {
        "populate_by_name": True,
    }

    def to_api(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        if self.from_ is not None:
            payload["from"] = self.from_
        if self.to is not None:
            payload["to"] = self.to
        if self.subject is not None:
            payload["subject"] = self.subject
        if self.query is not None:
            payload["query"] = self.query
        if self.negated_query is not None:
            payload["negatedQuery"] = self.negated_query
        if self.has_attachment is not None:
            payload["hasAttachment"] = self.has_attachment
        if self.exclude_chats is not None:
            payload["excludeChats"] = self.exclude_chats
        if self.size is not None:
            payload["size"] = self.size
        if self.size_comparison is not None:
            payload["sizeComparison"] = self.size_comparison
        return payload


class FilterActionInput(BaseModel):
    add_label_ids: list[str] = Field(default_factory=list, description="Label IDs to add when criteria match.")
    remove_label_ids: list[str] = Field(default_factory=list, description="Label IDs to remove when criteria match.")
    forward: str | None = Field(default=None, description="Forwarding email destination.")

    def to_api(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        if self.add_label_ids:
            payload["addLabelIds"] = self.add_label_ids
        if self.remove_label_ids:
            payload["removeLabelIds"] = self.remove_label_ids
        if self.forward is not None:
            payload["forward"] = self.forward
        return payload


class CreateFilterRequest(BaseModel):
    criteria: FilterCriteriaInput = Field(description="Filter matching criteria block.")
    action: FilterActionInput = Field(description="Filter action block.")


class DeleteFilterRequest(BaseModel):
    filter_id: str = Field(description="Filter ID to delete.")


class ListDraftsRequest(BaseModel):
    max_results: int = Field(default=25, ge=1, le=500, description="Maximum drafts to return.")
    page_token: str | None = Field(default=None, description="Pagination token from previous response.")
    include_spam_trash: bool = Field(default=False, description="Include spam/trash messages in draft listing context.")


class GetDraftRequest(BaseModel):
    draft_id: str = Field(description="Draft ID to fetch.")
    format: Literal["full", "metadata", "minimal", "raw"] = Field(default="full", description="Message format in draft payload.")
    metadata_headers: list[str] = Field(default_factory=list, description="Headers to include when format is metadata.")


class CreateDraftRequest(BaseModel):
    recipients: RecipientSet = Field(description="Recipient lists for draft message.")
    subject: str = Field(description="Draft subject.")
    text_body: str | None = Field(default=None, description="Plain text draft body.")
    html_body: str | None = Field(default=None, description="HTML draft body.")
    attachments: list[AttachmentInput] = Field(default_factory=list, description="Attachments to include in draft.")
    thread_id: str | None = Field(default=None, description="Optional thread ID for reply-style drafts.")


class UpdateDraftRequest(BaseModel):
    draft_id: str = Field(description="Draft ID to replace.")
    recipients: RecipientSet = Field(description="Updated recipient lists.")
    subject: str = Field(description="Updated draft subject.")
    text_body: str | None = Field(default=None, description="Updated plain text body.")
    html_body: str | None = Field(default=None, description="Updated HTML body.")
    attachments: list[AttachmentInput] = Field(default_factory=list, description="Updated attachment set.")
    thread_id: str | None = Field(default=None, description="Thread ID for updated draft message.")


class DeleteDraftRequest(BaseModel):
    draft_id: str = Field(description="Draft ID to delete.")


class SendDraftRequest(BaseModel):
    draft_id: str = Field(description="Draft ID to send.")


class ListThreadsRequest(BaseModel):
    query: str | None = Field(default=None, description="Optional Gmail query to filter threads.")
    label_ids: list[str] = Field(default_factory=list, description="Optional label IDs filter for thread list.")
    max_results: int = Field(default=25, ge=1, le=500, description="Maximum threads to return.")
    page_token: str | None = Field(default=None, description="Pagination token from previous response.")
    include_spam_trash: bool = Field(default=False, description="Include spam/trash in thread listing.")


class GetThreadRequest(BaseModel):
    thread_id: str = Field(description="Thread ID to fetch.")
    format: Literal["full", "metadata", "minimal"] = Field(default="full", description="Message format for thread payload.")
    metadata_headers: list[str] = Field(default_factory=list, description="Headers to include when format is metadata.")


class ModifyThreadRequest(BaseModel):
    thread_id: str = Field(description="Thread ID to modify.")
    add_label_ids: list[str] = Field(default_factory=list, description="Label IDs to add to all thread messages.")
    remove_label_ids: list[str] = Field(default_factory=list, description="Label IDs to remove from all thread messages.")


class ThreadIdRequest(BaseModel):
    thread_id: str = Field(description="Thread ID.")


class ListHistoryRequest(BaseModel):
    start_history_id: str = Field(description="Start history ID returned by prior Gmail read/list operations.")
    history_types: list[
        Literal[
            "messageAdded",
            "messageDeleted",
            "labelAdded",
            "labelRemoved",
        ]
    ] = Field(default_factory=list, description="Optional history event types to include.")
    label_id: str | None = Field(default=None, description="Optional label ID filter.")
    max_results: int = Field(default=100, ge=1, le=500, description="Maximum history records to return.")
    page_token: str | None = Field(default=None, description="Pagination token from previous response.")


class ForwardingAddressRequest(BaseModel):
    forwarding_email: EmailStr = Field(description="Forwarding email address.")


class GetVacationSettingsRequest(BaseModel):
    # No args currently needed; request model kept for consistency.
    include_placeholder: bool = Field(default=False, description="Placeholder field; ignored by API call.")


class UpdateVacationSettingsRequest(BaseModel):
    enable_auto_reply: bool = Field(default=False, description="Enable/disable vacation auto-reply.")
    response_subject: str | None = Field(default=None, description="Auto-reply subject line.")
    response_body_plain_text: str | None = Field(default=None, description="Plain text auto-reply body.")
    response_body_html: str | None = Field(default=None, description="HTML auto-reply body.")
    restrict_to_contacts: bool = Field(default=False, description="Send auto-replies only to contacts.")
    restrict_to_domain: bool = Field(default=False, description="Send auto-replies only within workspace domain.")
    start_time: int | None = Field(default=None, ge=0, description="Start time as epoch milliseconds.")
    end_time: int | None = Field(default=None, ge=0, description="End time as epoch milliseconds.")


class MessageIdRequest(BaseModel):
    message_id: str = Field(description="Gmail message ID.")


class MarkNotSpamRequest(BaseModel):
    message_id: str = Field(description="Message ID currently in spam.")
    add_to_inbox: bool = Field(default=True, description="Add INBOX label while removing SPAM.")
