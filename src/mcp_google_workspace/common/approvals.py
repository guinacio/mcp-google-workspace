"""Prepare/commit: consent for clients that cannot be asked directly.

``prepare_workspace_action`` runs the named tool with the given arguments up
to its consent gate (``common/consent.py``): the call is validated by the
same input schema, authorization and middleware as a direct call, classified
by the same table, and its preview rendered by the same renderer, but the
tool body never runs. When the call is consequential, prepare opens a
``prepared`` operation record (``common/operations.py``) binding the tool,
the arguments and the digest of the validated arguments to a one-time,
principal-scoped commit token, and returns the preview text.

``commit_workspace_action`` claims the record (``executing``) and runs the
bound call once with a commit grant for exactly that digest. The grant is the
consent when the client cannot be asked (or ``MCP_CONSENT=preview_only``);
a client that can ask is still asked, with the same preview. The confirmation
guard settles the record: ``awaiting_input`` when the bound tool asked a
question, back to ``prepared`` when nothing non-repeatable reached Google, or
``succeeded`` / ``failed`` / ``outcome_unknown``. A repeated commit of a
finished operation returns its saved result instead of executing again.
Tokens are never stored raw (records are keyed by a SHA-256 of the token).
"""

from __future__ import annotations

import secrets
from typing import Any, Final

from ..auth.identity import current_principal

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
        "rate_limited",
        "server_draining",
        "principal_revoked",
        "authorization_backend_unavailable",
        "reauth_required",
        "missing_capability",
    }
)


def commit_token_ttl_seconds() -> int:
    """Commit tokens follow ``MCP_CONFIRMATION_TTL_SECONDS`` (default 600 s)."""
    from .operations import pending_ttl_seconds

    return pending_ttl_seconds()


async def prepare_action(tool: str, arguments: dict[str, Any], *, server: Any = None) -> Any:
    """Run ``tool`` up to its consent gate and open a commit token for it.

    Returns the prepared payload, or the nested call's error result when the
    call is invalid, unauthorized or needs no consent.
    """
    from fastmcp.tools import InputRequiredToolResult

    from .consent import consent_capable_tools, preparing

    if tool not in consent_capable_tools():
        raise ValueError(f"{tool} never needs consent; call it directly.")
    if server is None:
        from ..server import workspace_mcp as server
    with preparing(tool) as request:
        result = await server.call_tool(tool, dict(arguments))
    if isinstance(result, InputRequiredToolResult) or result.is_error:
        # Validation, authorization or "no consent needed": the nested error says why.
        return result
    if not request.used or request.preview is None or request.digest is None or request.action is None:
        raise RuntimeError(f"{tool} did not reach its consent gate; nothing was prepared.")
    return await open_prepared_action(
        tool, dict(arguments), action=request.action, preview=request.preview, digest=request.digest
    )


async def open_prepared_action(
    tool: str, arguments: dict[str, Any], *, action: str, preview: str, digest: str
) -> dict[str, Any]:
    """Open a ``prepared`` operation record and return its commit token."""
    from .operations import get_operation_store, operation_key

    token = "cmt_" + secrets.token_urlsafe(32)
    key = operation_key(current_principal().storage_key, token)
    expires_at = await get_operation_store().open_commit(key, tool, arguments, digest)
    return {
        "status": "prepared",
        "commit_token": token,
        "expires_at": int(expires_at),
        "action": action,
        "preview": preview,
        "next_action": {
            "tool": "commit_workspace_action",
            "arguments": {"commit_token": token},
        },
    }
