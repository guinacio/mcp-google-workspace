"""Pydantic models for Gmail tools."""

from __future__ import annotations

from typing import Literal

from pydantic import EmailStr, Field, model_validator

from ..common.request_model import ToolRequestModel


class AttachmentInput(ToolRequestModel):
    """One attachment selected in the MCP App or read from a local filesystem."""

    model_config = {
        "extra": "forbid",
        "populate_by_name": True,
        "json_schema_extra": {
            "description": (
                "Email attachment. Provide exactly one source: uploaded_file for a file "
                "selected with the Workspace Files MCP App (uploads_file_manager), or file_path for local stdio use."
            ),
            "oneOf": [
                {
                    "required": ["uploaded_file"],
                    "properties": {"file_path": {"type": "null"}},
                    "title": "MCP App uploaded file",
                },
                {
                    "required": ["file_path"],
                    "properties": {"uploaded_file": {"type": "null"}},
                    "title": "Local filesystem path",
                },
            ],
        },
    }

    file_path: str | None = Field(
        default=None,
        description="Path to a file on the server's machine; only for a local (stdio) server, refused over remote MCP.",
    )
    uploaded_file: str | None = Field(
        default=None,
        description="Upload handle (upl_...) of a file the user picked in uploads_file_manager, as listed by uploads_list_files.",
    )
    mime_type: str | None = Field(
        default=None,
        description=(
            "MIME type to attach the file as, e.g. 'application/pdf'; defaults to the uploaded file's "
            "type, or application/octet-stream for a file_path."
        ),
    )
    filename: str | None = Field(
        default=None,
        description="File name recipients see; defaults to the uploaded file's original name or the file_path's base name.",
    )

    @model_validator(mode="after")
    def _validate_source(self) -> "AttachmentInput":
        if sum(bool(value) for value in (self.file_path, self.uploaded_file)) != 1:
            raise ValueError("Exactly one of file_path or uploaded_file must be provided.")
        return self


class RecipientSet(ToolRequestModel):
    to: list[EmailStr] = Field(default_factory=list, description="Primary recipient addresses.")
    cc: list[EmailStr] = Field(default_factory=list, description="CC recipient addresses.")
    bcc: list[EmailStr] = Field(default_factory=list, description="BCC recipient addresses.")


class SendEmailRequest(ToolRequestModel):
    recipients: RecipientSet = Field(description="Recipient lists for TO/CC/BCC.")
    subject: str = Field(description="Email subject line.")
    text_body: str | None = Field(default=None, description="Plain text email body.")
    html_body: str | None = Field(default=None, description="HTML email body.")
    attachments: list[AttachmentInput] = Field(default_factory=list, description="Optional file attachments.")


class ReadEmailsRequest(ToolRequestModel):
    model_config = {
        "json_schema_extra": {
            "examples": [
                {"message_ids": ["18c9c7b7e2a1f123"], "format": "clean"},
            ]
        }
    }
    message_ids: list[str] = Field(
        min_length=1,
        max_length=100,
        description="One to 100 Gmail message IDs to read with one consistent response shape.",
    )
    format: Literal["metadata", "preview", "clean", "full"] = Field(
        default="clean",
        description=(
            "Detail level. preview is the first 1,000 characters of the cleaned body; clean is the "
            "cleaned body paged near 2k tokens; full is the whole cleaned body plus the raw text part."
        ),
    )
    offset: int = Field(default=0, ge=0, description="Character offset for continuing a truncated clean body.")
    include_html: bool = Field(
        default=False,
        description="With format=full, also return the original HTML part as html_body (null otherwise).",
    )


class DigestRequest(ToolRequestModel):
    window: str = Field(default="3d", pattern=r"^\d+[dhw]$", description="Lookback window such as 3d, 24h, or 1w.")
    unread_only: bool = Field(default=False, description="Include only unread messages.")
    max_items: int = Field(default=25, ge=1, le=100, description="Maximum latest received or sent messages to return.")


