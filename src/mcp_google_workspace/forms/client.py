"""Google Forms API client helpers."""

from __future__ import annotations

from typing import Any

from ..auth import build_forms_service


def forms_service() -> Any:
    return build_forms_service()
