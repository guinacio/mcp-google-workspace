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
    async def search_emails(
        query: str | None = None,
        label_ids: list[str] = [],
        max_results: int = 25,
        page_token: str | None = None,
        include_spam_trash: bool = False,
        from_email: str | None = None,
        to_email: str | None = None,
        subject_contains: str | None = None,
        has_attachment: bool = False,
        is_unread: bool = False,
        newer_than_days: int | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Search Gmail messages using Gmail query syntax and optional label filters."""
        request = SearchEmailRequest(
            query=query,
            label_ids=label_ids,
            max_results=max_results,
            page_token=page_token,
            include_spam_trash=include_spam_trash,
            from_email=from_email,
            to_email=to_email,
            subject_contains=subject_contains,
            has_attachment=has_attachment,
            is_unread=is_unread,
            newer_than_days=newer_than_days,
        )
        service = gmail_service()
        query_str = _build_search_query(request)
        if ctx is not None:
            await ctx.info("Running Gmail search query.")
        result = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=query_str,
                labelIds=request.label_ids or None,
                maxResults=request.max_results,
                pageToken=request.page_token,
                includeSpamTrash=request.include_spam_trash,
            )
            .execute()
        )
        messages = result.get("messages", [])
        if ctx is not None:
            await ctx.report_progress(len(messages), request.max_results, "Search results loaded")
        return {
            "messages": messages,
            "result_size_estimate": result.get("resultSizeEstimate", 0),
            "next_page_token": result.get("nextPageToken"),
            "effective_query": query_str,
        }

    @server.tool(name="list_emails")
    async def list_emails(
        label_id: str = "INBOX",
        max_results: int = 5,
        unread_only: bool = False,
        page_token: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List messages from a label with optional unread-only filtering."""
        request = ListEmailsRequest(
            label_id=label_id,
            max_results=max_results,
            unread_only=unread_only,
            page_token=page_token,
        )
        service = gmail_service()
        label_ids = [request.label_id] if request.label_id else []
        if request.unread_only and "UNREAD" not in label_ids:
            label_ids.append("UNREAD")
        if ctx is not None:
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
        if ctx is not None:
            await ctx.report_progress(len(messages), request.max_results, "Messages listed")
        return {
            "messages": messages,
            "next_page_token": result.get("nextPageToken"),
            "result_size_estimate": result.get("resultSizeEstimate", 0),
        }
