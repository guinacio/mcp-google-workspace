"""Gmail message operations."""

from __future__ import annotations

import logging

from email.utils import getaddresses
import re
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from googleapiclient.errors import HttpError

from ...common.async_ops import execute_google_request
from ...common.confirmation import confirm_destructive_action
from ...common.repeat_safety import note_reconciliation
from ...common.timezone import resolve_user_timezone
from ...file_uploads import require_local_filesystem, workspace_file_upload
from ..client import gmail_service
from ..mime_utils import (
    build_email_message,
    decode_rfc2047,
    email_to_gmail_raw,
    extract_message_bodies,
    stamp_message_id,
)
from ..helpers import (
    ATTACHMENTS_DESCRIPTION as _ATTACHMENTS,
    HTML_BODY_DESCRIPTION as _HTML_BODY,
    REPLY_CONFIRM_SEND_DESCRIPTION as _REPLY_CONFIRM_SEND,
    TEXT_BODY_DESCRIPTION as _TEXT_BODY,
    recipient_set,
)
from ..presentation import (
    PREVIEW_LIMIT_CHARS,
    clean_message_content,
    envelope,
    header_map,
    message_attachments,
)
from ..schemas import (
    AttachmentInput,
    DeleteMessageRequest,
    ModifyMessageRequest,
    ReadEmailsRequest,
    SendEmailRequest,
)

