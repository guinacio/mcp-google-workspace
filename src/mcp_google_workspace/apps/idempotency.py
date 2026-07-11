"""Durable, atomic idempotency claims for Apps mutations."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Protocol

import redis

from .schemas import AppActionResult


def _metric(event: str, backend: str) -> None:
    from ..common.production import IDEMPOTENCY_EVENTS

    IDEMPOTENCY_EVENTS.labels(event, backend).inc()


@dataclass(frozen=True, slots=True)
class Claim:
    owner: bool
    cached: AppActionResult | None = None


class IdempotencyStore(Protocol):
    def claim(self, scope: str, key: str, request_json: str) -> Claim: ...
    def complete(self, scope: str, key: str, result: AppActionResult) -> None: ...
    def abandon(self, scope: str, key: str) -> None: ...


def _database_path() -> Path:
    configured = os.getenv("MCP_IDEMPOTENCY_DB", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    token_dir = os.getenv("MCP_USER_TOKEN_DIR", "").strip()
    if token_dir:
        return Path(token_dir).expanduser().resolve() / "apps-idempotency.sqlite3"
    return Path(tempfile.gettempdir()) / "mcp-google-workspace-idempotency.sqlite3"


class DurableIdempotencyStore:
    def __init__(self, path: Path | None = None, *, ttl_seconds: int = 86_400) -> None:
        self.path = path or _database_path()
        self.ttl_seconds = ttl_seconds

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency (
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT,
                expires_at INTEGER NOT NULL,
                PRIMARY KEY (scope, key)
            )
            """
        )
        return connection

    @staticmethod
    def request_hash(request_json: str) -> str:
        return sha256(request_json.encode("utf-8")).hexdigest()

    def claim(self, scope: str, key: str, request_json: str) -> Claim:
        request_hash = self.request_hash(request_json)
        now = int(time.time())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM idempotency WHERE expires_at < ?", (now,))
            row = connection.execute(
                "SELECT request_hash, status, result_json FROM idempotency WHERE scope=? AND key=?",
                (scope, key),
            ).fetchone()
            if row is not None:
                stored_hash, status, result_json = row
                if stored_hash != request_hash:
                    _metric("argument_conflict", "sqlite")
                    raise ValueError(
                        "idempotency_key was already used with different arguments."
                    )
                if status == "completed" and isinstance(result_json, str):
                    _metric("cache_hit", "sqlite")
                    return Claim(
                        owner=False,
                        cached=AppActionResult.model_validate_json(result_json),
                    )
                _metric("in_progress_collision", "sqlite")
                raise RuntimeError(
                    "An identical idempotent operation is already in progress; retry shortly."
                )
            connection.execute(
                "INSERT INTO idempotency(scope,key,request_hash,status,expires_at) VALUES(?,?,?,?,?)",
                (scope, key, request_hash, "in_progress", now + min(self.ttl_seconds, 300)),
            )
            _metric("claimed", "sqlite")
            return Claim(owner=True)

    def complete(self, scope: str, key: str, result: AppActionResult) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE idempotency SET status='completed', result_json=?, expires_at=? WHERE scope=? AND key=?",
                (result.model_dump_json(), int(time.time()) + self.ttl_seconds, scope, key),
            )

    def abandon(self, scope: str, key: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM idempotency WHERE scope=? AND key=? AND status='in_progress'",
                (scope, key),
            )


class RedisIdempotencyStore:
    """Cross-replica atomic idempotency using Redis TTL records."""

    _CLAIM = """
    local existing = redis.call('GET', KEYS[1])
    if existing then return existing end
    redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2], 'NX')
    return false
    """

    def __init__(self, url: str, *, ttl_seconds: int = 86_400) -> None:
        self.client = redis.Redis.from_url(url, decode_responses=True)
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _key(scope: str, key: str) -> str:
        return f"mcp:idem:{scope}:{key}"

    def claim(self, scope: str, key: str, request_json: str) -> Claim:
        request_hash = DurableIdempotencyStore.request_hash(request_json)
        pending = json.dumps({"hash": request_hash, "status": "in_progress"}, separators=(",", ":"))
        existing = self.client.eval(self._CLAIM, 1, self._key(scope, key), pending, 300)
        if existing is None:
            _metric("claimed", "redis")
            return Claim(owner=True)
        value = json.loads(str(existing))
        if value.get("hash") != request_hash:
            _metric("argument_conflict", "redis")
            raise ValueError("idempotency_key was already used with different arguments.")
        if value.get("status") == "completed":
            _metric("cache_hit", "redis")
            return Claim(owner=False, cached=AppActionResult.model_validate(value["result"]))
        _metric("in_progress_collision", "redis")
        raise RuntimeError("An identical idempotent operation is already in progress; retry shortly.")

    def complete(self, scope: str, key: str, result: AppActionResult) -> None:
        redis_key = self._key(scope, key)
        current = self.client.get(redis_key)
        if current is None:
            raise RuntimeError("Idempotency claim expired before completion.")
        value = json.loads(current)
        value.update({"status": "completed", "result": result.model_dump(mode="json")})
        self.client.set(redis_key, json.dumps(value, separators=(",", ":")), ex=self.ttl_seconds)

    def abandon(self, scope: str, key: str) -> None:
        self.client.delete(self._key(scope, key))


_REDIS_URL = os.getenv("MCP_REDIS_URL", "").strip()
STORE: IdempotencyStore = (
    RedisIdempotencyStore(_REDIS_URL) if _REDIS_URL else DurableIdempotencyStore()
)
