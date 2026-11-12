"""Gmail normalizers over recorded-shape payloads: messages, drafts, labels, filters, settings."""

from __future__ import annotations

from mcp_google_workspace.gmail.presentation import (
    draft_detail,
    draft_summary,
    envelope,
    filter_envelope,
    forwarding_address_envelope,
    label_envelope,
    mail_feed_envelope,
    vacation_envelope,
)

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
    Case(
        name="gmail_list_drafts_one_full",
        fixture="gmail_get_draft.json",
        normalize=lambda draft: {
            "drafts": [draft_detail(draft, format="full", account_timezone=TIMEZONE)],
            "count": 1,
            "next_page_token": None,
            "result_size_estimate": 1,
        },
    ),
    Case(
        name="gmail_list_drafts_one_minimal",
        fixture="gmail_get_draft.json",
        # A 'minimal' read carries no payload: headers and attachments are null, not empty.
        normalize=lambda draft: {
            "drafts": [
                draft_detail(
                    {**draft, "message": {k: v for k, v in draft["message"].items() if k != "payload"}},
                    format="minimal",
                    account_timezone=TIMEZONE,
                )
            ],
            "count": 1,
            "next_page_token": None,
            "result_size_estimate": 1,
        },
    ),
    Case(
        name="gmail_save_draft",
        fixture="gmail_get_draft.json",
        normalize=lambda draft: {"status": "ok", "mode": "created", "draft": draft_summary(draft)},
    ),
    Case(
        name="gmail_list_labels",
        fixture="gmail_list_labels.json",
        normalize=lambda payload: {
            "labels": [label_envelope(item) for item in payload["labels"]],
            "count": len(payload["labels"]),
        },
        items_key="labels",
    ),
    Case(
        name="gmail_manage_filters",
        fixture="gmail_list_filters.json",
        normalize=lambda payload: {
            "action": "list",
            "filters": [filter_envelope(item) for item in payload["filter"]],
            "count": len(payload["filter"]),
        },
        items_key="filters",
    ),
    Case(
        name="gmail_manage_forwarding",
        fixture="gmail_list_forwarding_addresses.json",
        normalize=lambda payload: {
            "action": "list",
            "forwarding_addresses": [forwarding_address_envelope(item) for item in payload["forwardingAddresses"]],
            "count": len(payload["forwardingAddresses"]),
        },
        items_key="forwarding_addresses",
    ),
    Case(
        name="gmail_manage_vacation",
        fixture="gmail_get_vacation.json",
        normalize=lambda payload: {"action": "get", "vacation": vacation_envelope(payload)},
    ),
]
