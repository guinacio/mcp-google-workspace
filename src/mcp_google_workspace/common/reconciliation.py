"""Cheap, reliable checks that resolve an ``outcome_unknown`` operation (W4b).

A check may only ever *confirm* that the uncertain change exists. "Not found"
is never treated as proof that it did not happen (search indexing can lag), so
an unconfirmed operation stays ``outcome_unknown`` and is never re-executed.

Implemented:

* **Gmail send** (``gmail_sent`` hint). ``gmail_send_email`` and the reply
  tools set an RFC 822 ``Message-ID`` on every outgoing message and register it
  before calling ``users.messages.send``. The check searches the mailbox with
  ``in:sent rfc822msgid:<id>``; one match confirms the send and yields the
  tool's normal ``status: sent`` result with the found message/thread ids.

Calendar creation does not need a check here: with ``idempotency_key`` the
insert uses a deterministic event id, so the call is repeat safe and the tool's
own ``events.get`` by that id (and the 409 path) returns the existing event on
retry. Everything else reports ``outcome_unknown`` with verification guidance
(see ``operations.verification_steps``).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

LOGGER = logging.getLogger("mcp_google_workspace.reconciliation")


async def _gmail_sent(hint: Mapping[str, Any]) -> dict[str, Any] | None:
    from ..auth.google_auth import build_gmail_service
    from .async_ops import execute_google_request

    message_id = str(hint.get("rfc822_message_id") or "")
    if not message_id:
        return None
    found = await execute_google_request(
        build_gmail_service()
        .users()
        .messages()
        .list(userId="me", q=f"in:sent rfc822msgid:{message_id}", maxResults=1, includeSpamTrash=True)
    )
    messages = found.get("messages") if isinstance(found, dict) else None
    if not messages:
        return None
    first = messages[0] if isinstance(messages[0], dict) else {}
    return {
        "status": "sent",
        "message_id": first.get("id"),
        "thread_id": first.get("threadId"),
        "label_ids": [],
    }


_CHECKS = {"gmail_sent": _gmail_sent}


async def reconcile(record: Mapping[str, Any]) -> Any | None:
    """Return the confirmed tool result for an ``outcome_unknown`` record, if provable."""
    for hint in record.get("reconcile") or []:
        if not isinstance(hint, Mapping):
            continue
        check = _CHECKS.get(str(hint.get("kind")))
        if check is None:
            continue
        try:
            confirmed = await check(hint)
        except Exception:  # noqa: BLE001 - a failed check leaves the outcome unknown
            LOGGER.warning("reconciliation check failed kind=%s", hint.get("kind"))
            continue
        if confirmed is None:
            continue
        if record.get("kind") == "commit":
            return {"status": "committed", "tool": record.get("tool"), "result": confirmed}
        return confirmed
    return None


__all__ = ["reconcile"]
