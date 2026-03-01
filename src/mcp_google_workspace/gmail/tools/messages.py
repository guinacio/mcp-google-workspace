"""Gmail message operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastmcp import Context, FastMCP

from ..client import gmail_service
from ..mime_utils import (
    build_email_message,
    decode_rfc2047,
    email_to_gmail_raw,
    extract_message_bodies,
    flatten_parts,
)
from ..schemas import (
    DeleteMessageRequest,
    ModifyMessageRequest,
    ReadEmailRequest,
    SendEmailRequest,
)


def register(server: FastMCP) -> None:
    @server.tool(name="send_email")
    async def send_email(request: SendEmailRequest, ctx: Context) -> dict[str, Any]:
        """Send an email with TO/CC/BCC, text/HTML body, and optional attachments."""
        service = gmail_service()
        if request.confirm_send:
            @dataclass
            class Confirmation:
                confirm: bool

            response = await ctx.elicit(
                (
                    f"Send email?\n"
                    f"To: {', '.join(str(v) for v in request.recipients.to)}\n"
                    f"Subject: {request.subject}\n"
                    f"Attachments: {len(request.attachments)}"
                ),
                response_type=Confirmation,  # type: ignore[arg-type]
            )
            if response.action != "accept":
                return {"status": "cancelled", "message": "User cancelled send."}
            confirmed = bool(getattr(response.data, "confirm", False))
            if not confirmed:
                return {"status": "cancelled", "message": "User cancelled send."}

        await ctx.info("Building MIME email payload.")
        attachment_payloads: list[dict[str, str]] = []
        total = max(len(request.attachments), 1)
        for i, item in enumerate(request.attachments, start=1):
            attachment_payloads.append(
                {
                    "path": item.file_path,
                    "filename": item.filename or "",
                    "mime_type": item.mime_type or "application/octet-stream",
                }
            )
            await ctx.report_progress(i, total, f"Prepared attachment {i}/{total}")

        email_message = build_email_message(
            subject=request.subject,
            to=[str(v) for v in request.recipients.to],
            cc=[str(v) for v in request.recipients.cc],
            bcc=[str(v) for v in request.recipients.bcc],
            text_body=request.text_body,
            html_body=request.html_body,
            attachments=attachment_payloads,
        )
        raw = email_to_gmail_raw(email_message)
        await ctx.info("Sending email through Gmail API.")
        sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return {
            "status": "sent",
            "message_id": sent.get("id"),
            "thread_id": sent.get("threadId"),
            "label_ids": sent.get("labelIds", []),
        }

    @server.tool(name="read_email")
    async def read_email(request: ReadEmailRequest, ctx: Context) -> dict[str, Any]:
        """Fetch one Gmail message by ID.

        Input must be an object shaped like:
        {"message_id": "<gmail_message_id>"}
        """
        service = gmail_service()
        await ctx.info(f"Reading message {request.message_id}.")
        message = (
            service.users()
            .messages()
            .get(userId="me", id=request.message_id, format="full")
            .execute()
        )
        payload = message.get("payload", {})
        headers = payload.get("headers", [])
        header_map = {h.get("name", "").lower(): h.get("value", "") for h in headers}
        bodies = extract_message_bodies(payload) if not request.summary_mode else {"text": None, "html": None}
        attachments: list[dict[str, Any]] = []
        for part in flatten_parts(payload):
            filename = part.get("filename")
            body = part.get("body", {})
            attachment_id = body.get("attachmentId")
            if filename and attachment_id:
                attachments.append(
                    {
                        "filename": filename,
                        "mime_type": part.get("mimeType"),
                        "size": body.get("size", 0),
                        "download_id": attachment_id,
                    }
                )

        await ctx.info(f"Parsed MIME structure with {len(attachments)} attachment(s).")
        return {
            "id": message.get("id"),
            "thread_id": message.get("threadId"),
            "snippet": message.get("snippet"),
            "subject": decode_rfc2047(header_map.get("subject")),
            "from": decode_rfc2047(header_map.get("from")),
            "to": decode_rfc2047(header_map.get("to")),
            "date": header_map.get("date"),
            "text_body": bodies["text"],
            "html_body": bodies["html"],
            "attachments": attachments,
            "label_ids": message.get("labelIds", []),
            "history_id": message.get("historyId"),
            "internal_date": message.get("internalDate"),
            "summary_mode": request.summary_mode,
            "bodies_omitted": request.summary_mode,
        }

    @server.tool(name="mark_as_read")
    async def mark_as_read(message_id: str, ctx: Context) -> dict[str, Any]:
        """Remove the UNREAD label from a message."""
        service = gmail_service()
        await ctx.info(f"Marking {message_id} as read.")
        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
        return {"status": "ok", "message_id": message_id, "operation": "mark_as_read"}

    @server.tool(name="mark_as_unread")
    async def mark_as_unread(message_id: str, ctx: Context) -> dict[str, Any]:
        """Add the UNREAD label to a message."""
        service = gmail_service()
        await ctx.info(f"Marking {message_id} as unread.")
        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": ["UNREAD"]},
        ).execute()
        return {"status": "ok", "message_id": message_id, "operation": "mark_as_unread"}

    @server.tool(name="move_email")
    async def move_email(request: ModifyMessageRequest, ctx: Context) -> dict[str, Any]:
        """Modify a message's labels to move/classify it across mailbox states."""
        service = gmail_service()
        await ctx.info(f"Moving message {request.message_id}.")
        result = service.users().messages().modify(
            userId="me",
            id=request.message_id,
            body={
                "addLabelIds": request.add_label_ids,
                "removeLabelIds": request.remove_label_ids,
            },
        ).execute()
        return {"status": "ok", "message": result}

    @server.tool(name="delete_email")
    async def delete_email(request: DeleteMessageRequest, ctx: Context) -> dict[str, Any]:
        """Trash or permanently delete a message based on the request mode."""
        service = gmail_service()
        if request.permanent:
            response = await ctx.elicit(
                "Permanently delete this email? This cannot be undone.",
                response_type=bool,  # type: ignore[arg-type]
            )
            if response.action != "accept" or not bool(response.data):
                return {"status": "cancelled"}
            service.users().messages().delete(userId="me", id=request.message_id).execute()
            return {"status": "ok", "mode": "permanent", "message_id": request.message_id}

        service.users().messages().trash(userId="me", id=request.message_id).execute()
        return {"status": "ok", "mode": "trash", "message_id": request.message_id}
