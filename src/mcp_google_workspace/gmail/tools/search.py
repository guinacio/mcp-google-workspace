"""Gmail listing and search tools."""

from __future__ import annotations

import logging

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from pydantic import Field

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ...common.timezone import resolve_user_timezone
from ..client import gmail_service
from ..helpers import gather_in_order
from ..label_resolver import LabelResolver
from ..presentation import envelope, mail_feed_envelope
from ..schemas import DigestRequest, SearchEmailRequest

LOGGER = logging.getLogger(__name__)


def _quote_search_value(value: str) -> str:
    """Quote one Gmail search helper value without changing its meaning."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _build_search_query(request: SearchEmailRequest) -> str | None:
    terms: list[str] = []
    if request.query:
        terms.append(f"({request.query})")
    if request.from_email:
        terms.append(f"from:{_quote_search_value(request.from_email)}")
    if request.to_email:
        terms.append(f"to:{_quote_search_value(request.to_email)}")
    if request.subject_contains:
        terms.append(f"subject:{_quote_search_value(request.subject_contains)}")
    if request.has_attachment:
        terms.append("has:attachment")
    if request.is_unread:
        terms.append("is:unread")
    if request.newer_than_days is not None:
        terms.append(f"newer_than:{request.newer_than_days}d")
    if not terms:
        return None
    return " ".join(terms)


# What a search hit needs from each message: headers, labels and the attachment
# parts, without any body data.
_HIT_FIELDS = (
    "id,threadId,labelIds,snippet,internalDate,"
    "payload(mimeType,filename,headers,"
    "body(attachmentId,size),"
    "parts(mimeType,filename,headers,body(attachmentId,size),"
    "parts(mimeType,filename,body(attachmentId,size))))"
)


def register(server: FastMCP) -> None:
    @server.tool(name="get_mail_digest")
    async def get_mail_digest(
        window: Annotated[
            str,
            Field(
                pattern=r"^\d+[dhw]$",
                description=(
                    "Lookback window: a number of days (d), hours (h) or weeks (w), e.g. '24h' or '2w'. "
                    "Mail received or sent since now minus the window is listed."
                ),
            ),
        ] = "3d",
        unread_only: Annotated[
            bool, "When true, only unread messages are listed (adds is:unread); by default read and unread both are."
        ] = False,
        max_items: Annotated[
            int,
            Field(
                ge=1,
                le=100,
                description=(
                    "Messages to return. There is no next page: when the window holds more, shorten it or "
                    "page through gmail_search_emails instead."
                ),
            ),
        ] = 25,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List the mail received and sent in a recent time window (default the last 3 days) as one feed with snippets, with no category or importance filtering; drafts, Spam and Trash are left out. Use it to catch up on recent mail; use gmail_search_emails to find mail by sender, subject or any Gmail query, and gmail_check_mail_updates to poll for new mail since a saved checkpoint. Returns messages (id, thread_id, from, subject, date, snippet, unread, has_attachments, direction 'received' or 'sent'), count, window and account_timezone; there is no next page. Read bodies with gmail_read_emails."""
        request = DigestRequest(window=window, unread_only=unread_only, max_items=max_items)
        window_match = re.fullmatch(r"(\d+)([dhw])", request.window)
        if window_match is None:  # pragma: no cover - DigestRequest validates this shape
            raise ValueError("window must be a positive number followed by d, h, or w")
        amount, unit = window_match.groups()
        seconds = int(amount) * {"d": 86_400, "h": 3_600, "w": 604_800}[unit]
        after = int((datetime.now(UTC) - timedelta(seconds=seconds)).timestamp())
        # Do not filter by category, Inbox placement, sender type, direction, or
        # inferred importance: routed and sent mail must remain available.
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
        feed_messages: list[dict[str, Any]] = []
        for message in messages:
            item = mail_feed_envelope(message, account_timezone=account_timezone)
            if item is not None:
                feed_messages.append(item)
        return {
            "messages": feed_messages,
            "count": len(feed_messages),
            "window": request.window,
            "account_timezone": account_timezone,
        }

    @server.tool(name="search_emails")
    async def search_emails(
        query: Annotated[
            str | None,
            (
                "Gmail search query in the same syntax as the Gmail search box, e.g. "
                "'from:ana@example.com newer_than:7d has:attachment'. It is wrapped in parentheses and "
                "ANDed with the helper filters; with no query, labels or filters, all mail outside Spam and Trash matches."
            ),
        ] = None,
        labels: Annotated[
            list[str] | None,
            (
                "Only mail that carries every one of these labels, given by name as shown in Gmail (e.g. "
                "'Clients/Acme') or by ID; system labels such as INBOX or UNREAD match case-insensitively. "
                "An unknown or ambiguous name fails with not_found and the candidate labels."
            ),
        ] = None,
        max_results: Annotated[
            int,
            Field(
                ge=1,
                le=500,
                description=(
                    "Messages (or, with group_by_thread, conversations) per page; each hit is fetched "
                    "separately, so large pages are slower."
                ),
            ),
        ] = 25,
        page_token: str | None = None,
        include_spam_trash: bool = False,
        from_email: Annotated[
            str | None, "Sender filter, added to the query as from:\"<value>\"; an address or part of a name, e.g. 'ana@example.com'."
        ] = None,
        to_email: Annotated[
            str | None, "Recipient filter, added to the query as to:\"<value>\", e.g. 'team@example.com'."
        ] = None,
        subject_contains: Annotated[
            str | None, "Phrase that must appear in the subject line, added to the query as subject:\"<value>\"."
        ] = None,
        has_attachment: Annotated[
            bool, "When true, adds has:attachment so only messages with attachments match; false adds nothing."
        ] = False,
        is_unread: Annotated[
            bool, "When true, adds is:unread so only unread messages match; false adds nothing, so read mail matches too."
        ] = False,
        newer_than_days: Annotated[
            int | None,
            Field(ge=1, le=3650, description="Only mail from the last N days, added to the query as newer_than:{N}d."),
        ] = None,
        group_by_thread: Annotated[
            bool,
            (
                "Return conversations instead of messages: threads, each summarized by its newest "
                "message plus message_count."
            ),
        ] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Find Gmail messages, or with group_by_thread whole conversations, by a Gmail search query, labels (names or IDs) and helper filters (sender, recipient, subject, unread, attachments, age), ANDed together, a page at a time; Spam and Trash are left out unless include_spam_trash is true. Use gmail_get_mail_digest for a plain recent feed and gmail_check_mail_updates to poll for new mail since a saved checkpoint. Returns messages (id, thread_id, from, subject, date, snippet, unread, has_attachments, is_draft, is_sent) or threads (the same plus message_count), next_page_token, result_size_estimate and effective_query. Read bodies with gmail_read_emails or gmail_get_thread."""
        request = SearchEmailRequest(
            query=query,
            label_ids=list(labels or []),
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
        label_ids = await LabelResolver(service, execute_google_request).resolve_all(request.label_ids)
        account_timezone = await resolve_user_timezone()
        query_str = _build_search_query(request)
        LOGGER.debug("Running Gmail search query.")
        listing = service.users().threads() if group_by_thread else service.users().messages()
        result = await execute_google_request(
            listing.list(
                userId="me",
                q=query_str,
                labelIds=label_ids or None,
                maxResults=request.max_results,
                pageToken=request.page_token,
                includeSpamTrash=request.include_spam_trash,
            )
        )
        if group_by_thread:
            # One bounded-concurrency read per thread, without body data.
            threads = await gather_in_order(
                [item["id"] for item in result.get("threads", [])],
                lambda thread_id: execute_google_request(
                    service.users().threads().get(
                        userId="me", id=thread_id, format="full", fields=f"id,messages({_HIT_FIELDS})"
                    )
                ),
            )
            summaries = []
            for thread in threads:
                thread_messages = thread.get("messages", [])
                if thread_messages:
                    summaries.append(
                        {
                            **envelope(thread_messages[-1], account_timezone=account_timezone),
                            "message_count": len(thread_messages),
                        }
                    )
            if ctx is not None:
                await ctx.report_progress(len(summaries), request.max_results, "Threads listed")
            return {
                "threads": summaries,
                "count": len(summaries),
                "result_size_estimate": result.get("resultSizeEstimate", 0),
                "next_page_token": result.get("nextPageToken"),
                "effective_query": query_str,
                "account_timezone": account_timezone,
            }
        messages = result.get("messages", [])
        # One bounded-concurrency fetch per hit (the same helper the digest
        # uses) instead of a sequential round trip per message.
        details = await gather_in_order(
            [item["id"] for item in messages],
            lambda message_id: execute_google_request(
                service.users().messages().get(
                    userId="me", id=message_id, format="full", fields=_HIT_FIELDS
                )
            ),
        )
        envelopes = [envelope(message, account_timezone=account_timezone) for message in details]
        if ctx is not None:
            await ctx.report_progress(len(messages), request.max_results, "Search results loaded")
        return {
            "messages": envelopes,
            "count": len(envelopes),
            "result_size_estimate": result.get("resultSizeEstimate", 0),
            "next_page_token": result.get("nextPageToken"),
            "effective_query": query_str,
            "account_timezone": account_timezone,
        }
