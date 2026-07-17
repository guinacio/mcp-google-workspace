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
from ..presentation import envelope

LOGGER = logging.getLogger(__name__)


def register(server: FastMCP) -> None:
    @server.tool(name="check_mail_updates")
    async def check_mail_updates(
        since_history_id: Annotated[
            str | None,
            (
                "Gmail historyId to resume from, taken from a prior response's "
                "next_history_id/continue_from_history_id; omit for a cold start."
            ),
        ] = None,
        timestamp: Annotated[
            str | None,
            (
                "ISO-8601 datetime or epoch-seconds string used as a cold-start alternative to "
                "since_history_id, returning mail newer than this time when no history cursor is available."
            ),
        ] = None,
        max_results: int = 100,
        page_token: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Return a compact incremental mailbox heartbeat, with direct-mail highlights."""
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
                if ctx is not None:
                    await ctx.warning(
                        f"Skipped deleted Gmail message {message_id} while reading mailbox history."
                    )
                continue
            messages.append(envelope(message, account_timezone=account_timezone))
        counts: dict[str, int] = {}
        for item in messages:
            category = item["category"] or "uncategorized"
            counts[category] = counts.get(category, 0) + 1
        # Drafts can surface through history responses; keep them out of the
        # direct-mail highlights so an unsent draft is never reported as real mail.
        highlights = [
            item
            for item in messages
            if not item["is_newsletter"] and not item["is_automated"] and not item["is_draft"]
        ]
        return {
            "new_count": len(messages),
            "skipped_deleted_count": len(skipped_deleted_message_ids),
            "skipped_deleted_message_ids": skipped_deleted_message_ids,
            "counts_by_category": counts,
            "highlights": highlights,
            "next_history_id": next_history_id,
            "next_page_token": next_page_token,
            "continue_from_history_id": since_history_id if next_page_token and since_history_id else None,
            "continue_from_timestamp": timestamp if next_page_token and not since_history_id else None,
            "account_timezone": account_timezone,
        }
