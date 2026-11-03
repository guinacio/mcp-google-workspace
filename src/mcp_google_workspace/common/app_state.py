"""Revisioned, TTL-bounded application state for MCP App views.

MCP 2026-07-28 has no transport session: every request may arrive on a fresh
connection, possibly at another replica. Anything a UI view needs across calls
therefore lives in an explicit application store addressed by a server-issued
handle (see :mod:`mcp_google_workspace.apps.state`), never in connection or
process scope.

Every record carries an integer ``revision`` that starts at 1 and increases by
one on each successful write. Writers use :meth:`AppStateStore.compare_and_set`
with the revision they read, so two concurrent or stale writers can never
silently overwrite each other: exactly one wins and the other receives a
deterministic ``conflict`` outcome together with the current record.

Records expire after a sliding idle TTL. ``get`` with ``refresh_ttl_seconds``
and every successful write push the expiry forward.

Why not FastMCP's ``SessionProvider``/``Session``: in FastMCP 4.0.10 those
accessors read-modify-write one dict with no revision or compare-and-set
(``fastmcp/server/sessions.py``, ``Session.set``), never apply a TTL on write
(retention is left to the backing key-value store), mint ids with ``uuid4``
(122 random bits), key isolation on ``(client_id, issuer, subject)`` and
collapse every unauthenticated caller into one shared ``anon`` bucket, and
``SessionProvider`` publishes extra model-visible ``create_session`` /
``end_session`` tools. The py-key-value stores underneath offer ``get``/``put``
with TTL but no conditional write, so CAS cannot be layered on them either.

Backends:

* :class:`MemoryAppStateStore` — in-process, for stdio and tests.
* :class:`RedisAppStateStore` — shared by every replica; one Redis hash per
  record (``rev`` + ``data``) updated by Lua scripts so each operation is
  atomic. Record bodies are Fernet-encrypted with the deployment key ring when
  one is supplied.

:func:`default_app_state_store` picks the backend with the same rule as the
Tasks queue: ``MCP_REDIS_URL`` selects Redis except in the local stdio bundle.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any, Literal, Protocol

from cryptography.fernet import InvalidToken

from .crypto import FernetKeyring

LOGGER = logging.getLogger("mcp_google_workspace.app_state")

DEFAULT_REDIS_PREFIX = "mcp:appstate:v1"


@dataclass(frozen=True, slots=True)
class VersionedRecord:
    """One stored JSON object and the revision that produced it."""

    revision: int
    value: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CasResult:
    """Outcome of :meth:`AppStateStore.compare_and_set`.

    ``ok``: the write happened and ``record`` is the new record.
    ``conflict``: the stored revision differs; nothing was written and
    ``record`` is the current record.
    ``missing``: no live record exists (unknown or expired key).
    """

    status: Literal["ok", "conflict", "missing"]
    record: VersionedRecord | None = None


class AppStateStore(Protocol):
    """The single storage interface in front of MCP App state."""

    backend_name: str

    async def create(
        self, key: str, value: dict[str, Any], *, ttl_seconds: float
    ) -> VersionedRecord | None:
        """Insert ``value`` at revision 1; return ``None`` if the key exists."""
        ...

    async def get(
        self, key: str, *, refresh_ttl_seconds: float | None = None
    ) -> VersionedRecord | None:
        """Return the live record, optionally extending its idle expiry."""
        ...

    async def compare_and_set(
        self,
        key: str,
        expected_revision: int,
        value: dict[str, Any],
        *,
        ttl_seconds: float,
    ) -> CasResult:
        """Replace the record only if it is still at ``expected_revision``."""
        ...

    async def delete(self, key: str) -> bool:
        """Remove the record; return whether one existed."""
        ...


def _encode(value: dict[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


class MemoryAppStateStore:
    """Process-local store for stdio and tests.

    A plain ``threading.Lock`` guards every operation (no awaits happen while it
    is held), so the store is safe across event loops and worker threads.
    Values are kept as JSON text so callers can never mutate stored state
    through a shared reference.
    """

    backend_name = "memory"

    def __init__(
        self,
        *,
        max_entries: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._entries: dict[str, tuple[int, str, float]] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._clock = clock

    def _live_locked(self, key: str, now: float) -> tuple[int, str, float] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry[2] <= now:
            del self._entries[key]
            return None
        return entry

    def _prune_locked(self, now: float) -> None:
        for key in [key for key, entry in self._entries.items() if entry[2] <= now]:
            del self._entries[key]
        overflow = len(self._entries) - self._max_entries + 1
        if overflow > 0:
            # Evict the records closest to expiry (least recently used).
            for key, _ in sorted(self._entries.items(), key=lambda item: item[1][2])[:overflow]:
                del self._entries[key]

    async def create(
        self, key: str, value: dict[str, Any], *, ttl_seconds: float
    ) -> VersionedRecord | None:
        encoded = _encode(value)
        with self._lock:
            now = self._clock()
            if self._live_locked(key, now) is not None:
                return None
            self._prune_locked(now)
            self._entries[key] = (1, encoded, now + ttl_seconds)
        return VersionedRecord(1, json.loads(encoded))

    async def get(
        self, key: str, *, refresh_ttl_seconds: float | None = None
    ) -> VersionedRecord | None:
        with self._lock:
            now = self._clock()
            entry = self._live_locked(key, now)
            if entry is None:
                return None
            revision, encoded, expires_at = entry
            if refresh_ttl_seconds:
                self._entries[key] = (revision, encoded, now + refresh_ttl_seconds)
        return VersionedRecord(revision, json.loads(encoded))

    async def compare_and_set(
        self,
        key: str,
        expected_revision: int,
        value: dict[str, Any],
        *,
        ttl_seconds: float,
    ) -> CasResult:
        encoded = _encode(value)
        with self._lock:
            now = self._clock()
            entry = self._live_locked(key, now)
            if entry is None:
                return CasResult("missing")
            revision, current, _ = entry
            if revision != expected_revision:
                return CasResult("conflict", VersionedRecord(revision, json.loads(current)))
            self._entries[key] = (revision + 1, encoded, now + ttl_seconds)
        return CasResult("ok", VersionedRecord(revision + 1, json.loads(encoded)))

    async def delete(self, key: str) -> bool:
        with self._lock:
            return self._entries.pop(key, None) is not None


# Each script touches exactly one hash key. Redis runs a script atomically, so a
# revision check and the write that depends on it can never interleave with
# another replica's write.
_CREATE_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('HSET', KEYS[1], 'rev', '1', 'data', ARGV[1])
redis.call('PEXPIRE', KEYS[1], ARGV[2])
return 1
"""

