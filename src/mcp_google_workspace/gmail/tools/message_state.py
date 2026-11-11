"""Gmail message state: labels, read, archive, spam, restore, and deletion."""

from __future__ import annotations

import logging

from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ..client import gmail_service
from ..label_resolver import LabelResolver

LOGGER = logging.getLogger(__name__)

# messages.batchModify and messages.batchDelete accept at most 1,000 IDs.
_MAX_MESSAGE_IDS = 1000

_MESSAGE_IDS = (
    "Gmail message IDs to change, 1 to 1,000, from gmail_search_emails, gmail_get_mail_digest or "
    "gmail_read_emails; give message_ids or thread_id, not both."
)
_THREAD_ID = (
    "Gmail thread ID (thread_id from search or read results): the change applies to every message of "
    "that conversation; give message_ids or thread_id, not both."
)
_DRY_RUN = (
    "True previews the call instead of running it: nothing is sent to Google and the user is not "
    "asked. Returns status 'dry_run', preview, action, count (distinct message IDs; null for a "
    "thread_id), requested_count (IDs as given) and consent_required: whether the real call would "
    "ask the user to confirm."
)
_LABEL_VALUES = (
    "Label names as shown in Gmail (e.g. 'Clients/Acme') or label IDs; system labels such as STARRED, "
    "IMPORTANT or INBOX are matched case-insensitively. An unknown or ambiguous name fails with "
    "not_found and the candidate labels; no label is created."
)


def _target(message_ids: list[str] | None, thread_id: str | None) -> tuple[list[str], str | None]:
    ids = [message_id for message_id in (message_ids or []) if message_id]
    if bool(ids) == bool(thread_id):
        raise ValueError("Give exactly one of message_ids (non-empty) or thread_id.")
    if len(ids) > _MAX_MESSAGE_IDS:
        raise ValueError(f"message_ids accepts at most {_MAX_MESSAGE_IDS} IDs per call.")
    return list(dict.fromkeys(ids)), thread_id


def _message_summary(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": message.get("id"),
        "thread_id": message.get("threadId"),
        "label_ids": message.get("labelIds", []),
    }


