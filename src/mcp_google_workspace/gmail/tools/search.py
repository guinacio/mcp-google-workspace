"""Gmail listing and search tools."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from ..client import gmail_service
from ..schemas import ListEmailsRequest, SearchEmailRequest


def register(server: FastMCP) -> None:
    @server.tool(name="search_emails")
    async def search_emails(request: SearchEmailRequest, ctx: Context) -> dict[str, Any]:
        """Search Gmail messages using Gmail query syntax and optional label filters."""
        service = gmail_service()
        await ctx.info("Running Gmail search query.")
        result = (
            service.users()
            .messages()
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
        messages = result.get("messages", [])
        await ctx.report_progress(len(messages), request.max_results, "Search results loaded")
        return {
            "messages": messages,
            "result_size_estimate": result.get("resultSizeEstimate", 0),
            "next_page_token": result.get("nextPageToken"),
        }

    @server.tool(name="list_emails")
    async def list_emails(request: ListEmailsRequest, ctx: Context) -> dict[str, Any]:
        """List messages from a specific label (defaults to INBOX)."""
        service = gmail_service()
        await ctx.info(f"Listing emails from label {request.label_id}.")
        result = (
            service.users()
            .messages()
            .list(
                userId="me",
                labelIds=[request.label_id],
                maxResults=request.max_results,
                pageToken=request.page_token,
            )
            .execute()
        )
        messages = result.get("messages", [])
        await ctx.report_progress(len(messages), request.max_results, "Messages listed")
        return {
            "messages": messages,
            "next_page_token": result.get("nextPageToken"),
            "result_size_estimate": result.get("resultSizeEstimate", 0),
        }
