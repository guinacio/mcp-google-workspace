"""Server-issued dashboard view handles and revisioned view state.

Each dashboard view (one tool invocation that renders the UI) gets its own
unguessable handle, minted here and returned in the launch tool result. Every
later UI callback passes the handle back; the server resolves it against the
shared :mod:`~mcp_google_workspace.common.app_state` store and authorizes it
for the calling principal on every use.

Handle format: ``wsv_`` followed by 43 URL-safe base64 characters, i.e.
``secrets.token_urlsafe(32)`` = 256 random bits. The handle itself is never
stored: records are keyed by ``view:<principal storage key>:<sha256(handle)>``
and also record the owning principal, so

* a handle used by another principal addresses a different key and resolves to
  nothing (reported exactly like an unknown or expired handle, so a caller
  cannot probe which handles exist), and
* a leaked store dump does not reveal usable handles.

The principal is :func:`mcp_google_workspace.auth.identity.current_principal`:
the verified ``(issuer, subject)`` of the bearer token over HTTP, or the one
explicit trusted-local principal (``MCP_LOCAL_PRINCIPAL``, default
``local-user``) over stdio. Stdio is a single trusted-user boundary: handle
secrecy there separates views, not users.

State updates are compare-and-set on the record revision. A caller that sends
``expected_revision`` gets a deterministic :class:`ViewStateConflict` when the
view moved on; a caller that omits it has its change applied atomically on top
of the latest state (bounded retries), so no concurrent field update is lost.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
import hmac
import os
import re
import secrets
import time
from typing import Any, Final

from dateutil.relativedelta import relativedelta
from pydantic import ValidationError

from ..auth.identity import current_principal
from ..common.app_state import AppStateStore, default_app_state_store
from .schemas import DashboardState, DashboardStatePatch

VIEW_META_KEY: Final[str] = "mcp-google-workspace/view"
"""``_meta`` key under which tool results carry the view descriptor."""

HANDLE_PREFIX: Final[str] = "wsv_"
HANDLE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^wsv_[A-Za-z0-9_-]{43}$")
HANDLE_MAX_LENGTH: Final[int] = 256

DEFAULT_VIEW_TTL_SECONDS: Final[int] = 24 * 60 * 60
MIN_VIEW_TTL_SECONDS: Final[int] = 60
MAX_VIEW_TTL_SECONDS: Final[int] = 30 * 24 * 60 * 60
_RECORD_KIND: Final[str] = "dashboard"
_RECORD_VERSION: Final[int] = 1
_MAX_UNCONDITIONAL_ATTEMPTS: Final[int] = 16


class ViewHandleError(Exception):
    """The view handle is malformed, unknown, expired, or not the caller's."""

    code: Final[str] = "view_handle_invalid"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        if reason == "malformed":
            message = "The dashboard view handle is malformed."
        else:
            message = (
                "The dashboard view handle is unknown or has expired. "
                "Open a new dashboard view."
            )
        super().__init__(message)


class ViewStateConflict(Exception):
    """A conditional update lost the race to a newer revision."""

    code: Final[str] = "view_state_conflict"

    def __init__(self, expected_revision: int, current: "DashboardView") -> None:
        self.expected_revision = expected_revision
        self.current = current
        super().__init__(
            f"The dashboard view changed (expected revision {expected_revision}, "
            f"current revision {current.revision}). Refresh the view and retry."
        )


@dataclass(frozen=True, slots=True)
class DashboardView:
    """A resolved view: its handle, current revision, and state."""

    handle: str
    revision: int
    state: DashboardState
    expires_at: int
    ttl_seconds: int

    def descriptor(self) -> dict[str, Any]:
        """Public view metadata returned to the UI (and the model)."""
        return {
            "handle": self.handle,
            "revision": self.revision,
            "expires_at": self.expires_at,
            "ttl_seconds": self.ttl_seconds,
        }


def view_ttl_seconds_from_environment() -> int:
    """Sliding idle TTL for dashboard views (``MCP_APP_VIEW_TTL_SECONDS``)."""
    raw = os.getenv("MCP_APP_VIEW_TTL_SECONDS", "").strip()
    if not raw:
        return DEFAULT_VIEW_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"MCP_APP_VIEW_TTL_SECONDS must be an integer, received {raw!r}.") from exc
    if not MIN_VIEW_TTL_SECONDS <= value <= MAX_VIEW_TTL_SECONDS:
        raise ValueError(
            f"MCP_APP_VIEW_TTL_SECONDS must be between {MIN_VIEW_TTL_SECONDS} and "
            f"{MAX_VIEW_TTL_SECONDS}, received {value}."
        )
    return value


def mint_view_handle() -> str:
    return HANDLE_PREFIX + secrets.token_urlsafe(32)


def _validated_handle(handle: object) -> str:
    if not isinstance(handle, str) or not HANDLE_PATTERN.fullmatch(handle):
        raise ViewHandleError("malformed")
    return handle