_GET_SCRIPT = """
local rev = redis.call('HGET', KEYS[1], 'rev')
if not rev then return false end
local data = redis.call('HGET', KEYS[1], 'data')
if tonumber(ARGV[1]) > 0 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end
return {rev, data}
"""

_CAS_SCRIPT = """
local rev = redis.call('HGET', KEYS[1], 'rev')
if not rev then return {'missing'} end
if rev ~= ARGV[1] then
  return {'conflict', rev, redis.call('HGET', KEYS[1], 'data')}
end
local next_rev = tostring(tonumber(rev) + 1)
redis.call('HSET', KEYS[1], 'rev', next_rev, 'data', ARGV[2])
redis.call('PEXPIRE', KEYS[1], ARGV[3])
return {'ok', next_rev}
"""


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _milliseconds(seconds: float) -> str:
    return str(max(1, int(seconds * 1000)))


class RedisAppStateStore:
    """Replica-shared store on Redis (or any client with a compatible async ``eval``).

    ``client`` must expose ``redis.asyncio.Redis``-style ``eval`` and
    ``delete`` coroutines; tests pass an in-memory Redis implementation.
    """

    backend_name = "redis"

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = DEFAULT_REDIS_PREFIX,
        keyring: FernetKeyring | None = None,
    ) -> None:
        self._client = client
        self._prefix = prefix.rstrip(":")
        self._keyring = keyring

    @classmethod
    def from_url(cls, url: str, *, keyring: FernetKeyring | None = None) -> "RedisAppStateStore":
        import redis.asyncio as redis_asyncio

        return cls(redis_asyncio.Redis.from_url(url), keyring=keyring)

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    def _seal(self, value: dict[str, Any]) -> str:
        encoded = _encode(value)
        if self._keyring is None:
            return encoded
        return self._keyring.encrypt(encoded.encode("utf-8")).decode("ascii")

    def _open(self, raw: Any) -> dict[str, Any] | None:
        data = raw if isinstance(raw, bytes) else _text(raw).encode("utf-8")
        if self._keyring is not None:
            try:
                data = self._keyring.decrypt(data).plaintext
            except (InvalidToken, ValueError):
                # A record sealed by a retired key cannot be trusted or read;
                # callers treat it like an expired record.
                LOGGER.warning("Discarding app state that no configured key can decrypt.")
                return None
        try:
            loaded = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError):
            LOGGER.warning("Discarding malformed app state record.")
            return None
        return loaded if isinstance(loaded, dict) else None

    async def create(
        self, key: str, value: dict[str, Any], *, ttl_seconds: float
    ) -> VersionedRecord | None:
        created = await self._client.eval(
            _CREATE_SCRIPT, 1, self._key(key), self._seal(value), _milliseconds(ttl_seconds)
        )
        if int(created) != 1:
            return None
        return VersionedRecord(1, json.loads(_encode(value)))

    async def get(
        self, key: str, *, refresh_ttl_seconds: float | None = None
    ) -> VersionedRecord | None:
        refresh = _milliseconds(refresh_ttl_seconds) if refresh_ttl_seconds else "0"
        raw = await self._client.eval(_GET_SCRIPT, 1, self._key(key), refresh)
        if not raw:
            return None
        value = self._open(raw[1])
        if value is None:
            return None
        return VersionedRecord(int(_text(raw[0])), value)

    async def compare_and_set(
        self,
        key: str,
        expected_revision: int,
        value: dict[str, Any],
        *,
        ttl_seconds: float,
    ) -> CasResult:
        raw = await self._client.eval(
            _CAS_SCRIPT,
            1,
            self._key(key),
            str(expected_revision),
            self._seal(value),
            _milliseconds(ttl_seconds),
        )
        status = _text(raw[0])
        if status == "ok":
            return CasResult("ok", VersionedRecord(int(_text(raw[1])), json.loads(_encode(value))))
        if status == "conflict":
            current = self._open(raw[2])
            if current is None:
                return CasResult("missing")
            return CasResult("conflict", VersionedRecord(int(_text(raw[1])), current))
        return CasResult("missing")

    async def delete(self, key: str) -> bool:
        return bool(await self._client.delete(self._key(key)))


