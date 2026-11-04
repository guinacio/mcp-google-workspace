"""Gmail message envelopes over a recorded-shape metadata payload."""

from __future__ import annotations

from mcp_google_workspace.gmail.presentation import envelope, mail_feed_envelope

from . import TIMEZONE, Case

CASES = [
    Case(
        name="gmail_search_emails",
        fixture="gmail_message_metadata.json",
        normalize=lambda message: {
            "messages": [envelope(message, account_timezone=TIMEZONE)],
            "result_size_estimate": 1,
            "next_page_token": None,
            "effective_query": "from:bruno@example.com",
            "account_timezone": TIMEZONE,
        },
        items_key="messages",
    ),
    Case(
        name="gmail_get_mail_digest",
        fixture="gmail_message_metadata.json",
        normalize=lambda message: {
            "messages": [mail_feed_envelope(message, account_timezone=TIMEZONE)],
            "count": 1,
            "window": "3d",
            "account_timezone": TIMEZONE,
        },
        items_key="messages",
    ),
]
