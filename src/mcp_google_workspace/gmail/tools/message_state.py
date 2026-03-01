"""Additional Gmail message-state tools."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from ..client import gmail_service
from ..schemas import MarkNotSpamRequest, MessageIdRequest


def register(server: FastMCP) -> None:
    @server.tool(name="untrash_email")
    async def untrash_email(
        message_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Restore a previously trashed message."""
        request = MessageIdRequest(message_id=message_id)
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Restoring message {request.message_id} from trash.")
        restored = service.users().messages().untrash(
            userId="me",
            id=request.message_id,
        ).execute()
        return {"status": "ok", "message": restored}

    @server.tool(name="mark_as_spam")
    async def mark_as_spam(
        message_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Mark a message as spam (adds SPAM label, removes INBOX)."""
        request = MessageIdRequest(message_id=message_id)
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Marking message {request.message_id} as spam.")
        result = service.users().messages().modify(
            userId="me",
            id=request.message_id,
            body={
                "addLabelIds": ["SPAM"],
                "removeLabelIds": ["INBOX"],
            },
        ).execute()
        return {"status": "ok", "message": result}

    @server.tool(name="mark_as_not_spam")
    async def mark_as_not_spam(
        message_id: str,
        add_to_inbox: bool = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Remove SPAM label and optionally add message back to INBOX."""
        request = MarkNotSpamRequest(message_id=message_id, add_to_inbox=add_to_inbox)
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Marking message {request.message_id} as not spam.")
        add_label_ids = ["INBOX"] if request.add_to_inbox else []
        result = service.users().messages().modify(
            userId="me",
            id=request.message_id,
            body={
                "addLabelIds": add_label_ids,
                "removeLabelIds": ["SPAM"],
            },
        ).execute()
        return {"status": "ok", "message": result}
