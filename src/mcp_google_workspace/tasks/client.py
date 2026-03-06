"""Google Tasks API client helpers."""

from __future__ import annotations

from typing import Any

from ..auth import build_tasks_service


def tasks_service() -> Any:
    return build_tasks_service()
