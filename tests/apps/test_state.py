from __future__ import annotations

from datetime import date

from mcp_google_workspace.apps.schemas import DashboardStatePatch
from mcp_google_workspace.apps.state import get_state, next_range, patch_state, prev_range, today


def test_state_defaults_and_patch():
    state = get_state("test-session-state")
    assert state.session_id == "test-session-state"
    assert state.view == "week"

    updated = patch_state(
        "test-session-state",
        DashboardStatePatch(view="day", timezone="America/Sao_Paulo", selected_calendars=["primary", "team"]),
    )
    assert updated.view == "day"
    assert updated.timezone == "America/Sao_Paulo"
    assert updated.selected_calendars == ["primary", "team"]


def test_state_navigation():
    session_id = "test-session-navigation"
    patch_state(session_id, DashboardStatePatch(view="week", anchor_date=date(2026, 3, 1)))
    next_state = next_range(session_id)
    assert next_state.anchor_date == date(2026, 3, 8)

    prev_state = prev_range(session_id)
    assert prev_state.anchor_date == date(2026, 3, 1)

    today_state = today(session_id)
    assert isinstance(today_state.anchor_date, date)
