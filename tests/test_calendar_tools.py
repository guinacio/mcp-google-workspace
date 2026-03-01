from mcp_google_workspace.calendar.tools import _validate_and_fix_datetime


def test_validate_and_fix_datetime_adds_timezone():
    result = _validate_and_fix_datetime("2026-02-28T10:30:00", "UTC")
    assert result is not None
    assert result.endswith("+00:00")


def test_validate_and_fix_datetime_date_only():
    result = _validate_and_fix_datetime("2026-02-28", "UTC")
    assert result is not None
    assert "T00:00:00" in result
