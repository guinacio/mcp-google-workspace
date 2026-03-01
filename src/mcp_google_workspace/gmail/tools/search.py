"""Gmail listing and search tools."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from ..client import gmail_service
from ..schemas import ListEmailsRequest, SearchEmailRequest


def _build_search_query(request: SearchEmailRequest) -> str | None:
    terms: list[str] = []
    if request.query:
        terms.append(f"({request.query})")
    if request.from_email:
        terms.append(f"from:{request.from_email}")
    if request.to_email:
        terms.append(f"to:{request.to_email}")
    if request.subject_contains:
        escaped = request.subject_contains.replace('"', '\\"')
        terms.append(f'subject:"{escaped}"')
    if request.has_attachment:
        terms.append("has:attachment")
    if request.is_unread:
        terms.append("is:unread")
    if request.newer_than_days is not None:
        terms.append(f"newer_than:{request.newer_than_days}d")
    if not terms:
        return None
    return " ".join(terms)


def register(server: FastMCP) -> None:
    @server.tool(name="search_emails")
    async def search_emails(request: SearchEmailRequest, ctx: Context) -> dict[str, Any]:
        """Search Gmail messages using Gmail query syntax and optional label filters."""
        service = gmail_service()
        query = _build_search_query(request)
        await ctx.info("Running Gmail search query.")
        result = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=query,
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
            "effective_query": query,
        }

    @server.tool(name="list_emails")
    async def list_emails(request: ListEmailsRequest, ctx: Context) -> dict[str, Any]:
        """List messages from a label with optional unread-only filtering."""
        service = gmail_service()
        label_ids = [request.label_id] if request.label_id else []
        if request.unread_only and "UNREAD" not in label_ids:
            label_ids.append("UNREAD")
        await ctx.info(
            f"Listing emails from labels {label_ids or ['INBOX']} (max_results={request.max_results})."
        )
        result = (
            service.users()
            .messages()
            .list(
                userId="me",
                labelIds=label_ids or ["INBOX"],
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
