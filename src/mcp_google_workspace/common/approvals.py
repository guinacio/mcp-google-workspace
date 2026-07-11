"""Durable one-time prepare/commit records for consequential actions."""

from __future__ import annotations

from contextvars import ContextVar
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import time
from typing import Any

import redis

from ..auth.identity import current_principal
from ..runtime import get_token_storage_settings

COMMIT_ACTIVE: ContextVar[bool] = ContextVar("mcp_commit_active", default=False)

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
            "token TEXT PRIMARY KEY, scope TEXT NOT NULL, payload BLOB NOT NULL, expires_at INTEGER NOT NULL)"
        )
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

    def consume(self, token: str) -> tuple[str, dict[str, Any]]:
        scope = current_principal().storage_key
        now = int(time.time())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload,expires_at FROM approvals WHERE token=? AND scope=?",
                (token, scope),
            ).fetchone()
            connection.execute("DELETE FROM approvals WHERE token=? AND scope=?", (token, scope))
        if row is None or int(row[1]) < now:
            raise ValueError("Commit token is invalid, expired, already used, or belongs to another principal.")
        payload = json.loads(get_token_storage_settings().keyring.decrypt(row[0]).plaintext)
        if not isinstance(payload, dict) or not isinstance(payload.get("arguments"), dict):
            raise ValueError("Commit token payload is invalid.")
        return str(payload["tool"]), payload["arguments"]


class RedisApprovalStore:
    """One-time principal-bound commit tokens shared across replicas."""

    _CONSUME = """
    local value = redis.call('GET', KEYS[1])
    if value then redis.call('DEL', KEYS[1]) end
    return value
    """

    def __init__(self, url: str, *, ttl_seconds: int = 300) -> None:
        self.client = redis.Redis.from_url(url)
        self.ttl_seconds = ttl_seconds

    def prepare(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool not in CONSEQUENTIAL_TOOLS:
            raise ValueError("This tool does not use the consequential-action prepare protocol.")
        scope = current_principal().storage_key
        token = "cmt_" + secrets.token_urlsafe(32)
        expires_at = int(time.time()) + self.ttl_seconds
        payload = get_token_storage_settings().keyring.encrypt(
            json.dumps({"tool": tool, "arguments": arguments}, separators=(",", ":")).encode()
        )
        self.client.set(f"mcp:approval:{scope}:{token}", payload, ex=self.ttl_seconds, nx=True)
        return {
            "status": "prepared",
            "commit_token": token,
            "expires_at": expires_at,
            "impact": impact_preview(tool, arguments),
            "next_action": {"tool": "commit_workspace_action", "arguments": {"commit_token": token}},
        }

    def consume(self, token: str) -> tuple[str, dict[str, Any]]:
        scope = current_principal().storage_key
        value = self.client.eval(self._CONSUME, 1, f"mcp:approval:{scope}:{token}")
        if value is None:
            raise ValueError("Commit token is invalid, expired, already used, or belongs to another principal.")
        payload = json.loads(get_token_storage_settings().keyring.decrypt(value).plaintext)
        if not isinstance(payload, dict) or not isinstance(payload.get("arguments"), dict):
            raise ValueError("Commit token payload is invalid.")
        return str(payload["tool"]), payload["arguments"]


_REDIS_URL = os.getenv("MCP_REDIS_URL", "").strip()
APPROVAL_STORE: ApprovalStore | RedisApprovalStore = (
    RedisApprovalStore(_REDIS_URL) if _REDIS_URL else ApprovalStore()
)