def register(server: FastMCP) -> None:
    @server.tool(name="modify_messages")
    async def modify_messages(
        message_ids: Annotated[list[str] | None, _MESSAGE_IDS] = None,
        thread_id: Annotated[str | None, _THREAD_ID] = None,
        add_labels: Annotated[list[str] | None, "Labels to add. " + _LABEL_VALUES] = None,
        remove_labels: Annotated[list[str] | None, "Labels to remove. " + _LABEL_VALUES] = None,
        mark_read: Annotated[
            bool | None, "True marks the mail read (removes UNREAD), false marks it unread (adds UNREAD)."
        ] = None,
        archive: Annotated[
            bool | None,
            "True archives the mail (removes INBOX; it stays in All Mail), false moves it back to the inbox (adds INBOX).",
        ] = None,
        spam: Annotated[
            bool | None,
            (
                "True reports the mail as spam (adds SPAM, removes INBOX); false takes it out of Spam "
                "(removes SPAM and adds INBOX, unless archive is true)."
            ),
        ] = None,
        untrash: Annotated[
            bool,
            (
                "True first restores the mail from Trash, undoing gmail_delete with mode 'trash'; "
                "permanently deleted mail cannot be restored."
            ),
        ] = False,
        dry_run: Annotated[bool, _DRY_RUN] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Change the state of Gmail messages or of one whole conversation: add or remove labels by name or ID, mark read or unread, archive or move back to the inbox, report spam or not spam, or restore from Trash, in one call. Use gmail_delete to move mail to Trash or delete it, and gmail_manage_labels to create or rename labels. Returns status 'ok', the label IDs added and removed, untrashed, processed and messages (id, thread_id, label_ids) when Gmail reports them (one message or a thread). Changing 10 or more message_ids asks the user to confirm first."""
        if dry_run:
            raise RuntimeError("dry_run is answered by the consent gate before the tool body runs.")
        ids, thread = _target(message_ids, thread_id)
        service = gmail_service()
        resolver = LabelResolver(service, execute_google_request)
        add = await resolver.resolve_all(add_labels)
        remove = await resolver.resolve_all(remove_labels)

        def toggle(value: bool | None, label: str, *, when_true: list[str], when_false: list[str]) -> None:
            if value is True:
                when_true.append(label)
            elif value is False:
                when_false.append(label)

        toggle(mark_read, "UNREAD", when_true=remove, when_false=add)
        toggle(archive, "INBOX", when_true=remove, when_false=add)
        if spam is True:
            add.append("SPAM")
            remove.append("INBOX")
        elif spam is False:
            remove.append("SPAM")
            if archive is not True:
                add.append("INBOX")
        add = list(dict.fromkeys(add))
        remove = list(dict.fromkeys(remove))
        conflicting = sorted(set(add) & set(remove))
        if conflicting:
            raise ValueError(f"Labels cannot be added and removed in one call: {', '.join(conflicting)}.")
        if not add and not remove and not untrash:
            raise ValueError(
                "Nothing to change: give add_labels, remove_labels, mark_read, archive, spam or untrash."
            )

        messages: list[dict[str, Any]] = []
        processed: int | None = len(ids)
        if thread is not None:
            LOGGER.debug(f"Modifying thread {thread}.")
            result: dict[str, Any] = {}
            if untrash:
                result = await execute_google_request(
                    service.users().threads().untrash(userId="me", id=thread)
                )
            if add or remove:
                result = await execute_google_request(
                    service.users()
                    .threads()
                    .modify(userId="me", id=thread, body={"addLabelIds": add, "removeLabelIds": remove})
                )
            messages = [_message_summary(item) for item in result.get("messages", []) if isinstance(item, dict)]
            processed = len(messages) if messages else None
        else:
            LOGGER.debug(f"Modifying {len(ids)} message(s).")
            restored: dict[str, Any] = {}
            if untrash:
                for index, message_id in enumerate(ids, start=1):
                    restored = await execute_google_request(
                        service.users().messages().untrash(userId="me", id=message_id)
                    )
                    if ctx is not None and len(ids) > 1:
                        await ctx.report_progress(index, len(ids), f"Restored {index}/{len(ids)} from Trash")
            if (add or remove) and len(ids) == 1:
                restored = await execute_google_request(
                    service.users()
                    .messages()
                    .modify(userId="me", id=ids[0], body={"addLabelIds": add, "removeLabelIds": remove})
                )
            elif add or remove:
                # One request for many messages; Gmail returns no per-message result.
                await execute_google_request(
                    service.users()
                    .messages()
                    .batchModify(userId="me", body={"ids": ids, "addLabelIds": add, "removeLabelIds": remove})
                )
                restored = {}
            if len(ids) == 1 and restored:
                messages = [_message_summary(restored)]
        return {
            "status": "ok",
            "message_ids": ids or None,
            "thread_id": thread,
            "added_label_ids": add,
            "removed_label_ids": remove,
            "untrashed": untrash,
            "processed": processed,
            "messages": messages,
        }

    @server.tool(name="delete")
    async def delete(
        message_ids: Annotated[
            list[str] | None,
            "Gmail message IDs to delete, 1 to 1,000; give message_ids or thread_id, not both.",
        ] = None,
        thread_id: Annotated[
            str | None,
            "Gmail thread ID: every message of that conversation is deleted; give message_ids or thread_id, not both.",
        ] = None,
        mode: Annotated[
            Literal["trash", "permanent"],
            (
                "'trash' (default) moves the mail to Trash, reversible with gmail_modify_messages untrash=true "
                "until Gmail empties Trash after 30 days; 'permanent' deletes it for good after the user confirms."
            ),
        ] = "trash",
        dry_run: Annotated[bool, _DRY_RUN] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Delete Gmail messages or one whole conversation: move them to Trash (default) or, with mode 'permanent', delete them for good. Prefer Trash, which gmail_modify_messages with untrash=true can undo; to only hide mail from the inbox, archive it with gmail_modify_messages instead. Returns status 'ok', mode, message_ids or thread_id and processed, or status 'cancelled' if the user declines. Permanent deletion, and trashing 10 or more messages, asks the user to confirm first; permanent deletion cannot be undone. Trashing several messages goes one by one, so a failure part-way leaves the earlier ones in Trash."""
        if dry_run:
            raise RuntimeError("dry_run is answered by the consent gate before the tool body runs.")
        ids, thread = _target(message_ids, thread_id)
        service = gmail_service()
        outcome: dict[str, Any] = {
            "status": "ok",
            "mode": mode,
            "message_ids": ids or None,
            "thread_id": thread,
            "processed": len(ids) if ids else None,
        }
        if thread is not None:
            if mode == "permanent":
                LOGGER.debug(f"Permanently deleting thread {thread}.")
                await execute_google_request(service.users().threads().delete(userId="me", id=thread))
            else:
                LOGGER.debug(f"Moving thread {thread} to trash.")
                trashed = await execute_google_request(service.users().threads().trash(userId="me", id=thread))
                outcome["processed"] = len(trashed.get("messages", [])) or None
            return outcome
        if mode == "permanent":
            if len(ids) == 1:
                await execute_google_request(service.users().messages().delete(userId="me", id=ids[0]))
            else:
                await execute_google_request(
                    service.users().messages().batchDelete(userId="me", body={"ids": ids})
                )
            return outcome
        for index, message_id in enumerate(ids, start=1):
            await execute_google_request(service.users().messages().trash(userId="me", id=message_id))
            if ctx is not None and len(ids) > 1:
                await ctx.report_progress(index, len(ids), f"Moved {index}/{len(ids)} to trash")
        return outcome
