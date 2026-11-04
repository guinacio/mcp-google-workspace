"""Gmail history tools."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from googleapiclient.errors import HttpError

from ...common.async_ops import execute_google_request
from ...common.timezone import resolve_user_timezone
from ..client import gmail_service
from ..helpers import gather_in_order
from ..presentation import mail_feed_envelope

LOGGER = logging.getLogger(__name__)


def register(server: FastMCP) -> None:
    @server.tool(name="check_mail_updates")
    async def check_mail_updates(
        since_history_id: Annotated[
            str | None,
            (
                "Gmail historyId to resume from: the next_history_id saved from the previous call, or the "
                "continue_from_history_id while paging; omit for a cold start. On paginated responses, "
                "next_history_id is null until the final page; do not overwrite a saved checkpoint with null. "
                "Continue with next_page_token as page_token and preserve the response's "
                "continue_from_history_id or continue_from_timestamp. Takes precedence over timestamp."
            ),
        ] = None,
        timestamp: Annotated[
            str | None,
            (
                "Cold-start alternative to since_history_id: an ISO-8601 datetime with offset, e.g. "
                "'2026-11-01T08:00:00Z', or epoch seconds; returns mail added after that time. Ignored when "
                "since_history_id is set; with neither, the last day of mail is returned."
            ),
        ] = None,
        max_results: Annotated[
            int,
            (
                "Page size (default 100): history records when resuming from since_history_id, messages on "
                "a cold start. Continue with next_page_token."
            ),
        ] = 100,
        page_token: Annotated[
            str | None,
            (
                "Pagination token from a prior response's next_page_token. Resubmit the "
                "matching continue_from_history_id as since_history_id, or "
                "continue_from_timestamp as timestamp, until the final page returns a "
                "non-null next_history_id."
            ),
        ] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Fetch the mail added to the mailbox since a saved checkpoint (since_history_id), or on a cold start since timestamp (the last day without one), received and sent alike with drafts left out, for incremental polling. Use gmail_get_mail_digest for a one-off look at a recent window and gmail_search_emails to search. Returns messages (the digest's fields), count, skipped_deleted_message_ids for mail deleted before it could be read, next_page_token, and next_history_id: the checkpoint to save for the next call, null until the last page. Read bodies with gmail_read_emails."""
        service = gmail_service()
        account_timezone = await resolve_user_timezone()
        if since_history_id:
            result = await execute_google_request(
                service.users().history().list(
                    userId="me",
                    startHistoryId=since_history_id,
                    historyTypes=["messageAdded"],
                    maxResults=max_results,
                    pageToken=page_token,
                )
            )
            ids = list(dict.fromkeys(
                item.get("message", {}).get("id")
                for event in result.get("history", [])
                for item in event.get("messagesAdded", [])
                if item.get("message", {}).get("id")
            ))
            next_page_token = result.get("nextPageToken")
            # Gmail's historyId is the mailbox's newest value, not this page's
            # final event. Advancing it before all pages are consumed drops events.
            next_history_id = result.get("historyId") if not next_page_token else None
        else:
            # Establish the cursor before querying so mail that arrives during
            # bootstrap is picked up by the next history-based heartbeat.
            profile = await execute_google_request(service.users().getProfile(userId="me"))
            if timestamp:
                try:
                    after: int | str = int(
                        datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
                    )
                except ValueError:
                    after = timestamp
                query = f"after:{after} -in:draft"
            else:
                query = "newer_than:1d -in:draft"
            listed = await execute_google_request(
                service.users().messages().list(
                    userId="me", q=query, maxResults=max_results, pageToken=page_token
                )
            )
            ids = [item["id"] for item in listed.get("messages", [])]
            next_page_token = listed.get("nextPageToken")
            next_history_id = profile.get("historyId") if not next_page_token else None
        # A deleted message surfaced by history returns 404 on fetch; capture the
        # sentinel per-slot so ordering is preserved under concurrent fetching.
        _MISSING = object()

        async def fetch(message_id: str) -> Any:
            try:
                return await execute_google_request(
                    service.users().messages().get(userId="me", id=message_id, format="full")
                )
            except HttpError as exc:
                if getattr(exc.resp, "status", None) != 404:
                    raise
                return _MISSING

        fetched = await gather_in_order(ids, fetch)
        messages: list[dict[str, Any]] = []
        skipped_deleted_message_ids: list[str] = []
        for message_id, message in zip(ids, fetched, strict=True):
            if message is _MISSING:
                skipped_deleted_message_ids.append(message_id)
                LOGGER.info(
                    "gmail_history_message_missing message_id=%s outcome=skipped",
                    message_id,
                )
                LOGGER.debug(
                    f"Skipped deleted Gmail message {message_id} while reading mailbox history."
                )
                continue
            item = mail_feed_envelope(message, account_timezone=account_timezone)
            if item is not None:
                messages.append(item)
        return {
            "count": len(messages),
            "skipped_deleted_count": len(skipped_deleted_message_ids),
            "skipped_deleted_message_ids": skipped_deleted_message_ids,
            "messages": messages,
            "next_history_id": next_history_id,
            "next_page_token": next_page_token,
            "continue_from_history_id": since_history_id if next_page_token and since_history_id else None,
            "continue_from_timestamp": timestamp if next_page_token and not since_history_id else None,
            "account_timezone": account_timezone,
        }
