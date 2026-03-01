"""Session-scoped dashboard state management."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from threading import Lock

from .schemas import DashboardState, DashboardStatePatch

_STATE_BY_SESSION: dict[str, DashboardState] = {}
_LOCK = Lock()


def _shift_date(anchor: date, view: str, forward: bool) -> date:
    step = 1 if forward else -1
    if view in {"day", "agenda"}:
        return anchor + timedelta(days=step)
    if view == "week":
        return anchor + timedelta(days=7 * step)
    if view == "month":
        # Keep month navigation deterministic without extra dependencies.
        return anchor + timedelta(days=30 * step)
    return anchor


def get_state(session_id: str, timezone: str | None = None) -> DashboardState:
    with _LOCK:
        existing = _STATE_BY_SESSION.get(session_id)
        if existing is None:
            created = DashboardState(session_id=session_id)
            if timezone:
                created.timezone = timezone
            _STATE_BY_SESSION[session_id] = created
            return deepcopy(created)
        return deepcopy(existing)


def set_state(session_id: str, state: DashboardState) -> DashboardState:
    with _LOCK:
        materialized = state.model_copy(update={"session_id": session_id})
        _STATE_BY_SESSION[session_id] = materialized
        return deepcopy(materialized)


def patch_state(session_id: str, patch: DashboardStatePatch) -> DashboardState:
    with _LOCK:
        current = _STATE_BY_SESSION.get(session_id, DashboardState(session_id=session_id))
        updates = patch.model_dump(exclude_none=True)
        merged = current.model_copy(update=updates)
        _STATE_BY_SESSION[session_id] = merged
        return deepcopy(merged)


def today(session_id: str) -> DashboardState:
    with _LOCK:
        current = _STATE_BY_SESSION.get(session_id, DashboardState(session_id=session_id))
        current.anchor_date = date.today()
        _STATE_BY_SESSION[session_id] = current
        return deepcopy(current)


def next_range(session_id: str) -> DashboardState:
    with _LOCK:
        current = _STATE_BY_SESSION.get(session_id, DashboardState(session_id=session_id))
        current.anchor_date = _shift_date(current.anchor_date, current.view, forward=True)
        _STATE_BY_SESSION[session_id] = current
        return deepcopy(current)


def prev_range(session_id: str) -> DashboardState:
    with _LOCK:
        current = _STATE_BY_SESSION.get(session_id, DashboardState(session_id=session_id))
        current.anchor_date = _shift_date(current.anchor_date, current.view, forward=False)
        _STATE_BY_SESSION[session_id] = current
        return deepcopy(current)
