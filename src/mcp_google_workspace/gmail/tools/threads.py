"""Gmail thread tools."""

from __future__ import annotations

import logging

from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ...common.timezone import resolve_user_timezone
from ..client import gmail_service
from ..presentation import clean_message_content, envelope
from ..schemas import GetThreadRequest

LOGGER = logging.getLogger(__name__)


def register(server: FastMCP) -> None:
    @server.tool(name="get_thread")
    async def get_thread(
        thread_id: str,
        format: Annotated[
            Literal["full", "metadata", "minimal", "clean"],
            (
                "'clean' (default): latest is the newest message's summary plus its cleaned body text, paged "
                "at about 8,000 characters (continue with gmail_read_emails and offset=next_offset), and "
                "prior_messages gives from, date, subject and snippet of each earlier message. The others "
                "return Gmail's raw thread resource under thread: 'full' with every message's headers and "
                "base64url-encoded body parts, 'metadata' with headers only, 'minimal' with IDs and labels only."
            ),
        ] = "clean",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read one Gmail conversation by thread ID. The default format 'clean' gives the newest message with its cleaned body plus a one-line summary of each earlier message; 'full', 'metadata' and 'minimal' return Gmail's raw thread resource instead. Use gmail_read_emails to read particular messages in full or continue a truncated body, and gmail_search_emails (group_by_thread=true lists conversations) to find threads. Returns thread_id, count (messages in the thread) and either latest, prior_messages and account_timezone ('clean') or thread (the other formats, whose historyId is Gmail's)."""
        api_format: Literal["full", "metadata", "minimal"] = "full" if format == "clean" else format
        request = GetThreadRequest(
            thread_id=thread_id,
            format=api_format,
        )
        service = gmail_service()
        account_timezone = await resolve_user_timezone()
        LOGGER.debug(f"Reading thread {request.thread_id}.")
        thread = await execute_google_request(
            service.users()
            .threads()
            .get(
                userId="me",
                id=request.thread_id,
                format=request.format,
            )
        )
        messages = thread.get("messages", [])
        if format == "clean":
            items = [envelope(item, account_timezone=account_timezone) for item in messages]
            latest = messages[-1] if messages else None
            return {
                "thread_id": thread.get("id"),
                "count": len(messages),
                "latest": {
                    **envelope(latest, account_timezone=account_timezone),
                    **clean_message_content(latest),
                } if latest else None,
                "prior_messages": [
                    {"from": item["from"], "date": item["date"], "subject": item["subject"], "snippet": item["snippet"]}
                    for item in items[:-1]
                ],
                "account_timezone": account_timezone,
            }
        return {
            "thread_id": thread.get("id"),
            "count": len(messages),
            "thread": thread,
        }
