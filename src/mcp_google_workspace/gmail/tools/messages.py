"""Gmail message operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from fastmcp import Context, FastMCP
from googleapiclient.errors import HttpError

from ...common.async_ops import execute_google_request, require_elicitation_context
from ...common.timezone import resolve_user_timezone
from ...file_uploads import require_local_filesystem, workspace_file_upload
from ..client import gmail_service
from ..mime_utils import (
    build_email_message,
    decode_rfc2047,
    email_to_gmail_raw,
    extract_message_bodies,
)
from ..helpers import recipient_set
from ..presentation import clean_message_content, envelope, header_map, message_attachments
from ..schemas import (
    AttachmentInput,
    DeleteMessageRequest,
    ModifyMessageRequest,
    ReadEmailsRequest,
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
        attachments: list[AttachmentInput] | None = None,
        confirm_send: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Send an email with TO/CC/BCC, text/HTML body, and optional attachments."""
        request = SendEmailRequest(
            recipients=recipient_set(to=to, cc=cc, bcc=bcc),
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            attachments=attachments or [],
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
        attachment_payloads: list[dict[str, Any]] = []
        total = max(len(request.attachments), 1)
        for i, item in enumerate(request.attachments, start=1):
            if item.uploaded_file:
                uploaded = workspace_file_upload.get_file(item.uploaded_file, ctx)
                attachment_payloads.append(
                    {
                        "data": uploaded.data,
                        "filename": item.filename or uploaded.name,
                        "mime_type": item.mime_type or uploaded.mime_type,
                    }
                )
            else:
                require_local_filesystem("Gmail attachment")
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

    @server.tool(name="read_emails")
    async def read_emails(
        message_ids: list[str],
        format: Literal["metadata", "preview", "clean", "full"] = "clean",
        offset: int = 0,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read one to 100 Gmail messages with a consistent, model-friendly detail level."""
        request = ReadEmailsRequest(message_ids=message_ids, format=format, offset=offset)
        service = gmail_service()
        account_timezone = await resolve_user_timezone()
        outputs: list[dict[str, Any]] = []
        missing_message_ids: list[str] = []
        for index, message_id in enumerate(request.message_ids, start=1):
            try:
                message = await execute_google_request(
                    service.users().messages().get(userId="me", id=message_id, format="full")
                )
            except HttpError as exc:
                if getattr(exc.resp, "status", None) != 404:
                    raise
                missing_message_ids.append(message_id)
                continue
            payload = message.get("payload", {})
            headers = header_map(payload)
            output = envelope(message, account_timezone=account_timezone)
            output.update({
                "to": decode_rfc2047(headers.get("to")),
                "attachments": message_attachments(payload),
                "label_ids": message.get("labelIds", []),
                "history_id": message.get("historyId"),
                "internal_date": message.get("internalDate"),
                "format": request.format,
            })
            if request.format == "metadata":
                output["bodies_omitted"] = True
            elif request.format == "preview":
                output.update(clean_message_content(message, offset=request.offset, limit=1_000))
            elif request.format == "clean":
                output.update(clean_message_content(message, offset=request.offset))
            elif request.format == "full":
                bodies = extract_message_bodies(payload)
                output.update({
                    "text_body": bodies.get("text"),
                    "html_body": bodies.get("html"),
                    "truncated": False,
                })
            outputs.append(output)
            if ctx is not None:
                await ctx.report_progress(index, len(request.message_ids), "Messages loaded")
        return {
            "messages": outputs,
            "format": request.format,
            "missing_count": len(missing_message_ids),
            "missing_message_ids": missing_message_ids,
            "account_timezone": account_timezone,
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
