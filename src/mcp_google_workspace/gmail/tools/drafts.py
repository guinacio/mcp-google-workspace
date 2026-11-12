"""Gmail draft lifecycle tools."""

from __future__ import annotations

import logging

from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ...common.timezone import resolve_user_timezone
from ...file_uploads import require_local_filesystem, workspace_file_upload
from ..client import gmail_service
from ..mime_utils import build_email_message, decode_rfc2047, email_to_gmail_raw
from ..helpers import (
    ATTACHMENTS_DESCRIPTION,
    HTML_BODY_DESCRIPTION,
    TEXT_BODY_DESCRIPTION,
    recipient_set,
)
from ..presentation import draft_detail, draft_summary, header_map
from .messages import _MESSAGE_ID_PATTERN, _reply_references
from ..schemas import (
    AttachmentInput,
    CreateDraftRequest,
    DeleteDraftRequest,
    GetDraftRequest,
    ListDraftsRequest,
    SendDraftRequest,
    UpdateDraftRequest,
)

LOGGER = logging.getLogger(__name__)

_DRAFT_THREAD_ID = (
    "Gmail thread to file the draft under as a reply (thread_id from search or read results): In-Reply-To "
    "and References point at the thread's latest message, so keep the thread's subject for Gmail to join it."
)
_SAVE_ATTACHMENTS = (
    ATTACHMENTS_DESCRIPTION + " When rewriting a draft, attachments already on it are dropped unless passed again."
)


async def load_draft_headers(draft_id: str) -> dict[str, Any]:
    """The saved draft's recipients and subject, for the send preview."""
    from email.utils import getaddresses

    draft = await execute_google_request(
        gmail_service().users().drafts().get(userId="me", id=draft_id, format="metadata")
    )
    headers = header_map(((draft.get("message") or {}).get("payload")) or {})

    def addresses(name: str) -> list[str]:
        return [address for _, address in getaddresses([headers.get(name, "")]) if address]

    return {
        "to": addresses("to"),
        "cc": addresses("cc"),
        "bcc": addresses("bcc"),
        "subject": decode_rfc2047(headers.get("subject")) or "(no subject)",
    }


def _build_raw_message_payload(
    *,
    subject: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    text_body: str | None,
    html_body: str | None,
    attachments: list[dict[str, Any]],
    in_reply_to: str | None = None,
    references: str | None = None,
) -> str:
    email_message = build_email_message(
        subject=subject,
        to=to,
        cc=cc,
        bcc=bcc,
        text_body=text_body,
        html_body=html_body,
        attachments=attachments,
        in_reply_to=in_reply_to,
        references=references,
    )
    return email_to_gmail_raw(email_message)


async def _thread_reply_headers(service: Any, thread_id: str) -> tuple[str | None, str | None]:
    """In-Reply-To and References for a reply to the thread's latest message with a Message-ID.

    Gmail files a draft into a thread only when, besides threadId, these headers
    point into it (and the subject matches).
    """
    thread = await execute_google_request(
        service.users()
        .threads()
        .get(userId="me", id=thread_id, format="metadata", metadataHeaders=["Message-ID", "References"])
    )
    for message in reversed(thread.get("messages") or []):
        headers = header_map(message.get("payload", {}))
        match = _MESSAGE_ID_PATTERN.search(headers.get("message-id", ""))
        if match:
            return match.group(0), _reply_references(headers.get("references", ""), match.group(0))
    return None, None


