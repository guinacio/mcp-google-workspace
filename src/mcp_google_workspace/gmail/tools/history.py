"""Gmail history tools."""

from __future__ import annotations

from typing import Any, Literal

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ..client import gmail_service
from ..schemas import ListHistoryRequest


def register(server: FastMCP) -> None:
    @server.tool(name="list_history")
    async def list_history(
        start_history_id: str,
        history_types: list[
            Literal["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"]
        ] | None = None,
        label_id: str | None = None,
        max_results: int = 100,
        page_token: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List mailbox history events starting at a given history ID."""
        request = ListHistoryRequest(
            start_history_id=start_history_id,
            history_types=history_types or [],
            label_id=label_id,
            max_results=max_results,
            page_token=page_token,
        )
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Listing Gmail history from {request.start_history_id}.")
        result = await execute_google_request(
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
        )
        history = result.get("history", [])
        if ctx is not None:
            await ctx.report_progress(len(history), request.max_results, "History page loaded")
        return {
            "history": history,
            "history_id": result.get("historyId"),
            "next_page_token": result.get("nextPageToken"),
        }
