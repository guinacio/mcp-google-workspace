"""Google Keep API client helpers."""

from __future__ import annotations

from typing import Any

from ..auth import build_keep_service


def keep_service() -> Any:
    return build_keep_service()


def normalize_note_name(note_name: str) -> str:
    if note_name.startswith("notes/"):
        return note_name
    return f"notes/{note_name}"