def app_state_backend_url(environ: dict[str, str] | None = None) -> str | None:
    """Return the Redis URL for shared app state, or ``None`` for memory.

    Mirrors the Tasks queue rule: ``MCP_REDIS_URL`` is shared state for the HTTP
    fleet, but the local stdio bundle (``MCP_RUNTIME_MODE=bundle``) never joins
    a remote deployment's Redis merely because the variable is present.
    """
    env = os.environ if environ is None else environ
    url = env.get("MCP_REDIS_URL", "").strip()
    if not url or env.get("MCP_RUNTIME_MODE", "").strip().lower() == "bundle":
        return None
    return url


def build_app_state_store() -> AppStateStore:
    """Construct the configured backend (see :func:`app_state_backend_url`)."""
    url = app_state_backend_url()
    if url is None:
        return MemoryAppStateStore()
    try:
        keyring: FernetKeyring | None = FernetKeyring.from_environment()
    except ValueError as exc:
        raise ValueError(
            "Shared app state in Redis requires the token encryption key ring "
            "(MCP_SECRET_FILE, MCP_TOKEN_ENCRYPTION_KEYS or MCP_TOKEN_ENCRYPTION_KEY)."
        ) from exc
    return RedisAppStateStore.from_url(url, keyring=keyring)


_DEFAULT_STORE: AppStateStore | None = None
_DEFAULT_LOCK = threading.Lock()


def default_app_state_store() -> AppStateStore:
    """Process-wide store, built from the environment on first use."""
    global _DEFAULT_STORE
    with _DEFAULT_LOCK:
        if _DEFAULT_STORE is None:
            _DEFAULT_STORE = build_app_state_store()
        return _DEFAULT_STORE


def reset_default_app_state_store(store: AppStateStore | None = None) -> None:
    """Replace (or clear) the process-wide store; used by tests and fixtures."""
    global _DEFAULT_STORE
    with _DEFAULT_LOCK:
        _DEFAULT_STORE = store
