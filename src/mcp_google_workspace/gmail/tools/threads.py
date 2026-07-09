"""Gmail thread tools."""

from __future__ import annotations

from typing import Any, Literal

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request, require_elicitation_context
from ..client import gmail_service
from ..presentation import clean_message_content, envelope
from ..schemas import GetThreadRequest, ListThreadsRequest, ModifyThreadRequest, ThreadIdRequest


def register(server: FastMCP) -> None:
    @server.tool(name="list_threads")
    async def list_threads(
        query: str | None = None,
        label_ids: list[str] | None = None,
        max_results: int = 25,
        page_token: str | None = None,
        include_spam_trash: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List conversation threads with optional query/label filters."""
        request = ListThreadsRequest(
            query=query,
            label_ids=label_ids or [],
            max_results=max_results,
            page_token=page_token,
            include_spam_trash=include_spam_trash,
        )
        service = gmail_service()
        if ctx is not None:
            await ctx.info("Listing Gmail threads.")
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
                envelopes.append({**envelope(messages[-1]), "message_count": len(messages)})
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
        format: Literal["full", "metadata", "minimal", "clean"] = "clean",
        metadata_headers: list[str] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Fetch a thread cleanly: latest full message plus compact prior-message lines."""
        api_format: Literal["full", "metadata", "minimal"] = "full" if format == "clean" else format
        request = GetThreadRequest(
            thread_id=thread_id,
            format=api_format,
            metadata_headers=metadata_headers or [],
        )
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Reading thread {request.thread_id}.")
        thread = await execute_google_request(
            service.users()
            .threads()
            .get(
                userId="me",
                id=request.thread_id,
                format=request.format,
                metadataHeaders=request.metadata_headers or None,
            )
        )
        messages = thread.get("messages", [])
        if format == "clean":
            items = [envelope(item) for item in messages]
            latest = messages[-1] if messages else None
            return {
                "thread_id": thread.get("id"),
                "history_id": thread.get("historyId"),
                "message_count": len(messages),
                "latest": {**envelope(latest), **clean_message_content(latest)} if latest else None,
                "prior_messages": [
                    {"from": item["from"], "date": item["date"], "subject": item["subject"], "snippet": item["snippet"]}
                    for item in items[:-1]
                ],
            }
        return {
            "thread_id": thread.get("id"),
            "history_id": thread.get("historyId"),
            "message_count": len(messages),
            "thread": thread,
        }

    @server.tool(name="modify_thread")
    async def modify_thread(
        thread_id: str,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Apply label changes to all messages in a thread."""
        request = ModifyThreadRequest(
            thread_id=thread_id,
            add_label_ids=add_label_ids or [],
            remove_label_ids=remove_label_ids or [],
        )
        service = gmail_service()
        if not request.add_label_ids and not request.remove_label_ids:
            raise ValueError("At least one of add_label_ids/remove_label_ids must be provided.")
        if ctx is not None:
            await ctx.info(f"Modifying thread {request.thread_id}.")
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
        """Move an entire thread to trash."""
        request = ThreadIdRequest(thread_id=thread_id)
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Moving thread {request.thread_id} to trash.")
        trashed = await execute_google_request(
            service.users().threads().trash(userId="me", id=request.thread_id)
        )
        return {"status": "ok", "thread": trashed}

    @server.tool(name="untrash_thread")
    async def untrash_thread(
        thread_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Restore a trashed thread back to mailbox flow."""
        request = ThreadIdRequest(thread_id=thread_id)
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Restoring thread {request.thread_id} from trash.")
        restored = await execute_google_request(
            service.users().threads().untrash(userId="me", id=request.thread_id)
        )
        return {"status": "ok", "thread": restored}

    @server.tool(name="delete_thread")
    async def delete_thread(
        thread_id: str,
        force: bool = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Permanently delete a thread.

        ``force`` defaults to true so the tool works on MCP clients that don't
        implement elicitation; set to false to request an interactive
        confirmation prompt on supported clients.
        """
        request = ThreadIdRequest(thread_id=thread_id)
        if not force:
            confirm_ctx = require_elicitation_context(ctx, "delete_thread")
            response = await confirm_ctx.elicit(
                f"Permanently delete thread {request.thread_id}? This cannot be undone.",
                response_type=bool,  # type: ignore[arg-type]
            )
            if response.action != "accept" or not bool(response.data):
                return {"status": "cancelled"}
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Permanently deleting thread {request.thread_id}.")
        await execute_google_request(service.users().threads().delete(userId="me", id=request.thread_id))
        return {"status": "ok", "thread_id": request.thread_id}