class SearchEmailRequest(ToolRequestModel):
    query: str | None = Field(default=None, description="Gmail search query syntax.")
    label_ids: list[str] = Field(default_factory=list, description="Optional label IDs filter.")
    max_results: int = Field(default=25, ge=1, le=500, description="Maximum messages to return.")
    page_token: str | None = Field(default=None, description="Pagination token from previous call.")
    include_spam_trash: bool = Field(default=False, description="Include spam and trash in search.")
    from_email: str | None = Field(default=None, description="Optional sender email helper filter.")
    to_email: str | None = Field(default=None, description="Optional recipient email helper filter.")
    subject_contains: str | None = Field(default=None, description="Optional subject phrase helper filter.")
    has_attachment: bool = Field(default=False, description="When true, filter for emails with attachments.")
    is_unread: bool = Field(default=False, description="When true, filter for unread emails.")
    newer_than_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,
        description="Optional recency helper in days, translated to newer_than:{N}d.",
    )






class LabelCreateRequest(ToolRequestModel):
    name: str = Field(description="New label display name.")
    message_list_visibility: Literal["show", "hide"] | None = Field(default=None, description="Message list visibility setting.")
    label_list_visibility: Literal["labelShow", "labelShowIfUnread", "labelHide"] | None = Field(default=None, description="Label list visibility setting.")
    background_color: str | None = Field(default=None, description="Hex background color.")
    text_color: str | None = Field(default=None, description="Hex text color.")


class LabelUpdateRequest(ToolRequestModel):
    label_id: str = Field(description="Label ID to update.")
    name: str | None = Field(default=None, description="Updated label name.")
    message_list_visibility: Literal["show", "hide"] | None = Field(default=None, description="Updated message list visibility.")
    label_list_visibility: Literal["labelShow", "labelShowIfUnread", "labelHide"] | None = Field(default=None, description="Updated label list visibility.")
    background_color: str | None = Field(default=None, description="Updated hex background color.")
    text_color: str | None = Field(default=None, description="Updated hex text color.")


class LabelDeleteRequest(ToolRequestModel):
    label_id: str = Field(description="Label ID to delete.")



class DownloadAttachmentRequest(ToolRequestModel):
    message_id: str = Field(description="Message ID that contains the attachment.")
    attachment_id: str = Field(description="Attachment download_id returned by read_emails.")
    output_path: str = Field(description="Local output path where bytes will be saved.")


class FilterCriteriaInput(ToolRequestModel):
    from_: str | None = Field(
        default=None, alias="from", description="Sender email address or display name to match, e.g. 'ana@example.com'."
    )
    to: str | None = Field(
        default=None,
        description="Recipient email address or display name to match, in the To, Cc or Bcc headers.",
    )
    subject: str | None = Field(default=None, description="Case-insensitive phrase that must appear in the subject.")
    query: str | None = Field(
        default=None,
        description="Gmail search-box query the message must match, e.g. 'has:attachment larger:5M'.",
    )
    negated_query: str | None = Field(
        default=None, description="Gmail search-box query the message must not match, e.g. 'from:boss@example.com'."
    )
    has_attachment: bool | None = Field(
        default=None, description="When true, only messages with attachments match; omit to ignore attachments."
    )
    exclude_chats: bool | None = Field(default=None, description="When true, chat messages never match the filter.")
    size: int | None = Field(
        default=None,
        ge=0,
        description="Message size in bytes (whole message, headers and attachments included), compared using size_comparison.",
    )
    size_comparison: Literal["smaller", "larger"] | None = Field(
        default=None, description="Whether matching messages are 'smaller' or 'larger' than size."
    )

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