LOGGER = logging.getLogger(__name__)


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
        subject: Annotated[str, "Subject line; non-ASCII text is encoded automatically."],
        to: Annotated[
            list[str], "Primary recipient email addresses, e.g. ['ana@example.com']; each must be a valid address."
        ],
        cc: Annotated[list[str] | None, "Carbon-copy recipient email addresses, visible to every recipient."] = None,
        bcc: Annotated[
            list[str] | None, "Blind-copy recipient email addresses, hidden from the other recipients."
        ] = None,
        text_body: Annotated[str | None, _TEXT_BODY] = None,
        html_body: Annotated[str | None, _HTML_BODY] = None,
        attachments: Annotated[list[AttachmentInput] | None, _ATTACHMENTS] = None,
        confirm_send: Annotated[
            bool,
            (
                "When true, the user is asked to approve the send (shown To, Subject and the attachment "
                "count) before anything is sent: declining returns status 'cancelled', and a client that "
                "cannot ask makes the call fail unsent. Default false sends immediately."
            ),
        ] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Compose and send a new Gmail message, with a plain-text and/or HTML body and optional attachments. To answer an existing message inside its thread use gmail_reply_email or gmail_reply_all_email; to save mail for review instead of sending it, use gmail_create_draft. Returns status 'sent' with the new message_id, thread_id and label_ids, or status 'cancelled' if confirm_send asked and the user declined. Sends immediately unless confirm_send is true, and by default a send to 10 or more recipients in total must first go through prepare_workspace_action."""
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
            if not await confirm_destructive_action(
                ctx,
                "send_email",
                (
                    f"Send email?\n"
                    f"To: {', '.join(str(v) for v in request.recipients.to)}\n"
                    f"Subject: {request.subject}\n"
                    f"Attachments: {len(request.attachments)}"
                ),
                explicit_confirm_field=True,
            ):
                return {"status": "cancelled", "message": "User cancelled send."}

        LOGGER.debug("Building MIME email payload.")
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
        # users.messages.send is not repeat safe: register the Message-ID so
        # an unknown outcome can be checked in Sent instead of resent.
        note_reconciliation(
            {"kind": "gmail_sent", "rfc822_message_id": stamp_message_id(email_message)}
        )
        raw = email_to_gmail_raw(email_message)
        LOGGER.debug("Sending email through Gmail API.")
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
        LOGGER.debug(f"Loading Gmail reply context for message {message_id}.")
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
            if not await confirm_destructive_action(
                ctx,
                "reply_all_email" if reply_all else "reply_email",
                (
                    f"{'Reply all' if reply_all else 'Reply'} to email?\n"
                    f"To: {', '.join(to)}\n"
                    f"Cc: {', '.join(cc) or '(none)'}\n"
                    f"Subject: {subject}\n"
                    f"Attachments: {len(attachments)}"
                ),
                explicit_confirm_field=True,
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
        note_reconciliation(
            {"kind": "gmail_sent", "rfc822_message_id": stamp_message_id(email_message)}
        )
        raw = email_to_gmail_raw(email_message)
        LOGGER.debug(
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
        text_body: Annotated[str | None, _TEXT_BODY] = None,
        html_body: Annotated[str | None, _HTML_BODY] = None,
        attachments: Annotated[list[AttachmentInput] | None, _ATTACHMENTS] = None,
        confirm_send: Annotated[bool, _REPLY_CONFIRM_SEND] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Reply to the sender of one Gmail message, inside its existing thread: the subject is reused unchanged and In-Reply-To and References point at that message. It goes to the message's Reply-To address, else its From, leaving out the account's own and send-as addresses (on your own sent mail it goes to the first original recipient instead); gmail_reply_all_email also copies everyone else, and gmail_send_email starts a new conversation. Returns status 'sent' with the new message_id, thread_id, to, cc and subject. Sends immediately unless confirm_send is true, which asks the user first and returns status 'cancelled' if declined."""
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
            "to": reply.get("to") or [],
            "cc": reply.get("cc") or [],
            "subject": reply.get("subject"),
            "label_ids": reply.get("label_ids") or [],
            "message": reply.get("message"),
        }

    @server.tool(name="reply_all_email")
    async def reply_all_email(
        message_id: str,
        text_body: Annotated[str | None, _TEXT_BODY] = None,
        html_body: Annotated[str | None, _HTML_BODY] = None,
        attachments: Annotated[list[AttachmentInput] | None, _ATTACHMENTS] = None,
        confirm_send: Annotated[bool, _REPLY_CONFIRM_SEND] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Reply to everyone on one Gmail message, inside its existing thread: the subject is reused unchanged and In-Reply-To and References point at that message. To is its Reply-To address (else From) and Cc every other original To and Cc address, leaving out the account's own and send-as addresses; on your own sent mail, To and Cc keep the original recipients. Use gmail_reply_email to answer only the sender. Returns status 'sent' with the new message_id, thread_id, to, cc and subject. Sends immediately unless confirm_send is true, which asks the user first and returns status 'cancelled' if declined."""
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
            "to": reply.get("to") or [],
            "cc": reply.get("cc") or [],
            "subject": reply.get("subject"),
            "label_ids": reply.get("label_ids") or [],
            "message": reply.get("message"),
        }

    @server.tool(name="read_emails")
    async def read_emails(
        message_ids: list[str],
        format: Annotated[
            Literal["metadata", "preview", "clean", "full"],
            (
                "Body detail: 'metadata' omits the body; 'preview' returns the first 1,000 characters "
                "of the cleaned body text; 'clean' returns the cleaned body text in pages of about "
                "8,000 characters; 'full' returns the whole cleaned body unpaged plus the original "
                "text/plain part (text_body) and, with include_html, the HTML part."
            ),
        ] = "clean",
        offset: Annotated[
            int,
            (
                "Character offset into the cleaned body text to resume from: the next_offset of a "
                "truncated 'preview' or 'clean' result; not a message index. It applies to every message "
                "in the call, so continue one message at a time; 'full' ignores it."
            ),
        ] = 0,
        include_html: Annotated[
            bool,
            (
                "With format='full' only: also return the original HTML part as html_body. Set it "
                "only when the markup itself is needed (layout, images, exact link targets with "
                "their query strings); newsletter HTML is often tens of kilobytes. When false, "
                "html_body is null and the readable text is in body."
            ),
        ] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read the content of one to 100 Gmail messages by ID, all at the same detail level. Use it after gmail_search_emails, gmail_get_mail_digest or gmail_check_mail_updates, which return only snippets; gmail_get_thread reads a whole conversation at once. Returns messages (id, thread_id, from, to, subject, date, snippet, label_ids, attachments and, by format, body with truncated and next_offset), missing_message_ids for IDs that no longer exist instead of failing the call, and account_timezone. Attachments here carry no download ID: get it from gmail_list_attachments."""
        request = ReadEmailsRequest(
            message_ids=message_ids, format=format, offset=offset, include_html=include_html
        )
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
                output.update(
                    clean_message_content(message, offset=request.offset, limit=PREVIEW_LIMIT_CHARS)
                )
            elif request.format == "clean":
                output.update(clean_message_content(message, offset=request.offset))
            elif request.format == "full":
                bodies = extract_message_bodies(payload)
                output.update(clean_message_content(message, limit=None))
                output.update({
                    "text_body": bodies.get("text"),
                    "html_body": bodies.get("html") if request.include_html else None,
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
        """Mark one Gmail message as read by removing its UNREAD label. To mark many messages use gmail_batch_modify (remove_label_ids ['UNREAD']), and for a whole conversation gmail_modify_thread. Returns status 'ok' with the message_id and operation 'mark_as_read'. Undo with gmail_mark_as_unread."""
        service = gmail_service()
        LOGGER.debug(f"Marking {message_id} as read.")
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
        """Mark one Gmail message as unread by adding its UNREAD label back. To mark many messages use gmail_batch_modify (add_label_ids ['UNREAD']), and for a whole conversation gmail_modify_thread. Returns status 'ok' with the message_id and operation 'mark_as_unread'. Undo with gmail_mark_as_read."""
        service = gmail_service()
        LOGGER.debug(f"Marking {message_id} as unread.")
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
        """Move one Gmail message by changing its labels: archive it (remove INBOX), file it under a label (add a user label ID, remove INBOX) or put it back in the inbox (add INBOX); at least one label list is required. It makes the same Gmail call as gmail_apply_labels and differs only in adding status 'ok' to the result; for many messages use gmail_batch_modify, for a conversation gmail_modify_thread, and for Trash gmail_delete_email. Returns status 'ok' and the updated Gmail message (id, threadId, labelIds) under message."""
        request = ModifyMessageRequest(
            message_id=message_id,
            add_label_ids=add_label_ids or [],
            remove_label_ids=remove_label_ids or [],
        )
        if not request.add_label_ids and not request.remove_label_ids:
            raise ValueError("At least one of add_label_ids/remove_label_ids must be provided.")
        service = gmail_service()
        LOGGER.debug(f"Moving message {request.message_id}.")
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
        permanent: Annotated[
            bool,
            (
                "When true, delete the message for good instead of moving it to Trash; the user is asked "
                "to confirm first, and a client that cannot ask makes the call fail without deleting. "
                "Default false moves it to Trash, reversible with gmail_untrash_email."
            ),
        ] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Move one Gmail message to Trash, or with permanent=true delete it for good. For many messages use gmail_batch_delete, and for a whole conversation gmail_trash_thread or gmail_delete_thread. Returns status 'ok', mode ('trash' or 'permanent') and message_id, or status 'cancelled' if the user declines. Trash is reversible with gmail_untrash_email (Gmail empties Trash after 30 days); permanent deletion asks the user to confirm and cannot be undone."""
        request = DeleteMessageRequest(message_id=message_id, permanent=permanent)
        service = gmail_service()
        if request.permanent:
            if not await confirm_destructive_action(
                ctx,
                "delete_email",
                "Permanently delete this email? This cannot be undone.",
            ):
                return {"status": "cancelled"}
            await execute_google_request(
                service.users().messages().delete(userId="me", id=request.message_id)
            )
            return {"status": "ok", "mode": "permanent", "message_id": request.message_id}

        await execute_google_request(service.users().messages().trash(userId="me", id=request.message_id))
        return {"status": "ok", "mode": "trash", "message_id": request.message_id}
