"""Google Docs API client helpers."""

from __future__ import annotations

from typing import Any

from ..auth import build_docs_service


def docs_service() -> Any:
    return build_docs_service()
