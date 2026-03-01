"""Gmail API wrappers shared by tools/resources."""

from __future__ import annotations

from typing import Any

from ..auth import build_gmail_service


def gmail_service() -> Any:
    return build_gmail_service()
