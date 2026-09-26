"""Durable one-time prepare/commit records for consequential actions.

Commit lifecycle (W4a): a commit *claims* the prepared token, runs the bound
call, and then either *completes* it (consumed) or *releases* it. Release only
happens when the nested call provably did not execute the action: it asked a
multi-round-trip confirmation question, or it was rejected before execution
(see ``PRE_EXECUTION_ERROR_CODES``). Any other outcome consumes the token, so a
lost response can never be retried into a duplicate Google mutation. W4b
replaces this with durable operation records
(``prepared -> awaiting_input -> executing -> succeeded | failed | outcome_unknown``)
behind the same claim/release/complete seam.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import time
from typing import Any, Final

import redis

from ..auth.identity import current_principal
from ..runtime import get_token_storage_settings

COMMIT_ACTIVE: ContextVar[bool] = ContextVar("mcp_commit_active", default=False)

_INVALID_COMMIT = "Commit token is invalid, expired, already used, or belongs to another principal."
_COMMIT_IN_PROGRESS = "Commit token is already being committed by another request; retry after it finishes."

# Structured error codes that this server raises strictly *before* a Google
# mutation is attempted: confirmation gates, admission control, revocation,
# credential/scope checks, and provider rejections that execute nothing (401
# reauth, 429). A commit whose nested call fails with one of these releases its
# token for a later retry. Every other failure (timeouts, 5xx, unexpected
# errors, invalid_input raised mid-body) leaves the outcome uncertain and
# consumes the token.
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


@dataclass(frozen=True, slots=True)
class ClaimedApproval:
    """A prepared action exclusively claimed by one commit attempt."""

    token: str
    tool: str
    arguments: dict[str, Any]


def _decode_payload(token: str, encrypted: bytes) -> ClaimedApproval:
    payload = json.loads(get_token_storage_settings().keyring.decrypt(encrypted).plaintext)
    if not isinstance(payload, dict) or not isinstance(payload.get("arguments"), dict):
        raise ValueError("Commit token payload is invalid.")
    return ClaimedApproval(token=token, tool=str(payload["tool"]), arguments=payload["arguments"])

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


class ApprovalStore:
    def __init__(self, path: Path | None = None, *, ttl_seconds: int = 300) -> None:
        configured = os.getenv("MCP_APPROVAL_DB", "").strip()
        self.path = path or (
            Path(configured).expanduser().resolve()
            if configured
            else Path(tempfile.gettempdir()) / "mcp-google-workspace-approvals.sqlite3"
        )
        self.ttl_seconds = ttl_seconds

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS approvals ("
            "token TEXT PRIMARY KEY, scope TEXT NOT NULL, payload BLOB NOT NULL, expires_at INTEGER NOT NULL, "
            "claimed INTEGER NOT NULL DEFAULT 0)"
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(approvals)")}
        if "claimed" not in columns:
            connection.execute("ALTER TABLE approvals ADD COLUMN claimed INTEGER NOT NULL DEFAULT 0")
        return connection

    def prepare(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool not in CONSEQUENTIAL_TOOLS:
            raise ValueError("This tool does not use the consequential-action prepare protocol.")
        scope = current_principal().storage_key
        token = "cmt_" + secrets.token_urlsafe(32)
        expires_at = int(time.time()) + self.ttl_seconds
        payload = json.dumps({"tool": tool, "arguments": arguments}, separators=(",", ":")).encode()
        encrypted = get_token_storage_settings().keyring.encrypt(payload)
        with self._connect() as connection:
            connection.execute("DELETE FROM approvals WHERE expires_at < ?", (int(time.time()),))
            connection.execute(
                "INSERT INTO approvals(token,scope,payload,expires_at) VALUES(?,?,?,?)",
                (token, scope, encrypted, expires_at),
            )
        return {
            "status": "prepared",
            "commit_token": token,
            "expires_at": expires_at,
            "impact": impact_preview(tool, arguments),
            "next_action": {
                "tool": "commit_workspace_action",
                "arguments": {"commit_token": token},
            },
        }

    def claim(self, token: str) -> ClaimedApproval:
        """Exclusively claim a live token for one commit attempt."""
        scope = current_principal().storage_key
        now = int(time.time())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload,expires_at,claimed FROM approvals WHERE token=? AND scope=?",
                (token, scope),
            ).fetchone()
            if row is None or int(row[1]) < now:
                connection.execute("COMMIT")
                raise ValueError(_INVALID_COMMIT)
            if int(row[2]):
                connection.execute("COMMIT")
                raise ValueError(_COMMIT_IN_PROGRESS)
            connection.execute(
                "UPDATE approvals SET claimed=1 WHERE token=? AND scope=?", (token, scope)
            )
            connection.execute("COMMIT")
        return _decode_payload(token, row[0])

    def release(self, token: str) -> None:
        """Return a claimed token unused (the bound action did not execute)."""
        scope = current_principal().storage_key
        with self._connect() as connection:
            connection.execute(
                "UPDATE approvals SET claimed=0 WHERE token=? AND scope=?", (token, scope)
            )

    def complete(self, token: str) -> None:
        """Consume a claimed token: the bound action ran (or may have run)."""
        scope = current_principal().storage_key
        with self._connect() as connection:
            connection.execute("DELETE FROM approvals WHERE token=? AND scope=?", (token, scope))

    def consume(self, token: str) -> tuple[str, dict[str, Any]]:
        """Claim and immediately complete (single-shot use without a commit round)."""
        claimed = self.claim(token)
        self.complete(token)
        return claimed.tool, claimed.arguments


class RedisApprovalStore:
    """One-time principal-bound commit tokens shared across replicas."""

    def __init__(self, url: str, *, ttl_seconds: int = 300) -> None:
        self.client = redis.Redis.from_url(url)
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _key(scope: str, token: str) -> str:
        return f"mcp:approval:{scope}:{token}"

    def prepare(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool not in CONSEQUENTIAL_TOOLS:
            raise ValueError("This tool does not use the consequential-action prepare protocol.")
        scope = current_principal().storage_key
        token = "cmt_" + secrets.token_urlsafe(32)
        expires_at = int(time.time()) + self.ttl_seconds
        payload = get_token_storage_settings().keyring.encrypt(
            json.dumps({"tool": tool, "arguments": arguments}, separators=(",", ":")).encode()
        )
        self.client.set(self._key(scope, token), payload, ex=self.ttl_seconds, nx=True)
        return {
            "status": "prepared",
            "commit_token": token,
            "expires_at": expires_at,
            "impact": impact_preview(tool, arguments),
            "next_action": {"tool": "commit_workspace_action", "arguments": {"commit_token": token}},
        }

    def claim(self, token: str) -> ClaimedApproval:
        """Exclusively claim a live token: ``SET NX`` a claim marker for its remaining TTL."""
        key = self._key(current_principal().storage_key, token)
        remaining_ms = int(self.client.pttl(key))
        if remaining_ms <= 0:
            raise ValueError(_INVALID_COMMIT)
        if not self.client.set(key + ":claim", b"1", nx=True, px=remaining_ms):
            raise ValueError(_COMMIT_IN_PROGRESS)
        value = self.client.get(key)
        if not isinstance(value, bytes):
            self.client.delete(key + ":claim")
            raise ValueError(_INVALID_COMMIT)
        return _decode_payload(token, value)

    def release(self, token: str) -> None:
        """Return a claimed token unused (the bound action did not execute)."""
        self.client.delete(self._key(current_principal().storage_key, token) + ":claim")

    def complete(self, token: str) -> None:
        """Consume a claimed token: the bound action ran (or may have run)."""
        key = self._key(current_principal().storage_key, token)
        self.client.delete(key, key + ":claim")

    def consume(self, token: str) -> tuple[str, dict[str, Any]]:
        """Claim and immediately complete (single-shot use without a commit round)."""
        claimed = self.claim(token)
        self.complete(token)
        return claimed.tool, claimed.arguments


_REDIS_URL = os.getenv("MCP_REDIS_URL", "").strip()
APPROVAL_STORE: ApprovalStore | RedisApprovalStore = (
    RedisApprovalStore(_REDIS_URL) if _REDIS_URL else ApprovalStore()
)
