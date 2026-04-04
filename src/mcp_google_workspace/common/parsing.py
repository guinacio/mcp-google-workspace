"""Shared parsing utilities used across multiple workspace modules."""

from __future__ import annotations


def normalize_participants(participants: list[str] | str | None) -> list[str]:
    """Normalise a *participants* argument into a plain ``list[str]``.

    Accepts:
    * ``None`` – returns an empty list (callers should apply their own default).
    * A ``str`` – interpreted as either a JSON-encoded array or a
      comma-separated list of identifiers.
    * A ``list[str]`` – returned as-is.
    """
    if participants is None:
        return []
    if isinstance(participants, str):
        raw = participants.strip()
        if not raw:
            return []
        if raw.startswith("["):
            import json

            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in raw.split(",") if item.strip()]
    return participants
