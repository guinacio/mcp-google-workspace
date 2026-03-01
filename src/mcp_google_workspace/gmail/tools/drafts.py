"""Gmail draft lifecycle tools."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from ..client import gmail_service
from ..mime_utils import build_email_message, email_to_gmail_raw
from ..schemas import (
    CreateDraftRequest,
    DeleteDraftRequest,
    GetDraftRequest,
    ListDraftsRequest,
    SendDraftRequest,
    UpdateDraftRequest,
)


def _build_raw_message_payload(
    *,
    subject: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    text_body: str | None,
    html_body: str | None,
    attachments: list[dict[str, str]],
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


def _attachment_payloads(items: list[Any]) -> list[dict[str, str]]:
    return [
        {
            "path": item.file_path,
            "filename": item.filename or "",
            "mime_type": item.mime_type or "application/octet-stream",
        }
        for item in items
    ]


def register(server: FastMCP) -> None:
    @server.tool(name="list_drafts")
    async def list_drafts(
        max_results: int = 25,
        page_token: str | None = None,
        include_spam_trash: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List Gmail drafts with pagination support."""
        request = ListDraftsRequest(
            max_results=max_results,
            page_token=page_token,
            include_spam_trash=include_spam_trash,
        )
        service = gmail_service()
        if ctx is not None:
            await ctx.info("Listing Gmail drafts.")
        result = (
            service.users()
            .drafts()
            .list(
                userId="me",
                maxResults=request.max_results,
                pageToken=request.page_token,
                includeSpamTrash=request.include_spam_trash,
            )
            .execute()
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
        format: str = "full",
        metadata_headers: list[str] = [],
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Fetch a single draft and its underlying message metadata/body payload."""
        request = GetDraftRequest(
            draft_id=draft_id,
            format=format,
            metadata_headers=metadata_headers,
        )
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Reading draft {request.draft_id}.")
        draft = (
            service.users()
            .drafts()
            .get(
                userId="me",
                id=request.draft_id,
                format=request.format,
            )
            .execute()
        )
        message = draft.get("message", {})
        return {
            "id": draft.get("id"),
            "message_id": message.get("id"),
            "thread_id": message.get("threadId"),
            "label_ids": message.get("labelIds", []),
            "snippet": message.get("snippet"),
            "draft": draft,
        }

    @server.tool(name="create_draft")
    async def create_draft(
        subject: str,
        to: list[str] = [],
        cc: list[str] = [],
        bcc: list[str] = [],
        text_body: str | None = None,
        html_body: str | None = None,
        attachments: list[dict[str, Any]] = [],
        thread_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create a draft message from recipients, content, and optional attachments."""
        request = CreateDraftRequest(
            recipients={"to": to, "cc": cc, "bcc": bcc},
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            attachments=attachments,
            thread_id=thread_id,
        )
        service = gmail_service()
        if ctx is not None:
            await ctx.info("Creating Gmail draft.")
        raw = _build_raw_message_payload(
            subject=request.subject,
            to=[str(v) for v in request.recipients.to],
            cc=[str(v) for v in request.recipients.cc],
            bcc=[str(v) for v in request.recipients.bcc],
            text_body=request.text_body,
            html_body=request.html_body,
            attachments=_attachment_payloads(request.attachments),
        )
        message_payload: dict[str, Any] = {"raw": raw}
        if request.thread_id:
            message_payload["threadId"] = request.thread_id
        created = (
            service.users()
            .drafts()
            .create(userId="me", body={"message": message_payload})
            .execute()
        )
        return {"draft": created}

    @server.tool(name="update_draft")
    async def update_draft(
        draft_id: str,
        subject: str,
        to: list[str] = [],
        cc: list[str] = [],
        bcc: list[str] = [],
        text_body: str | None = None,
        html_body: str | None = None,
        attachments: list[dict[str, Any]] = [],
        thread_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Replace an existing draft's message payload."""
        request = UpdateDraftRequest(
            draft_id=draft_id,
            recipients={"to": to, "cc": cc, "bcc": bcc},
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            attachments=attachments,
            thread_id=thread_id,
        )
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Updating Gmail draft {request.draft_id}.")
        raw = _build_raw_message_payload(
            subject=request.subject,
            to=[str(v) for v in request.recipients.to],
            cc=[str(v) for v in request.recipients.cc],
            bcc=[str(v) for v in request.recipients.bcc],
            text_body=request.text_body,
            html_body=request.html_body,
            attachments=_attachment_payloads(request.attachments),
        )
        message_payload: dict[str, Any] = {"raw": raw}
        if request.thread_id:
            message_payload["threadId"] = request.thread_id
        updated = (
            service.users()
            .drafts()
            .update(
                userId="me",
                id=request.draft_id,
                body={"id": request.draft_id, "message": message_payload},
            )
            .execute()
        )
        return {"draft": updated}

    @server.tool(name="delete_draft")
    async def delete_draft(
        draft_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Delete a draft by ID."""
        request = DeleteDraftRequest(draft_id=draft_id)
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Deleting Gmail draft {request.draft_id}.")
        service.users().drafts().delete(userId="me", id=request.draft_id).execute()
        return {"status": "ok", "draft_id": request.draft_id}

    @server.tool(name="send_draft")
    async def send_draft(
        draft_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Send a previously created Gmail draft."""
        request = SendDraftRequest(draft_id=draft_id)
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Sending Gmail draft {request.draft_id}.")
        sent = (
            service.users()
            .drafts()
            .send(userId="me", body={"id": request.draft_id})
            .execute()
        )
        return {
            "status": "sent",
            "message_id": sent.get("id"),
            "thread_id": sent.get("threadId"),
            "label_ids": sent.get("labelIds", []),
        }
