"""Google Meet API client helpers."""

from __future__ import annotations

from typing import Any

from ..auth import build_meet_service


def meet_service() -> Any:
    return build_meet_service()


def normalize_space_name(space_name: str) -> str:
    if space_name.startswith("spaces/"):
        return space_name
    return f"spaces/{space_name}"


def normalize_conference_record_name(record_name: str) -> str:
    if record_name.startswith("conferenceRecords/"):
        return record_name
    return f"conferenceRecords/{record_name}"
