"""Additional Gmail message-state tools."""

from __future__ import annotations

import logging

from typing import Annotated, Any

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ..client import gmail_service
from ..schemas import MarkNotSpamRequest, MessageIdRequest

LOGGER = logging.getLogger(__name__)


def register(server: FastMCP) -> None:
    @server.tool(name="untrash_email")
    async def untrash_email(
        message_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Restore one Gmail message from Trash, undoing gmail_delete_email (without permanent) for that message. For a whole conversation use gmail_untrash_thread; mail deleted with permanent=true or gmail_delete_thread cannot be restored. Returns status 'ok' and the restored Gmail message (id, threadId, labelIds) under message."""
        request = MessageIdRequest(message_id=message_id)
        service = gmail_service()
        LOGGER.debug(f"Restoring message {request.message_id} from trash.")
        restored = await execute_google_request(
            service.users().messages().untrash(
                userId="me",
                id=request.message_id,
            )
        )
        return {"status": "ok", "message": restored}

    @server.tool(name="mark_as_spam")
    async def mark_as_spam(
        message_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Move one Gmail message to Spam: adds the SPAM label and removes INBOX. To get a message out of the inbox without calling it spam, archive it with gmail_move_email (remove_label_ids ['INBOX']); for many messages use gmail_batch_modify with the same labels. Returns status 'ok' and the updated Gmail message (id, threadId, labelIds) under message. Undo with gmail_mark_as_not_spam."""
        request = MessageIdRequest(message_id=message_id)
        service = gmail_service()
        LOGGER.debug(f"Marking message {request.message_id} as spam.")
        result = await execute_google_request(
            service.users().messages().modify(
                userId="me",
                id=request.message_id,
                body={
                    "addLabelIds": ["SPAM"],
                    "removeLabelIds": ["INBOX"],
                },
            )
        )
        return {"status": "ok", "message": result}

    @server.tool(name="mark_as_not_spam")
    async def mark_as_not_spam(
        message_id: str,
        add_to_inbox: Annotated[
            bool,
            (
                "When true (default), also adds INBOX so the message is back in the inbox; when false, "
                "only SPAM is removed and the message stays archived under All Mail and its other labels."
            ),
        ] = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Take one Gmail message out of Spam: removes the SPAM label and, unless add_to_inbox is false, adds INBOX. It undoes gmail_mark_as_spam; a message in Trash comes back with gmail_untrash_email instead, and for many messages use gmail_batch_modify. Returns status 'ok' and the updated Gmail message (id, threadId, labelIds) under message."""
        request = MarkNotSpamRequest(message_id=message_id, add_to_inbox=add_to_inbox)
        service = gmail_service()
        LOGGER.debug(f"Marking message {request.message_id} as not spam.")
        add_label_ids = ["INBOX"] if request.add_to_inbox else []
        result = await execute_google_request(
            service.users().messages().modify(
                userId="me",
                id=request.message_id,
                body={
                    "addLabelIds": add_label_ids,
                    "removeLabelIds": ["SPAM"],
                },
            )
        )
        return {"status": "ok", "message": result}
