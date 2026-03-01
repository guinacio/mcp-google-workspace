"""Gmail history tools."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from ..client import gmail_service
from ..schemas import ListHistoryRequest


def register(server: FastMCP) -> None:
    @server.tool(name="list_history")
    async def list_history(request: ListHistoryRequest, ctx: Context) -> dict[str, Any]:
        """List mailbox history events starting at a given history ID."""
        service = gmail_service()
        await ctx.info(f"Listing Gmail history from {request.start_history_id}.")
        result = (
            service.users()
            .history()
            .list(
                userId="me",
                startHistoryId=request.start_history_id,
                historyTypes=request.history_types or None,
                labelId=request.label_id,
                maxResults=request.max_results,
                pageToken=request.page_token,
            )
            .execute()
        )
        history = result.get("history", [])
        await ctx.report_progress(len(history), request.max_results, "History page loaded")
        return {
            "history": history,
            "history_id": result.get("historyId"),
            "next_page_token": result.get("nextPageToken"),
        }
