"""Google Slides API client helpers."""

from __future__ import annotations

from typing import Any

from ..auth import build_slides_service


def slides_service() -> Any:
    return build_slides_service()
