from __future__ import annotations

from datetime import date

from mcp_google_workspace.apps.schemas import DashboardState
from mcp_google_workspace.apps.tools import _compute_window


def test_compute_window_for_week_with_weekend_uses_sunday_start() -> None:
    state = DashboardState(
        session_id="apps-tools-test",
        view="week",
        anchor_date=date(2026, 3, 4),
        timezone="UTC",
        include_weekend=True,
    )

    time_min, time_max = _compute_window(state)

    assert time_min == "2026-03-01T00:00:00+00:00"
    assert time_max == "2026-03-08T00:00:00+00:00"


def test_compute_window_for_weekday_only_uses_monday_start() -> None:
    state = DashboardState(
        session_id="apps-tools-test",
        view="week",
        anchor_date=date(2026, 3, 4),
        timezone="UTC",
        include_weekend=False,
    )

    time_min, time_max = _compute_window(state)

    assert time_min == "2026-03-02T00:00:00+00:00"
    assert time_max == "2026-03-07T00:00:00+00:00"
