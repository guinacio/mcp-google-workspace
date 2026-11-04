"""Gmail thread tools."""

from __future__ import annotations

import logging

from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ...common.confirmation import confirm_destructive_action
from ...common.timezone import resolve_user_timezone
from ..client import gmail_service
from ..presentation import clean_message_content, envelope
from ..schemas import GetThreadRequest, ListThreadsRequest, ModifyThreadRequest, ThreadIdRequest

LOGGER = logging.getLogger(__name__)


def register(server: FastMCP) -> None:
    @server.tool(name="list_threads")
    async def list_threads(
        query: Annotated[
            str | None,
            (
                "Gmail search query in the same syntax as the Gmail search box, e.g. "
                "'from:ana@example.com is:unread'; omit to list all threads outside Spam and Trash."
            ),
        ] = None,
        label_ids: Annotated[
            list[str] | None,
            (
                "Only threads that carry every one of these label IDs: system labels such as INBOX or "
                "UNREAD, or user label IDs (Label_...) from gmail_list_labels. Label names are not accepted."
            ),
        ] = None,
        max_results: Annotated[
            int,
            "Threads per page, 1 to 500 (default 25); each thread is fetched in full one after another, so large pages are slow.",
        ] = 25,
        page_token: str | None = None,
        include_spam_trash: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List Gmail conversations matching an optional Gmail query and labels, a page at a time, each summarized by its newest message. Use gmail_get_thread to read one conversation, and gmail_search_emails when individual messages rather than threads are wanted. Returns threads (the newest message's id, thread_id, from, subject, date, snippet, unread and has_attachments, plus message_count), next_page_token and result_size_estimate."""
        request = ListThreadsRequest(
            query=query,
            label_ids=label_ids or [],
            max_results=max_results,
            page_token=page_token,
            include_spam_trash=include_spam_trash,
        )
        service = gmail_service()
        account_timezone = await resolve_user_timezone()
        LOGGER.debug("Listing Gmail threads.")
        result = await execute_google_request(
            service.users()
            .threads()
            .list(
                userId="me",
                q=request.query,
                labelIds=request.label_ids or None,
                maxResults=request.max_results,
                pageToken=request.page_token,
                includeSpamTrash=request.include_spam_trash,
            )
        )
        threads = result.get("threads", [])
        envelopes = []
        for item in threads:
            thread = await execute_google_request(
                service.users().threads().get(userId="me", id=item["id"], format="full")
            )
            messages = thread.get("messages", [])
            if messages:
                envelopes.append(
                    {
                        **envelope(messages[-1], account_timezone=account_timezone),
                        "message_count": len(messages),
                    }
                )
        if ctx is not None:
            await ctx.report_progress(len(threads), request.max_results, "Threads listed")
        return {
            "threads": envelopes,
            "next_page_token": result.get("nextPageToken"),
            "result_size_estimate": result.get("resultSizeEstimate", 0),
        }

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
        """Read one Gmail conversation by thread ID. The default format 'clean' gives the newest message with its cleaned body plus a one-line summary of each earlier message; 'full', 'metadata' and 'minimal' return Gmail's raw thread resource instead. Use gmail_read_emails to read particular messages in full or continue a truncated body, and gmail_list_threads or gmail_search_emails to find threads. Returns thread_id, count (messages in the thread) and either latest, prior_messages and account_timezone ('clean') or thread (the other formats, whose historyId is Gmail's)."""
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

    @server.tool(name="modify_thread")
    async def modify_thread(
        thread_id: str,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Change labels on every message of one Gmail conversation: archive it (remove INBOX), mark it all read (remove UNREAD) or unread, star it, or file it under a user label. Use gmail_apply_labels or gmail_move_email for one message and gmail_batch_modify for messages from different threads; at least one label list is required. Returns status 'ok' and the updated Gmail thread resource under thread."""
        request = ModifyThreadRequest(
            thread_id=thread_id,
            add_label_ids=add_label_ids or [],
            remove_label_ids=remove_label_ids or [],
        )
        service = gmail_service()
        if not request.add_label_ids and not request.remove_label_ids:
            raise ValueError("At least one of add_label_ids/remove_label_ids must be provided.")
        LOGGER.debug(f"Modifying thread {request.thread_id}.")
        result = await execute_google_request(
            service.users()
            .threads()
            .modify(
                userId="me",
                id=request.thread_id,
                body={
                    "addLabelIds": request.add_label_ids,
                    "removeLabelIds": request.remove_label_ids,
                },
            )
        )
        return {"status": "ok", "thread": result}

    @server.tool(name="trash_thread")
    async def trash_thread(
        thread_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Move every message of one Gmail conversation to Trash, where Gmail deletes them for good after 30 days. For one message use gmail_delete_email; gmail_delete_thread deletes a conversation permanently right away. Returns status 'ok' and the Gmail thread resource under thread. Reversible with gmail_untrash_thread while the messages are still in Trash."""
        request = ThreadIdRequest(thread_id=thread_id)
        service = gmail_service()
        LOGGER.debug(f"Moving thread {request.thread_id} to trash.")
        trashed = await execute_google_request(
            service.users().threads().trash(userId="me", id=request.thread_id)
        )
        return {"status": "ok", "thread": trashed}

    @server.tool(name="untrash_thread")
    async def untrash_thread(
        thread_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Restore every message of one Gmail conversation from Trash, undoing gmail_trash_thread. For a single message use gmail_untrash_email; permanently deleted mail cannot be restored. Returns status 'ok' and the Gmail thread resource under thread."""
        request = ThreadIdRequest(thread_id=thread_id)
        service = gmail_service()
        LOGGER.debug(f"Restoring thread {request.thread_id} from trash.")
        restored = await execute_google_request(
            service.users().threads().untrash(userId="me", id=request.thread_id)
        )
        return {"status": "ok", "thread": restored}

    @server.tool(name="delete_thread")
    async def delete_thread(
        thread_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Permanently delete every message of one Gmail conversation, skipping Trash. It always asks the user to confirm first; for a reversible removal use gmail_trash_thread, and for one message gmail_delete_email. Returns status 'ok' with the thread_id, or status 'cancelled' if the user declines. Cannot be undone."""
        request = ThreadIdRequest(thread_id=thread_id)
        if not await confirm_destructive_action(
            ctx,
            "delete_thread",
            f"Permanently delete thread {request.thread_id}? This cannot be undone.",
        ):
            return {"status": "cancelled"}
        service = gmail_service()
        LOGGER.debug(f"Permanently deleting thread {request.thread_id}.")
        await execute_google_request(service.users().threads().delete(userId="me", id=request.thread_id))
        return {"status": "ok", "thread_id": request.thread_id}
