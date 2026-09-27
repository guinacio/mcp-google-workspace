"""Prepare/commit for consequential actions, backed by operation records.

``prepare_workspace_action`` opens a ``prepared`` operation record
(``common/operations.py``) that binds the exact tool and arguments to a
one-time, principal-scoped commit token. ``commit_workspace_action`` claims it
(``executing``), runs the bound call once, and the confirmation guard settles
the record: ``awaiting_input`` when the bound tool asked a confirmation
question, back to ``prepared`` when nothing non-repeatable reached Google, or
``succeeded`` / ``failed`` / ``outcome_unknown``. A repeated commit of a
finished operation returns its saved result instead of executing again (W4b).
Tokens are never consumed destructively and never stored raw (records are
keyed by a SHA-256 of the token).
"""

from __future__ import annotations

from contextvars import ContextVar
import os
import secrets
from typing import Any, Final

from ..auth.identity import current_principal

COMMIT_ACTIVE: ContextVar[bool] = ContextVar("mcp_commit_active", default=False)

INVALID_COMMIT = "Commit token is invalid, expired, or belongs to another principal."

# Structured error codes that this server raises strictly *before* a Google
# mutation is attempted: confirmation gates, admission control, revocation,
# credential/scope checks, and provider rejections that execute nothing (401
# reauth, 429). A commit whose nested call fails with one of these releases its
# operation (back to ``prepared``/``awaiting_input``) for a later retry. Other
# failures are settled from what the Google calls actually did (see
# ``operations.settle_after_failure``).
PRE_EXECUTION_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        "confirmation_required",
        "confirmation_invalid",
        "prepare_required",
        "rate_limited",
        "server_draining",
        "principal_revoked",
        "authorization_backend_unavailable",
        "reauth_required",
        "missing_capability",
    }
)


CONSEQUENTIAL_TOOLS = {
    "gmail_send_email",
    "drive_create_permission",
    "gmail_batch_modify",
    "calendar_update_event",
    "sheets_batch_update_spreadsheet",
}


def requires_prepare(tool: str, arguments: dict[str, Any]) -> bool:
    if tool == "gmail_send_email":
        recipients = sum(len(arguments.get(key) or []) for key in ("to", "cc", "bcc"))
        return recipients >= int(os.getenv("MCP_EMAIL_PREPARE_RECIPIENTS", "10"))
    if tool == "drive_create_permission":
        return arguments.get("permission_type") == "anyone" or arguments.get("type") == "anyone"
    if tool == "gmail_batch_modify":
        return len(arguments.get("message_ids") or []) >= 10
    if tool == "calendar_update_event":
        return arguments.get("recurrence") is not None
    if tool == "sheets_batch_update_spreadsheet":
        return len(arguments.get("requests") or []) >= 10
    return False


def impact_preview(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    preview: dict[str, Any] = {"tool": tool, "warnings": [], "counts": {}}
    if tool == "gmail_send_email":
        preview["counts"] = {
            key: len(arguments.get(key) or []) for key in ("to", "cc", "bcc", "attachments")
        }
        preview["subject"] = arguments.get("subject")
        preview["warnings"] = ["This sends an external email and cannot be recalled reliably."]
    elif tool == "drive_create_permission":
        preview["file_id"] = arguments.get("file_id")
        preview["permission_type"] = arguments.get("permission_type") or arguments.get("type")
        preview["warnings"] = ["This may expose a Drive resource outside the organization."]
    elif tool == "gmail_batch_modify":
        preview["counts"] = {"messages": len(arguments.get("message_ids") or [])}
    elif tool == "calendar_update_event":
        preview["event_id"] = arguments.get("event_id")
        preview["warnings"] = ["This update may affect a recurring event series."]
    elif tool == "sheets_batch_update_spreadsheet":
        preview["spreadsheet_id"] = arguments.get("spreadsheet_id")
        preview["counts"] = {"requests": len(arguments.get("requests") or [])}
    return preview


def commit_token_ttl_seconds() -> int:
    """Commit tokens follow ``MCP_CONFIRMATION_TTL_SECONDS`` (default 600 s)."""
    from .operations import pending_ttl_seconds

    return pending_ttl_seconds()


async def prepare_action(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Open a ``prepared`` operation record and return its commit token."""
    from .confirmation import arguments_digest
    from .operations import get_operation_store, operation_key

    if tool not in CONSEQUENTIAL_TOOLS:
        raise ValueError("This tool does not use the consequential-action prepare protocol.")
    token = "cmt_" + secrets.token_urlsafe(32)
    key = operation_key(current_principal().storage_key, token)
    expires_at = await get_operation_store().open_commit(key, tool, arguments, arguments_digest(arguments))
    return {
        "status": "prepared",
        "commit_token": token,
        "expires_at": int(expires_at),
        "impact": impact_preview(tool, arguments),
        "next_action": {
            "tool": "commit_workspace_action",
            "arguments": {"commit_token": token},
        },
    }
