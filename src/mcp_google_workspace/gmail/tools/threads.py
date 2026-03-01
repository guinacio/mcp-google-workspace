"""Gmail thread tools."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from ..client import gmail_service
from ..schemas import GetThreadRequest, ListThreadsRequest, ModifyThreadRequest, ThreadIdRequest


def register(server: FastMCP) -> None:
    @server.tool(name="list_threads")
    async def list_threads(request: ListThreadsRequest, ctx: Context) -> dict[str, Any]:
        """List conversation threads with optional query/label filters."""
        service = gmail_service()
        await ctx.info("Listing Gmail threads.")
        result = (
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
            .execute()
        )
        threads = result.get("threads", [])
        await ctx.report_progress(len(threads), request.max_results, "Threads listed")
        return {
            "threads": threads,
            "next_page_token": result.get("nextPageToken"),
            "result_size_estimate": result.get("resultSizeEstimate", 0),
        }

    @server.tool(name="get_thread")
    async def get_thread(request: GetThreadRequest, ctx: Context) -> dict[str, Any]:
        """Fetch one thread with message set and history metadata."""
        service = gmail_service()
        await ctx.info(f"Reading thread {request.thread_id}.")
        thread = (
            service.users()
            .threads()
            .get(
                userId="me",
                id=request.thread_id,
                format=request.format,
                metadataHeaders=request.metadata_headers or None,
            )
            .execute()
        )
        messages = thread.get("messages", [])
        return {
            "thread_id": thread.get("id"),
            "history_id": thread.get("historyId"),
            "message_count": len(messages),
            "thread": thread,
        }

    @server.tool(name="modify_thread")
    async def modify_thread(request: ModifyThreadRequest, ctx: Context) -> dict[str, Any]:
        """Apply label changes to all messages in a thread."""
        service = gmail_service()
        if not request.add_label_ids and not request.remove_label_ids:
            raise ValueError("At least one of add_label_ids/remove_label_ids must be provided.")
        await ctx.info(f"Modifying thread {request.thread_id}.")
        result = (
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
            .execute()
        )
        return {"status": "ok", "thread": result}

    @server.tool(name="trash_thread")
    async def trash_thread(request: ThreadIdRequest, ctx: Context) -> dict[str, Any]:
        """Move an entire thread to trash."""
        service = gmail_service()
        await ctx.info(f"Moving thread {request.thread_id} to trash.")
        trashed = service.users().threads().trash(userId="me", id=request.thread_id).execute()
        return {"status": "ok", "thread": trashed}

    @server.tool(name="untrash_thread")
    async def untrash_thread(request: ThreadIdRequest, ctx: Context) -> dict[str, Any]:
        """Restore a trashed thread back to mailbox flow."""
        service = gmail_service()
        await ctx.info(f"Restoring thread {request.thread_id} from trash.")
        restored = service.users().threads().untrash(userId="me", id=request.thread_id).execute()
        return {"status": "ok", "thread": restored}

    @server.tool(name="delete_thread")
    async def delete_thread(request: ThreadIdRequest, ctx: Context) -> dict[str, Any]:
        """Permanently delete a thread."""
        service = gmail_service()
        await ctx.info(f"Permanently deleting thread {request.thread_id}.")
        service.users().threads().delete(userId="me", id=request.thread_id).execute()
        return {"status": "ok", "thread_id": request.thread_id}
