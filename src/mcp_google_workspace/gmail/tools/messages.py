"""Gmail message operations."""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import getaddresses
import re
from typing import Annotated, Any, Literal

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


_MESSAGE_ID_PATTERN = re.compile(r"<[^<>\s]+>")
_REPLY_METADATA_HEADERS = [
    "From",
    "Reply-To",
    "To",
    "Cc",
    "Subject",
    "Message-ID",
    "References",
]


def _email_addresses(*header_values: str) -> list[str]:
    """Extract mailbox addresses from one or more RFC 5322 header values."""
    return [address for _, address in getaddresses(header_values) if address]


def _deduplicate_addresses(
    addresses: list[str],
    *,
    excluded: set[str] | None = None,
) -> list[str]:
    excluded_keys = {address.casefold() for address in excluded or set()}
    seen = set(excluded_keys)
    result: list[str] = []
    for address in addresses:
        key = address.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(address)
    return result


def _normalize_message_id(value: str) -> str:
    """Return one RFC-style Message-ID suitable for reply headers."""
    match = _MESSAGE_ID_PATTERN.search(value)
    if match:
        return match.group(0)
    candidate = value.strip()
    if candidate and not any(character.isspace() for character in candidate):
        return f"<{candidate.strip('<>')}>"
    raise ValueError(
        "The source email has no valid Message-ID header and cannot be replied to safely."
    )


def _reply_references(existing: str, source_message_id: str) -> str:
    references = _MESSAGE_ID_PATTERN.findall(existing)
    if source_message_id not in references:
        references.append(source_message_id)
    return " ".join(references)


def _resolve_reply_recipients(
    headers: dict[str, str],
    *,
    own_addresses: set[str],
    reply_all: bool,
) -> tuple[list[str], list[str]]:
    """Resolve Gmail-style reply recipients while excluding the current account."""
    own_keys = {address.casefold() for address in own_addresses}
    reply_targets = _email_addresses(headers.get("reply-to", ""))
    if not reply_targets:
        reply_targets = _email_addresses(headers.get("from", ""))
    reply_targets = _deduplicate_addresses(reply_targets, excluded=own_keys)
    original_to = _deduplicate_addresses(
        _email_addresses(headers.get("to", "")), excluded=own_keys
    )
    original_cc = _deduplicate_addresses(
        _email_addresses(headers.get("cc", "")), excluded=own_keys
    )

    if not reply_all:
        recipients = reply_targets or original_to[:1] or original_cc[:1]
        if not recipients:
            raise ValueError("The source email has no external recipient to reply to.")
        return recipients, []

    if reply_targets:
        to = reply_targets
        cc = _deduplicate_addresses(
            [*original_to, *original_cc],
            excluded={*own_keys, *(address.casefold() for address in to)},
        )
    else:
        # When replying from a message the user originally sent, preserve its
        # primary recipients instead of trying to reply back to the user.
        to = original_to
        cc = _deduplicate_addresses(
            original_cc,
            excluded={*own_keys, *(address.casefold() for address in to)},
        )
    if not to:
        raise ValueError("The source email has no external recipient to reply to.")
    return to, cc