class DashboardViewService:
    """Mint, resolve, and update dashboard views through one app-state store."""

    def __init__(
        self,
        store: AppStateStore | None = None,
        *,
        ttl_seconds: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._ttl_seconds = ttl_seconds
        self._clock = clock

    @property
    def store(self) -> AppStateStore:
        return self._store if self._store is not None else default_app_state_store()

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds if self._ttl_seconds is not None else view_ttl_seconds_from_environment()

    @staticmethod
    def _principal_key() -> str:
        return current_principal().storage_key

    @staticmethod
    def _storage_key(principal_key: str, handle: str) -> str:
        digest = sha256(handle.encode("ascii")).hexdigest()
        return f"view:{principal_key}:{digest}"

    def _view(self, handle: str, revision: int, record: dict[str, Any], ttl: int) -> DashboardView:
        return DashboardView(
            handle=handle,
            revision=revision,
            state=DashboardState.model_validate(record["state"]),
            expires_at=int(self._clock()) + ttl,
            ttl_seconds=ttl,
        )

    def _record(self, principal_key: str, state: DashboardState, created_at: int) -> dict[str, Any]:
        return {
            "v": _RECORD_VERSION,
            "kind": _RECORD_KIND,
            "principal": principal_key,
            "created_at": created_at,
            "state": state.model_dump(mode="json"),
        }

    def _owned(self, principal_key: str, record: dict[str, Any]) -> bool:
        owner = record.get("principal")
        return (
            record.get("v") == _RECORD_VERSION
            and record.get("kind") == _RECORD_KIND
            and isinstance(owner, str)
            and hmac.compare_digest(owner, principal_key)
        )

    async def create(self, state: DashboardState) -> DashboardView:
        """Mint a new handle for ``state``; every call yields an isolated view."""
        principal_key = self._principal_key()
        ttl = self.ttl_seconds
        while True:
            handle = mint_view_handle()
            record = self._record(principal_key, state, int(self._clock()))
            created = await self.store.create(
                self._storage_key(principal_key, handle), record, ttl_seconds=ttl
            )
            if created is not None:
                return self._view(handle, created.revision, created.value, ttl)

    async def _load(
        self, principal_key: str, handle: str, ttl: int
    ) -> tuple[dict[str, Any], DashboardView]:
        stored = await self.store.get(
            self._storage_key(principal_key, handle), refresh_ttl_seconds=ttl
        )
        if stored is None or not self._owned(principal_key, stored.value):
            raise ViewHandleError("unknown_or_expired")
        try:
            return stored.value, self._view(handle, stored.revision, stored.value, ttl)
        except (KeyError, ValidationError):
            raise ViewHandleError("unknown_or_expired") from None

    async def resolve(self, handle: object) -> DashboardView:
        """Authorize ``handle`` for the caller and extend its idle expiry."""
        valid = _validated_handle(handle)
        _, view = await self._load(self._principal_key(), valid, self.ttl_seconds)
        return view

    async def update(
        self,
        handle: object,
        mutate: Callable[[DashboardState], DashboardState],
        *,
        expected_revision: int | None = None,
    ) -> DashboardView:
        """Apply ``mutate`` atomically (compare-and-set on the revision).

        With ``expected_revision`` the write is conditional: if the view is at
        any other revision, nothing is written and :class:`ViewStateConflict`
        carries the current view. Without it, ``mutate`` is re-applied to the
        latest state until a compare-and-set succeeds (bounded).
        """
        valid = _validated_handle(handle)
        principal_key = self._principal_key()
        key = self._storage_key(principal_key, valid)
        ttl = self.ttl_seconds
        current: DashboardView | None = None
        for _ in range(_MAX_UNCONDITIONAL_ATTEMPTS):
            record, current = await self._load(principal_key, valid, ttl)
            if expected_revision is not None and current.revision != expected_revision:
                raise ViewStateConflict(expected_revision, current)
            updated_state = DashboardState.model_validate(
                mutate(current.state).model_dump(mode="json")
            )
            outcome = await self.store.compare_and_set(
                key,
                current.revision,
                {**record, "state": updated_state.model_dump(mode="json")},
                ttl_seconds=ttl,
            )
            if outcome.status == "ok" and outcome.record is not None:
                return self._view(valid, outcome.record.revision, outcome.record.value, ttl)
            if outcome.status == "missing" or outcome.record is None:
                raise ViewHandleError("unknown_or_expired")
            if expected_revision is not None:
                latest = self._view(valid, outcome.record.revision, outcome.record.value, ttl)
                raise ViewStateConflict(expected_revision, latest)
        # Persistent contention without a precondition: report it like a stale
        # write rather than looping forever.
        _, latest = await self._load(principal_key, valid, ttl)
        raise ViewStateConflict(current.revision if current else latest.revision, latest)


def shift_date(anchor: date, view: str, forward: bool) -> date:
    step = 1 if forward else -1
    if view in {"day", "agenda"}:
        return anchor + timedelta(days=step)
    if view == "week":
        return anchor + timedelta(days=7 * step)
    if view == "month":
        return anchor + relativedelta(months=step)
    return anchor


def next_range(state: DashboardState) -> DashboardState:
    return state.model_copy(update={"anchor_date": shift_date(state.anchor_date, state.view, True)})


def prev_range(state: DashboardState) -> DashboardState:
    return state.model_copy(update={"anchor_date": shift_date(state.anchor_date, state.view, False)})


def patch_state(state: DashboardState, patch: DashboardStatePatch) -> DashboardState:
    return state.model_copy(update=patch.model_dump(exclude_none=True))


def dashboard_views() -> DashboardViewService:
    """Process-wide service over the configured default store."""
    return _DEFAULT_SERVICE


_DEFAULT_SERVICE = DashboardViewService()
