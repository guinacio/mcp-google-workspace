"""Google Sheets API client helpers."""

from __future__ import annotations

from typing import Any

from ..auth import build_sheets_service


def sheets_service() -> Any:
    return build_sheets_service()