async def _prepare_attachment_payloads(
    attachments: list[AttachmentInput],
    ctx: Context | None,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    total = max(len(attachments), 1)
    for index, item in enumerate(attachments, start=1):
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
            require_local_filesystem("Gmail attachment")
            payloads.append(
                {
                    "path": item.file_path,
                    "filename": item.filename or "",
                    "mime_type": item.mime_type or "application/octet-stream",
                }
            )
        if ctx is not None:
            await ctx.report_progress(
                index, total, f"Prepared attachment {index}/{total}"
            )
    return payloads


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
        attachment_payloads = await _prepare_attachment_payloads(
            request.attachments, ctx
        )

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

    async def _send_reply(
        *,
        message_id: str,
        text_body: str | None,
        html_body: str | None,
        attachments: list[AttachmentInput],
        confirm_send: bool,
        reply_all: bool,
        ctx: Context | None,
    ) -> dict[str, Any]:
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Loading Gmail reply context for message {message_id}.")
        source = await execute_google_request(
            service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=_REPLY_METADATA_HEADERS,
            )
        )
        thread_id = str(source.get("threadId") or "")
        if not thread_id:
            raise ValueError(
                "The source email has no Gmail thread ID and cannot be replied to."
            )
        headers = header_map(source.get("payload", {}))
        source_message_id = _normalize_message_id(headers.get("message-id", ""))
        references = _reply_references(headers.get("references", ""), source_message_id)
        subject = decode_rfc2047(headers.get("subject"))

        profile = await execute_google_request(service.users().getProfile(userId="me"))
        own_addresses = {str(profile.get("emailAddress") or "")}
        send_as = await execute_google_request(
            service.users().settings().sendAs().list(userId="me")
        )
        own_addresses.update(
            str(item.get("sendAsEmail") or "")
            for item in send_as.get("sendAs", [])
            if item.get("sendAsEmail")
        )
        own_addresses.discard("")
        to, cc = _resolve_reply_recipients(
            headers,
            own_addresses=own_addresses,
            reply_all=reply_all,
        )

        if confirm_send:
            confirm_ctx = require_elicitation_context(
                ctx, "reply_all_email" if reply_all else "reply_email"
            )

            @dataclass
            class Confirmation:
                confirm: bool

            response = await confirm_ctx.elicit(
                (
                    f"{'Reply all' if reply_all else 'Reply'} to email?\n"
                    f"To: {', '.join(to)}\n"
                    f"Cc: {', '.join(cc) or '(none)'}\n"
                    f"Subject: {subject}\n"
                    f"Attachments: {len(attachments)}"
                ),
                response_type=Confirmation,  # type: ignore[arg-type]
            )
            if response.action != "accept" or not bool(
                getattr(response.data, "confirm", False)
            ):
                return {"status": "cancelled", "message": "User cancelled reply."}

        attachment_payloads = await _prepare_attachment_payloads(attachments, ctx)
        email_message = build_email_message(
            subject=subject,
            to=to,
            cc=cc,
            bcc=[],
            text_body=text_body,
            html_body=html_body,
            attachments=attachment_payloads,
            in_reply_to=source_message_id,
            references=references,
        )
        raw = email_to_gmail_raw(email_message)
        if ctx is not None:
            await ctx.info(
                f"Sending {'reply-all' if reply_all else 'reply'} through Gmail API."
            )
        sent = await execute_google_request(
            service.users()
            .messages()
            .send(
                userId="me",
                body={"raw": raw, "threadId": thread_id},
            )
        )
        return {
            "status": "sent",
            "mode": "reply_all" if reply_all else "reply",
            "replied_to_message_id": message_id,
            "message_id": sent.get("id"),
            "thread_id": sent.get("threadId"),
            "to": to,
            "cc": cc,
            "subject": subject,
            "label_ids": sent.get("labelIds", []),
        }

    @server.tool(name="reply_email")
    async def reply_email(
        message_id: str,
        text_body: str | None = None,
        html_body: str | None = None,
        attachments: list[AttachmentInput] | None = None,
        confirm_send: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Reply to one Gmail message in its existing thread, addressing its sender."""
        reply = await _send_reply(
            message_id=message_id,
            text_body=text_body,
            html_body=html_body,
            attachments=attachments or [],
            confirm_send=confirm_send,
            reply_all=False,
            ctx=ctx,
        )
        return {
            "status": reply.get("status"),
            "mode": reply.get("mode"),
            "replied_to_message_id": reply.get("replied_to_message_id"),
            "message_id": reply.get("message_id"),
            "thread_id": reply.get("thread_id"),
            "to": reply.get("to"),
            "cc": reply.get("cc"),
            "subject": reply.get("subject"),
            "label_ids": reply.get("label_ids"),
            "message": reply.get("message"),
        }

    @server.tool(name="reply_all_email")
    async def reply_all_email(
        message_id: str,
        text_body: str | None = None,
        html_body: str | None = None,
        attachments: list[AttachmentInput] | None = None,
        confirm_send: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Reply to all external participants in a Gmail message's existing thread."""
        reply = await _send_reply(
            message_id=message_id,
            text_body=text_body,
            html_body=html_body,
            attachments=attachments or [],
            confirm_send=confirm_send,
            reply_all=True,
            ctx=ctx,
        )
        return {
            "status": reply.get("status"),
            "mode": reply.get("mode"),
            "replied_to_message_id": reply.get("replied_to_message_id"),
            "message_id": reply.get("message_id"),
            "thread_id": reply.get("thread_id"),
            "to": reply.get("to"),
            "cc": reply.get("cc"),
            "subject": reply.get("subject"),
            "label_ids": reply.get("label_ids"),
            "message": reply.get("message"),
        }

    @server.tool(name="read_emails")
    async def read_emails(
        message_ids: list[str],
        format: Literal["metadata", "preview", "clean", "full"] = "clean",
        offset: Annotated[
            int,
            (
                "Character offset into the cleaned body text to resume from, for paginating through "
                "'preview'/'clean' format bodies via the response's next_offset; not a message index."
            ),
        ] = 0,
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
        if not request.add_label_ids and not request.remove_label_ids:
            raise ValueError("At least one of add_label_ids/remove_label_ids must be provided.")
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
