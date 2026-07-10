"""Gmail message operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request, require_elicitation_context
from ...common.timezone import resolve_user_timezone
from ..client import gmail_service
from ..mime_utils import (
    build_email_message,
    decode_rfc2047,
    email_to_gmail_raw,
    extract_message_bodies,
)
from ..helpers import attachment_inputs, recipient_set
from ..presentation import clean_message_content, envelope, header_map, message_attachments
from ..schemas import (
    DeleteMessageRequest,
    ModifyMessageRequest,
    ReadEmailRequest,
    SendEmailRequest,
)


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
            recipients=recipient_set(to=to, cc=cc, bcc=bcc),
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            attachments=attachment_inputs(attachments),
            confirm_send=confirm_send,
        )
        service = gmail_service()
        if request.confirm_send:
            confirm_ctx = require_elicitation_context(ctx, "send_email")

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
        format: Literal["metadata", "preview", "clean", "full"] = "clean",
        offset: int = 0,
        summary_mode: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Fetch one Gmail message; clean text is the default, full raw HTML is opt-in."""
        request = ReadEmailRequest(
            message_id=message_id, format=format, offset=offset, summary_mode=summary_mode
        )
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Reading message {request.message_id}.")
        message = await execute_google_request(
            service.users()
            .messages()
            .get(userId="me", id=request.message_id, format="full")
        )
        account_timezone = await resolve_user_timezone()
        payload = message.get("payload", {})
        headers = header_map(payload)
        output = envelope(message, account_timezone=account_timezone)
        effective_format = "metadata" if request.summary_mode else request.format
        attachments = message_attachments(payload)

        if ctx is not None:
            await ctx.info(f"Parsed MIME structure with {len(attachments)} attachment(s).")
        output.update({
            "to": decode_rfc2047(headers.get("to")),
            "attachments": attachments,
            "label_ids": message.get("labelIds", []),
            "history_id": message.get("historyId"),
            "internal_date": message.get("internalDate"),
            "format": effective_format,
        })
        if effective_format == "metadata":
            output["bodies_omitted"] = True
        if effective_format == "preview":
            output.update(clean_message_content(message, offset=request.offset, limit=1_000))
        elif effective_format == "clean":
            output.update(clean_message_content(message, offset=request.offset))
        elif effective_format == "full":
            bodies = extract_message_bodies(payload)
            output.update({"text_body": bodies.get("text"), "html_body": bodies.get("html"), "truncated": False})
        return output

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
            confirm_ctx = require_elicitation_context(ctx, "delete_email")
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
