"""Gmail listing and search tools."""

from __future__ import annotations

import logging

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ...common.timezone import resolve_user_timezone
from ..client import gmail_service
from ..helpers import gather_in_order
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


def register(server: FastMCP) -> None:
    @server.tool(name="get_mail_digest")
    async def get_mail_digest(
        window: Annotated[
            str,
            (
                "Lookback window as '<int><unit>' where unit is d (days), h (hours), or w (weeks), "
                "e.g. '24h' or '2w'; default is '3d'. Mail received or sent since now minus the window is listed."
            ),
        ] = "3d",
        unread_only: Annotated[
            bool, "When true, only unread messages are listed (adds is:unread); by default read and unread both are."
        ] = False,
        max_items: Annotated[
            int,
            (
                "Maximum messages to return, 1 to 100 (default 25). There is no next page: when the window "
                "holds more, shorten it or page through gmail_search_emails instead."
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
        label_ids: Annotated[
            list[str],
            (
                "Only messages that carry every one of these label IDs: system labels such as INBOX or "
                "UNREAD, or user label IDs (Label_...) from gmail_list_labels. Label names are not accepted."
            ),
        ] = [],
        max_results: Annotated[
            int, "Messages per page, 1 to 500 (default 25); each hit is fetched separately, so large pages are slower."
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
            int | None, "Only mail from the last N days, 1 to 3650, translated to Gmail's newer_than:{N}d query operator."
        ] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Find Gmail messages with a Gmail search query and optional helper filters (sender, recipient, subject, unread, attachments, age), ANDed together, a page at a time; Spam and Trash are left out unless include_spam_trash is true. Use gmail_get_mail_digest for a plain recent feed, gmail_check_mail_updates to poll for new mail since a saved checkpoint, and gmail_list_threads to get conversations. Returns messages (id, thread_id, from, subject, date, snippet, unread, has_attachments, is_draft, is_sent), next_page_token, result_size_estimate and effective_query, the exact query sent to Gmail. Read bodies with gmail_read_emails."""
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
        LOGGER.debug("Running Gmail search query.")
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
        # One bounded-concurrency fetch per hit (the same helper the digest
        # uses) instead of a sequential round trip per message.
        details = await gather_in_order(
            [item["id"] for item in messages],
            lambda message_id: execute_google_request(
                service.users().messages().get(
                    userId="me",
                    id=message_id,
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
        )
        envelopes = [envelope(message, account_timezone=account_timezone) for message in details]
        if ctx is not None:
            await ctx.report_progress(len(messages), request.max_results, "Search results loaded")
        return {
            "messages": envelopes,
            "result_size_estimate": result.get("resultSizeEstimate", 0),
            "next_page_token": result.get("nextPageToken"),
            "effective_query": query_str,
            "account_timezone": account_timezone,
        }
