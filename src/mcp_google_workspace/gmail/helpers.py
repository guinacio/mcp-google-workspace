"""Shared helper functions for Gmail tools."""

from __future__ import annotations

from .schemas import RecipientSet


def recipient_set(
    *,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> RecipientSet:
    return RecipientSet(
        to=to or [],
        cc=cc or [],
        bcc=bcc or [],
    )
