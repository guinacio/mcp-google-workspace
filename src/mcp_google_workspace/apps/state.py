"""Session-scoped dashboard state management."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from threading import Lock
import time

from dateutil.relativedelta import relativedelta

from .schemas import DashboardState, DashboardStatePatch

_STATE_BY_SESSION: dict[str, DashboardState] = {}
_STATE_LAST_ACCESSED: dict[str, float] = {}
_STATE_TTL_SECONDS = 24 * 60 * 60
_MAX_SESSIONS = 1_000
_LOCK = Lock()


def _prune_state_locked() -> None:
    now = time.monotonic()
    expired = [
        session_id
        for session_id, touched in _STATE_LAST_ACCESSED.items()
        if now - touched > _STATE_TTL_SECONDS
    ]
    for session_id in expired:
        _STATE_BY_SESSION.pop(session_id, None)
        _STATE_LAST_ACCESSED.pop(session_id, None)
    overflow = len(_STATE_BY_SESSION) - _MAX_SESSIONS
    if overflow > 0:
        oldest = sorted(
            _STATE_LAST_ACCESSED,
            key=lambda session_id: _STATE_LAST_ACCESSED[session_id],
        )[:overflow]
        for session_id in oldest:
            _STATE_BY_SESSION.pop(session_id, None)
            _STATE_LAST_ACCESSED.pop(session_id, None)


def _touch_state_locked(session_id: str) -> None:
    _STATE_LAST_ACCESSED[session_id] = time.monotonic()


def _shift_date(anchor: date, view: str, forward: bool) -> date:
    step = 1 if forward else -1
    if view in {"day", "agenda"}:
        return anchor + timedelta(days=step)
    if view == "week":
        return anchor + timedelta(days=7 * step)
    if view == "month":
        return anchor + relativedelta(months=step)
    return anchor


def get_state(
    session_id: str,
    timezone: str | None = None,
    anchor_date: date | None = None,
) -> DashboardState:
    with _LOCK:
        _prune_state_locked()
        existing = _STATE_BY_SESSION.get(session_id)
        if existing is None:
            created = DashboardState(
                session_id=session_id,
                anchor_date=anchor_date or date.today(),
            )
            if timezone:
                created.timezone = timezone
            _STATE_BY_SESSION[session_id] = created
            _touch_state_locked(session_id)
            return deepcopy(created)
        _touch_state_locked(session_id)
        return deepcopy(existing)


def set_state(session_id: str, state: DashboardState) -> DashboardState:
    with _LOCK:
        _prune_state_locked()
        materialized = state.model_copy(update={"session_id": session_id})
        _STATE_BY_SESSION[session_id] = materialized
        _touch_state_locked(session_id)
        return deepcopy(materialized)


def patch_state(session_id: str, patch: DashboardStatePatch) -> DashboardState:
    with _LOCK:
        _prune_state_locked()
        current = _STATE_BY_SESSION.get(session_id, DashboardState(session_id=session_id))
        updates = patch.model_dump(exclude_none=True)
        merged = current.model_copy(update=updates)
        _STATE_BY_SESSION[session_id] = merged
        _touch_state_locked(session_id)
        return deepcopy(merged)


def today(session_id: str, *, current_date: date | None = None) -> DashboardState:
    with _LOCK:
        _prune_state_locked()
        current = _STATE_BY_SESSION.get(session_id, DashboardState(session_id=session_id))
        current.anchor_date = current_date or date.today()
        _STATE_BY_SESSION[session_id] = current
        _touch_state_locked(session_id)
        return deepcopy(current)


def next_range(session_id: str) -> DashboardState:
    with _LOCK:
        _prune_state_locked()
        current = _STATE_BY_SESSION.get(session_id, DashboardState(session_id=session_id))
        current.anchor_date = _shift_date(current.anchor_date, current.view, forward=True)
        _STATE_BY_SESSION[session_id] = current
        _touch_state_locked(session_id)
        return deepcopy(current)


def prev_range(session_id: str) -> DashboardState:
    with _LOCK:
        _prune_state_locked()
        current = _STATE_BY_SESSION.get(session_id, DashboardState(session_id=session_id))
        current.anchor_date = _shift_date(current.anchor_date, current.view, forward=False)
        _STATE_BY_SESSION[session_id] = current
        _touch_state_locked(session_id)
        return deepcopy(current)