def _attachment_payloads(
    items: list[AttachmentInput], ctx: Context | None
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for item in items:
        if item.uploaded_file:
            uploaded = workspace_file_upload.get_file(item.uploaded_file, ctx)
            payloads.append(
                {
                    "data": uploaded.data,
                    "filename": item.filename or uploaded.name,
                    "mime_type": item.mime_type or uploaded.mime_type,
                }
            )
        else:
            require_local_filesystem("Gmail draft attachment")
            payloads.append(
                {
                    "path": item.file_path,
                    "filename": item.filename or "",
                    "mime_type": item.mime_type or "application/octet-stream",
                }
            )
    return payloads


def register(server: FastMCP) -> None:
    @server.tool(name="list_drafts")
    async def list_drafts(
        draft_id: Annotated[
            str | None,
            (
                "Draft ID (the id from this list or from gmail_save_draft) to read one draft in full: its "
                "recipients' headers, snippet and, with format 'full', the cleaned body. Paging parameters are "
                "ignored when it is given."
            ),
        ] = None,
        format: Annotated[
            Literal["full", "metadata", "minimal", "raw"],
            (
                "With draft_id only: how much of the draft to read: 'full' (default) adds the headers, "
                "attachments and the whole cleaned body text as body; 'metadata' the headers only; 'minimal' "
                "IDs, labels, date and snippet only; 'raw' also the whole RFC 2822 message base64url-encoded "
                "as raw."
            ),
        ] = "full",
        max_results: Annotated[int, "Drafts per page, 1 to 500 (default 25)."] = 25,
        page_token: str | None = None,
        include_spam_trash: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List the account's Gmail drafts a page at a time, or read one draft in full with draft_id. Change a draft with gmail_save_draft and send it with gmail_send_draft. Returns drafts and next_page_token: without draft_id each draft has id, message_id and thread_id only; with draft_id the one draft also has label_ids, date, from, to, cc, bcc, subject, snippet, attachments (with download_id) and, with format 'full', the whole cleaned body text as body with body_chars; fields a format does not read are null."""
        service = gmail_service()
        if draft_id is not None:
            request = GetDraftRequest(draft_id=draft_id, format=format)
            LOGGER.debug(f"Reading draft {request.draft_id}.")
            draft = await execute_google_request(
                service.users()
                .drafts()
                .get(
                    userId="me",
                    id=request.draft_id,
                    format=request.format,
                )
            )
            item = draft_detail(draft, format=request.format, account_timezone=await resolve_user_timezone())
            return {"drafts": [item], "count": 1, "next_page_token": None, "result_size_estimate": 1}  # nosec B105 -- no next page, not a credential
        list_request = ListDraftsRequest(
            max_results=max_results,
            page_token=page_token,
            include_spam_trash=include_spam_trash,
        )
        LOGGER.debug("Listing Gmail drafts.")
        result = await execute_google_request(
            service.users()
            .drafts()
            .list(
                userId="me",
                maxResults=list_request.max_results,
                pageToken=list_request.page_token,
                includeSpamTrash=list_request.include_spam_trash,
            )
        )
        drafts = [
            {
                "id": entry.get("id"),
                "message_id": (entry.get("message") or {}).get("id"),
                "thread_id": (entry.get("message") or {}).get("threadId"),
            }
            for entry in result.get("drafts", [])
            if isinstance(entry, dict)
        ]
        if ctx is not None:
            await ctx.report_progress(len(drafts), list_request.max_results, "Drafts loaded")
        return {
            "drafts": drafts,
            "count": len(drafts),
            "next_page_token": result.get("nextPageToken"),
            "result_size_estimate": result.get("resultSizeEstimate", 0),
        }

    @server.tool(name="save_draft")
    async def save_draft(
        subject: Annotated[str, "Subject line; non-ASCII text is encoded automatically."],
        draft_id: Annotated[
            str | None,
            (
                "ID of an existing draft to rewrite (from gmail_list_drafts): its recipients, subject, body and "
                "attachments are all replaced by this call. Omit to create a new draft."
            ),
        ] = None,
        to: Annotated[
            list[str] | None,
            "Primary recipient email addresses, e.g. ['ana@example.com']; may be left empty and filled in later. When rewriting, recipients left out are removed.",
        ] = None,
        cc: Annotated[list[str] | None, "Carbon-copy recipient email addresses, visible to every recipient."] = None,
        bcc: Annotated[list[str] | None, "Blind-copy recipient email addresses, hidden from the other recipients."] = None,
        text_body: Annotated[str | None, TEXT_BODY_DESCRIPTION] = None,
        html_body: Annotated[str | None, HTML_BODY_DESCRIPTION] = None,
        attachments: Annotated[list[AttachmentInput] | None, _SAVE_ATTACHMENTS] = None,
        thread_id: Annotated[str | None, _DRAFT_THREAD_ID] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Save mail as a Gmail draft without sending it, so the user can review or edit it in Gmail first: a new draft, or with draft_id a full rewrite of an existing one (pass again everything to keep). Send it later with gmail_send_draft; gmail_send_email sends right away, and gmail_reply replies in a thread right away with the reply recipients filled in. Returns status 'ok', mode ('created' or 'updated') and draft: id (the draft_id the other draft tools take), message_id, thread_id and label_ids."""
        recipients = recipient_set(to=to, cc=cc, bcc=bcc)
        service = gmail_service()
        if draft_id is None:
            request: CreateDraftRequest | UpdateDraftRequest = CreateDraftRequest(
                recipients=recipients,
                subject=subject,
                text_body=text_body,
                html_body=html_body,
                attachments=attachments or [],
                thread_id=thread_id,
            )
        else:
            request = UpdateDraftRequest(
                draft_id=draft_id,
                recipients=recipients,
                subject=subject,
                text_body=text_body,
                html_body=html_body,
                attachments=attachments or [],
                thread_id=thread_id,
            )
        in_reply_to, references = (
            await _thread_reply_headers(service, request.thread_id) if request.thread_id else (None, None)
        )
        raw = _build_raw_message_payload(
            subject=request.subject,
            to=[str(v) for v in request.recipients.to],
            cc=[str(v) for v in request.recipients.cc],
            bcc=[str(v) for v in request.recipients.bcc],
            text_body=request.text_body,
            html_body=request.html_body,
            attachments=_attachment_payloads(request.attachments, ctx),
            in_reply_to=in_reply_to,
            references=references,
        )
        message_payload: dict[str, Any] = {"raw": raw}
        if request.thread_id:
            message_payload["threadId"] = request.thread_id
        if isinstance(request, UpdateDraftRequest):
            LOGGER.debug(f"Updating Gmail draft {request.draft_id}.")
            saved = await execute_google_request(
                service.users()
                .drafts()
                .update(
                    userId="me",
                    id=request.draft_id,
                    body={"id": request.draft_id, "message": message_payload},
                )
            )
            return {"status": "ok", "mode": "updated", "draft": draft_summary(saved)}
        LOGGER.debug("Creating Gmail draft.")
        saved = await execute_google_request(
            service.users()
            .drafts()
            .create(userId="me", body={"message": message_payload})
        )
        return {"status": "ok", "mode": "created", "draft": draft_summary(saved)}

    @server.tool(name="delete_draft")
    async def delete_draft(
        draft_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Permanently delete one Gmail draft after asking the user to confirm; it does not go to Trash. To change a draft instead use gmail_save_draft with its draft_id. Returns status 'ok' with the draft_id, or status 'cancelled' if the user declines. Cannot be undone."""
        request = DeleteDraftRequest(draft_id=draft_id)
        service = gmail_service()
        LOGGER.debug(f"Deleting Gmail draft {request.draft_id}.")
        await execute_google_request(service.users().drafts().delete(userId="me", id=request.draft_id))
        return {"status": "ok", "draft_id": request.draft_id}

    @server.tool(name="send_draft")
    async def send_draft(
        draft_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Send an existing Gmail draft exactly as saved; Gmail removes it from Drafts once sent. Check its content with gmail_list_drafts (draft_id) and change it with gmail_save_draft first; gmail_send_email sends new mail without a draft. Returns status 'sent' with the sent message's message_id, thread_id and label_ids, or status 'cancelled' if the user declines. The user is asked to approve the send first, shown the draft's recipients and subject."""
        request = SendDraftRequest(draft_id=draft_id)
        service = gmail_service()
        LOGGER.debug(f"Sending Gmail draft {request.draft_id}.")
        sent = await execute_google_request(
            service.users()
            .drafts()
            .send(userId="me", body={"id": request.draft_id})
        )
        return {
            "status": "sent",
            "message_id": sent.get("id"),
            "thread_id": sent.get("threadId"),
            "label_ids": sent.get("labelIds", []),
        }
