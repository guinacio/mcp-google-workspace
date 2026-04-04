"""Gmail message operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ..client import gmail_service
from ..mime_utils import (
    build_email_message,
    decode_rfc2047,
    email_to_gmail_raw,
    extract_message_bodies,
    flatten_parts,
)
from ..schemas import (
    AttachmentInput,
    DeleteMessageRequest,
    ModifyMessageRequest,
    ReadEmailRequest,
    RecipientSet,
    SendEmailRequest,
)


def _recipient_set(
    *,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> RecipientSet:
    return RecipientSet(
        to=to or [],
        cc=cc or [],
        bcc=bcc or [],
    )


def _attachment_inputs(items: list[dict[str, Any]] | None) -> list[AttachmentInput]:
    return [AttachmentInput.model_validate(item) for item in (items or [])]


def _require_elicitation_context(ctx: Context | None, action_name: str) -> Context:
    if ctx is None:
        raise RuntimeError(f"{action_name} requires MCP context for user confirmation.")
    return ctx


def _header_value(header: dict[str, Any], key: str) -> str:
    value = header.get(key)
    return value if isinstance(value, str) else ""


def register(server: FastMCP) -> None:
    @server.tool(name="send_email")
    async def send_email(
        subject: str,
        to: list[str],
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        text_body: str | None = None,
        html_body: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        confirm_send: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Send an email with TO/CC/BCC, text/HTML body, and optional attachments."""
        request = SendEmailRequest(
            recipients=_recipient_set(to=to, cc=cc, bcc=bcc),
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            attachments=_attachment_inputs(attachments),
            confirm_send=confirm_send,
        )
        service = gmail_service()
        if request.confirm_send:
            confirm_ctx = _require_elicitation_context(ctx, "send_email")

            @dataclass
            class Confirmation:
                confirm: bool

            response = await confirm_ctx.elicit(
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

        if ctx is not None:
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
            if ctx is not None:
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
        if ctx is not None:
            await ctx.info("Sending email through Gmail API.")
        sent = await execute_google_request(
            service.users().messages().send(userId="me", body={"raw": raw})
        )
        return {
            "status": "sent",
            "message_id": sent.get("id"),
            "thread_id": sent.get("threadId"),
            "label_ids": sent.get("labelIds", []),
        }

    @server.tool(name="read_email")
    async def read_email(
        message_id: str,
        summary_mode: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Fetch one Gmail message by ID."""
        request = ReadEmailRequest(message_id=message_id, summary_mode=summary_mode)
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Reading message {request.message_id}.")
        message = await execute_google_request(
            service.users()
            .messages()
            .get(userId="me", id=request.message_id, format="full")
        )
        payload = message.get("payload", {})
        headers = payload.get("headers", [])
        header_map = {
            _header_value(header, "name").lower(): _header_value(header, "value")
            for header in headers
            if isinstance(header, dict)
        }
        if request.summary_mode:
            bodies: dict[str, str | None] = {"text": None, "html": None}
        else:
            extracted_bodies = extract_message_bodies(payload)
            bodies = {
                "text": extracted_bodies.get("text"),
                "html": extracted_bodies.get("html"),
            }
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

        if ctx is not None:
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
        await execute_google_request(
            service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["UNREAD"]},
            )
        )
        return {"status": "ok", "message_id": message_id, "operation": "mark_as_read"}

    @server.tool(name="mark_as_unread")
    async def mark_as_unread(message_id: str, ctx: Context) -> dict[str, Any]:
        """Add the UNREAD label to a message."""
        service = gmail_service()
        await ctx.info(f"Marking {message_id} as unread.")
        await execute_google_request(
            service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"addLabelIds": ["UNREAD"]},
            )
        )
        return {"status": "ok", "message_id": message_id, "operation": "mark_as_unread"}

    @server.tool(name="move_email")
    async def move_email(
        message_id: str,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Modify a message's labels to move/classify it across mailbox states."""
        request = ModifyMessageRequest(
            message_id=message_id,
            add_label_ids=add_label_ids or [],
            remove_label_ids=remove_label_ids or [],
        )
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Moving message {request.message_id}.")
        result = await execute_google_request(
            service.users().messages().modify(
                userId="me",
                id=request.message_id,
                body={
                    "addLabelIds": request.add_label_ids,
                    "removeLabelIds": request.remove_label_ids,
                },
            )
        )
        return {"status": "ok", "message": result}

    @server.tool(name="delete_email")
    async def delete_email(
        message_id: str,
        permanent: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Trash or permanently delete a message based on the request mode."""
        request = DeleteMessageRequest(message_id=message_id, permanent=permanent)
        service = gmail_service()
        if request.permanent:
            confirm_ctx = _require_elicitation_context(ctx, "delete_email")
            response = await confirm_ctx.elicit(
                "Permanently delete this email? This cannot be undone.",
                response_type=bool,  # type: ignore[arg-type]
            )
            if response.action != "accept" or not bool(response.data):
                return {"status": "cancelled"}
            await execute_google_request(
                service.users().messages().delete(userId="me", id=request.message_id)
            )
            return {"status": "ok", "mode": "permanent", "message_id": request.message_id}

        await execute_google_request(service.users().messages().trash(userId="me", id=request.message_id))
        return {"status": "ok", "mode": "trash", "message_id": request.message_id}
