"""Gmail listing and search tools."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ...common.timezone import resolve_user_timezone
from ..client import gmail_service
from ..helpers import gather_in_order
from ..presentation import (
    cleaned_message_body,
    detect_deadline,
    envelope,
    first_meaningful_sentence,
    header_map,
    requires_response,
)
from ..schemas import DigestRequest, SearchEmailRequest


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
    @server.tool(name="get_mail_digest")
    async def get_mail_digest(
        window: str = "3d",
        unread_only: bool = False,
        max_items: int = 25,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Summarize recent mail into direct-person and automated groups, deduplicated by thread."""
        request = DigestRequest(window=window, unread_only=unread_only, max_items=max_items)
        window_match = re.fullmatch(r"(\d+)([dhw])", request.window)
        if window_match is None:  # pragma: no cover - DigestRequest validates this shape
            raise ValueError("window must be a positive number followed by d, h, or w")
        amount, unit = window_match.groups()
        seconds = int(amount) * {"d": 86_400, "h": 3_600, "w": 604_800}[unit]
        after = int((datetime.now(UTC) - timedelta(seconds=seconds)).timestamp())
        # Exclude drafts at the source so an unsent draft never reaches the digest.
        query = f"after:{after} -in:draft" + (" is:unread" if request.unread_only else "")
        service = gmail_service()
        account_timezone = await resolve_user_timezone()
        result = await execute_google_request(
            service.users().messages().list(userId="me", q=query, maxResults=request.max_items)
        )
        messages = await gather_in_order(
            [ref["id"] for ref in result.get("messages", [])],
            lambda message_id: execute_google_request(
                service.users().messages().get(userId="me", id=message_id, format="full")
            ),
        )
        seen_threads: set[str] = set()
        people: list[dict[str, Any]] = []
        automated: list[dict[str, Any]] = []
        for message in messages:
            item = envelope(message, account_timezone=account_timezone)
            # Defense in depth: even if a draft slips past the query filter, it
            # must not be reported alongside genuinely received mail.
            if item["is_draft"]:
                continue
            thread_id = str(item.get("thread_id") or item.get("id"))
            if thread_id in seen_threads:
                continue
            seen_threads.add(thread_id)
            text, _ = cleaned_message_body(message)
            gist = first_meaningful_sentence(text)
            if gist and gist != item["snippet"]:
                item["gist"] = gist
            item["requires_response"] = requires_response(
                text,
                is_automated=item["is_automated"],
                is_newsletter=item["is_newsletter"],
            )
            item["deadline_detected"] = detect_deadline(
                text,
                date_header=header_map(message.get("payload", {})).get("date"),
                account_timezone=account_timezone,
            )
            (automated if item["is_automated"] or item["is_newsletter"] else people).append(item)
        return {
            "window": request.window,
            "account_timezone": account_timezone,
            "people": people,
            "automated": automated,
            "next_history_id": None,
        }

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
        account_timezone = await resolve_user_timezone()
        query_str = _build_search_query(request)
        if ctx is not None:
            await ctx.info("Running Gmail search query.")
        result = await execute_google_request(
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
        )
        messages = result.get("messages", [])
        envelopes = [
            envelope(
                await execute_google_request(
                    service.users().messages().get(
                        userId="me",
                        id=item["id"],
                        format="full",
                        fields=(
                            "id,threadId,labelIds,snippet,internalDate,"
                            "payload(mimeType,filename,headers,"
                            "body(attachmentId,size),"
                            "parts(mimeType,filename,headers,body(attachmentId,size),"
                            "parts(mimeType,filename,body(attachmentId,size))))"
                        ),
                    )
                ),
                account_timezone=account_timezone,
            )
            for item in messages
        ]
        if ctx is not None:
            await ctx.report_progress(len(messages), request.max_results, "Search results loaded")
        return {
            "messages": envelopes,
            "result_size_estimate": result.get("resultSizeEstimate", 0),
            "next_page_token": result.get("nextPageToken"),
            "effective_query": query_str,
            "account_timezone": account_timezone,
        }