class FilterActionInput(ToolRequestModel):
    add_labels: list[str] = Field(
        default_factory=list,
        description=(
            "Labels to add to matching mail, by name as shown in Gmail or by ID; system labels such as "
            "STARRED or IMPORTANT match case-insensitively."
        ),
    )
    remove_labels: list[str] = Field(
        default_factory=list,
        description="Labels to remove from matching mail, by name or ID, e.g. INBOX to archive it or UNREAD to mark it read.",
    )
    forward: str | None = Field(
        default=None,
        description="Email address to forward matching mail to; it must be an 'accepted' address from gmail_manage_forwarding.",
    )

    def to_api(self, *, add_label_ids: list[str], remove_label_ids: list[str]) -> dict[str, object]:
        """The Gmail filter action, with label names already resolved to IDs."""
        payload: dict[str, object] = {}
        if add_label_ids:
            payload["addLabelIds"] = add_label_ids
        if remove_label_ids:
            payload["removeLabelIds"] = remove_label_ids
        if self.forward is not None:
            payload["forward"] = self.forward
        return payload


class CreateFilterRequest(ToolRequestModel):
    criteria: FilterCriteriaInput = Field(description="Filter matching criteria block.")
    action: FilterActionInput = Field(description="Filter action block.")


class DeleteFilterRequest(ToolRequestModel):
    filter_id: str = Field(description="Filter ID to delete.")


class ListDraftsRequest(ToolRequestModel):
    max_results: int = Field(default=25, ge=1, le=500, description="Maximum drafts to return.")
    page_token: str | None = Field(default=None, description="Pagination token from previous response.")
    include_spam_trash: bool = Field(default=False, description="Include spam/trash messages in draft listing context.")


class GetDraftRequest(ToolRequestModel):
    draft_id: str = Field(description="Draft ID to fetch.")
    format: Literal["full", "metadata", "minimal", "raw"] = Field(default="full", description="Message format in draft payload.")


class CreateDraftRequest(ToolRequestModel):
    recipients: RecipientSet = Field(description="Recipient lists for draft message.")
    subject: str = Field(description="Draft subject.")
    text_body: str | None = Field(default=None, description="Plain text draft body.")
    html_body: str | None = Field(default=None, description="HTML draft body.")
    attachments: list[AttachmentInput] = Field(default_factory=list, description="Attachments to include in draft.")
    thread_id: str | None = Field(default=None, description="Optional thread ID for reply-style drafts.")


class UpdateDraftRequest(ToolRequestModel):
    draft_id: str = Field(description="Draft ID to replace.")
    recipients: RecipientSet = Field(description="Updated recipient lists.")
    subject: str = Field(description="Updated draft subject.")
    text_body: str | None = Field(default=None, description="Updated plain text body.")
    html_body: str | None = Field(default=None, description="Updated HTML body.")
    attachments: list[AttachmentInput] = Field(default_factory=list, description="Updated attachment set.")
    thread_id: str | None = Field(default=None, description="Thread ID for updated draft message.")


class DeleteDraftRequest(ToolRequestModel):
    draft_id: str = Field(description="Draft ID to delete.")


class SendDraftRequest(ToolRequestModel):
    draft_id: str = Field(description="Draft ID to send.")



class GetThreadRequest(ToolRequestModel):
    thread_id: str = Field(description="Thread ID to fetch.")
    format: Literal["full", "metadata", "minimal"] = Field(default="full", description="Gmail API message format for thread payload.")




class ForwardingAddressRequest(ToolRequestModel):
    forwarding_email: EmailStr = Field(description="Forwarding email address.")



class UpdateVacationSettingsRequest(ToolRequestModel):
    enable_auto_reply: bool = Field(default=False, description="Enable/disable vacation auto-reply.")
    response_subject: str | None = Field(default=None, description="Auto-reply subject line.")
    response_body_plain_text: str | None = Field(default=None, description="Plain text auto-reply body.")
    response_body_html: str | None = Field(default=None, description="HTML auto-reply body.")
    restrict_to_contacts: bool = Field(default=False, description="Send auto-replies only to contacts.")
    restrict_to_domain: bool = Field(default=False, description="Send auto-replies only within workspace domain.")
    start_time: int | None = Field(default=None, ge=0, description="Start time as epoch milliseconds.")
    end_time: int | None = Field(default=None, ge=0, description="End time as epoch milliseconds.")
