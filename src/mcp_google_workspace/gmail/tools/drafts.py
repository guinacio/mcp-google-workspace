"""Gmail draft lifecycle tools."""

from __future__ import annotations

import logging

from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ...common.confirmation import confirm_destructive_action
from ...file_uploads import require_local_filesystem, workspace_file_upload
from ..client import gmail_service
from ..mime_utils import build_email_message, email_to_gmail_raw
from ..helpers import (
    ATTACHMENTS_DESCRIPTION,
    HTML_BODY_DESCRIPTION,
    TEXT_BODY_DESCRIPTION,
    recipient_set,
)
from ..presentation import clean_message_content, clean_snippet
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
    "Gmail thread to file the draft under (thread_id from search or read results). Only the thread ID is "
    "set; the In-Reply-To and References headers Gmail's threading also expects are not added, so the "
    "draft may not join the thread. For a real reply use gmail_reply_email."
)
_UPDATE_ATTACHMENTS = (
    ATTACHMENTS_DESCRIPTION + " Attachments already on the draft are dropped unless passed again."
)


def _build_raw_message_payload(
    *,
    subject: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    text_body: str | None,
    html_body: str | None,
    attachments: list[dict[str, Any]],
) -> str:
    email_message = build_email_message(
        subject=subject,
        to=to,
        cc=cc,
        bcc=bcc,
        text_body=text_body,
        html_body=html_body,
        attachments=attachments,
    )
    return email_to_gmail_raw(email_message)


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
        max_results: Annotated[int, "Drafts per page, 1 to 500 (default 25)."] = 25,
        page_token: str | None = None,
        include_spam_trash: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List the account's Gmail drafts, a page at a time. Read a draft's recipients and body with gmail_get_draft, since this list carries IDs only. Returns drafts, each with its draft id and message (id, threadId), plus next_page_token and result_size_estimate."""
        request = ListDraftsRequest(
            max_results=max_results,
            page_token=page_token,
            include_spam_trash=include_spam_trash,
        )
        service = gmail_service()
        LOGGER.debug("Listing Gmail drafts.")
        result = await execute_google_request(
            service.users()
            .drafts()
            .list(
                userId="me",
                maxResults=request.max_results,
                pageToken=request.page_token,
                includeSpamTrash=request.include_spam_trash,
            )
        )
        drafts = result.get("drafts", [])
        if ctx is not None:
            await ctx.report_progress(len(drafts), request.max_results, "Drafts loaded")
        return {
            "drafts": drafts,
            "next_page_token": result.get("nextPageToken"),
            "result_size_estimate": result.get("resultSizeEstimate", 0),
        }

    @server.tool(name="get_draft")
    async def get_draft(
        draft_id: str,
        format: Annotated[
            Literal["full", "metadata", "minimal", "raw"],
            (
                "Gmail API format of the message under draft: 'full' (default) with headers and "
                "base64url-encoded body parts, and the cleaned body text added as body; 'metadata' with "
                "headers only; 'minimal' with IDs and labels only; 'raw' with the whole RFC 2822 message "
                "base64url-encoded in draft.message.raw."
            ),
        ] = "full",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read one Gmail draft by draft ID. Find draft IDs with gmail_list_drafts; change the draft with gmail_update_draft and send it with gmail_send_draft. Returns id, message_id, thread_id, label_ids, snippet and Gmail's draft resource under draft; with format 'full' also the whole cleaned body text as body, unpaged, with body_chars."""
        request = GetDraftRequest(
            draft_id=draft_id,
            format=format,
        )
        service = gmail_service()
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
        message = draft.get("message", {})
        snippet = message.get("snippet")
        result: dict[str, Any] = {
            "id": draft.get("id"),
            "message_id": message.get("id"),
            "thread_id": message.get("threadId"),
            "label_ids": message.get("labelIds", []),
            "snippet": clean_snippet(snippet) if isinstance(snippet, str) else snippet,
            "draft": draft,
        }
        if request.format == "full":
            # The same cleaned text as gmail_read_emails, whole and unpaged.
            result.update(clean_message_content(message, limit=None))
        return result

    @server.tool(name="create_draft")
    async def create_draft(
        subject: Annotated[str, "Subject line; non-ASCII text is encoded automatically."],
        to: Annotated[list[str] | None, "Primary recipient email addresses, e.g. ['ana@example.com']; may be left empty and filled in later."] = None,
        cc: Annotated[list[str] | None, "Carbon-copy recipient email addresses, visible to every recipient."] = None,
        bcc: Annotated[list[str] | None, "Blind-copy recipient email addresses, hidden from the other recipients."] = None,
        text_body: Annotated[str | None, TEXT_BODY_DESCRIPTION] = None,
        html_body: Annotated[str | None, HTML_BODY_DESCRIPTION] = None,
        attachments: Annotated[list[AttachmentInput] | None, ATTACHMENTS_DESCRIPTION] = None,
        thread_id: Annotated[str | None, _DRAFT_THREAD_ID] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Save a new Gmail draft without sending it, so the user can review or edit it in Gmail first. Send it later with gmail_send_draft or rewrite it with gmail_update_draft; gmail_send_email sends right away, and for an in-thread reply gmail_reply_email sets the reply headers that a draft made here lacks. Returns the Gmail draft resource under draft: its id (the draft_id the other draft tools take) and message (id, threadId)."""
        request = CreateDraftRequest(
            recipients=recipient_set(to=to, cc=cc, bcc=bcc),
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            attachments=attachments or [],
            thread_id=thread_id,
        )
        service = gmail_service()
        LOGGER.debug("Creating Gmail draft.")
        raw = _build_raw_message_payload(
            subject=request.subject,
            to=[str(v) for v in request.recipients.to],
            cc=[str(v) for v in request.recipients.cc],
            bcc=[str(v) for v in request.recipients.bcc],
            text_body=request.text_body,
            html_body=request.html_body,
            attachments=_attachment_payloads(request.attachments, ctx),
        )
        message_payload: dict[str, Any] = {"raw": raw}
        if request.thread_id:
            message_payload["threadId"] = request.thread_id
        created = await execute_google_request(
            service.users()
            .drafts()
            .create(userId="me", body={"message": message_payload})
        )
        return {"draft": created}

    @server.tool(name="update_draft")
    async def update_draft(
        draft_id: str,
        subject: Annotated[str, "Subject line of the rewritten draft; non-ASCII text is encoded automatically."],
        to: Annotated[list[str] | None, "Primary recipient email addresses; recipients left out here are removed from the draft."] = None,
        cc: Annotated[list[str] | None, "Carbon-copy recipient email addresses, visible to every recipient."] = None,
        bcc: Annotated[list[str] | None, "Blind-copy recipient email addresses, hidden from the other recipients."] = None,
        text_body: Annotated[str | None, TEXT_BODY_DESCRIPTION] = None,
        html_body: Annotated[str | None, HTML_BODY_DESCRIPTION] = None,
        attachments: Annotated[list[AttachmentInput] | None, _UPDATE_ATTACHMENTS] = None,
        thread_id: Annotated[str | None, _DRAFT_THREAD_ID] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Replace the whole content of an existing Gmail draft: recipients, subject, body and attachments are all rebuilt from this call, so pass again everything to keep. Read the current content with gmail_get_draft first, and send the result with gmail_send_draft. Returns the updated Gmail draft resource under draft: the same id with a new message (id, threadId)."""
        request = UpdateDraftRequest(
            draft_id=draft_id,
            recipients=recipient_set(to=to, cc=cc, bcc=bcc),
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            attachments=attachments or [],
            thread_id=thread_id,
        )
        service = gmail_service()
        LOGGER.debug(f"Updating Gmail draft {request.draft_id}.")
        raw = _build_raw_message_payload(
            subject=request.subject,
            to=[str(v) for v in request.recipients.to],
            cc=[str(v) for v in request.recipients.cc],
            bcc=[str(v) for v in request.recipients.bcc],
            text_body=request.text_body,
            html_body=request.html_body,
            attachments=_attachment_payloads(request.attachments, ctx),
        )
        message_payload: dict[str, Any] = {"raw": raw}
        if request.thread_id:
            message_payload["threadId"] = request.thread_id
        updated = await execute_google_request(
            service.users()
            .drafts()
            .update(
                userId="me",
                id=request.draft_id,
                body={"id": request.draft_id, "message": message_payload},
            )
        )
        return {"draft": updated}

    @server.tool(name="delete_draft")
    async def delete_draft(
        draft_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Permanently delete one Gmail draft after asking the user to confirm; it does not go to Trash. To change a draft instead use gmail_update_draft. Returns status 'ok' with the draft_id, or status 'cancelled' if the user declines. Cannot be undone."""
        request = DeleteDraftRequest(draft_id=draft_id)
        if not await confirm_destructive_action(
            ctx, "delete_draft", f"Permanently delete Gmail draft {request.draft_id}?"
        ):
            return {"status": "cancelled", "draft_id": request.draft_id}
        service = gmail_service()
        LOGGER.debug(f"Deleting Gmail draft {request.draft_id}.")
        await execute_google_request(service.users().drafts().delete(userId="me", id=request.draft_id))
        return {"status": "ok", "draft_id": request.draft_id}

    @server.tool(name="send_draft")
    async def send_draft(
        draft_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Send an existing Gmail draft exactly as saved; Gmail removes it from Drafts once sent. Check its content with gmail_get_draft and change it with gmail_update_draft first; gmail_send_email sends new mail without a draft. Returns status 'sent' with the sent message's message_id, thread_id and label_ids. Sends immediately, with no confirmation step."""
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
