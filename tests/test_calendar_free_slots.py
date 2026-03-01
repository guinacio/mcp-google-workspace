from datetime import datetime

import pytz

from mcp_google_workspace.calendar.tools import (
    _apply_working_hours,
    _build_slot_candidates,
    _merge_time_ranges,
)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(pytz.UTC)


def test_merge_time_ranges_merges_overlap_and_touching() -> None:
    ranges = [
        (_dt("2026-03-01T10:00:00+00:00"), _dt("2026-03-01T11:00:00+00:00")),
        (_dt("2026-03-01T10:30:00+00:00"), _dt("2026-03-01T12:00:00+00:00")),
        (_dt("2026-03-01T12:00:00+00:00"), _dt("2026-03-01T12:30:00+00:00")),
    ]

    merged = _merge_time_ranges(ranges)

    assert len(merged) == 1
    assert merged[0][0].isoformat() == "2026-03-01T10:00:00+00:00"
    assert merged[0][1].isoformat() == "2026-03-01T12:30:00+00:00"


def test_build_slot_candidates_respects_duration_step_and_limit() -> None:
    free_ranges = [
        (_dt("2026-03-01T09:00:00+00:00"), _dt("2026-03-01T11:00:00+00:00")),
    ]

    slots = _build_slot_candidates(
        free_ranges=free_ranges,
        slot_duration_minutes=60,
        granularity_minutes=30,
        max_results=2,
    )

    assert len(slots) == 2
    assert slots[0] == {
        "start": "2026-03-01T09:00:00+00:00",
        "end": "2026-03-01T10:00:00+00:00",
    }
    assert slots[1] == {
        "start": "2026-03-01T09:30:00+00:00",
        "end": "2026-03-01T10:30:00+00:00",
    }


def test_apply_working_hours_uses_defaults_window() -> None:
    free_ranges = [
        (_dt("2026-03-01T06:00:00+00:00"), _dt("2026-03-01T20:00:00+00:00")),
    ]

    clamped = _apply_working_hours(
        free_ranges=free_ranges,
        working_hours_start="08:00",
        working_hours_end="17:00",
    )

    assert len(clamped) == 1
    assert clamped[0][0].isoformat() == "2026-03-01T08:00:00+00:00"
    assert clamped[0][1].isoformat() == "2026-03-01T17:00:00+00:00"


def test_apply_working_hours_allows_custom_window() -> None:
    free_ranges = [
        (_dt("2026-03-01T06:00:00+00:00"), _dt("2026-03-01T20:00:00+00:00")),
    ]

    clamped = _apply_working_hours(
        free_ranges=free_ranges,
        working_hours_start="09:30",
        working_hours_end="18:30",
    )

    assert len(clamped) == 1
    assert clamped[0][0].isoformat() == "2026-03-01T09:30:00+00:00"
    assert clamped[0][1].isoformat() == "2026-03-01T18:30:00+00:00"
