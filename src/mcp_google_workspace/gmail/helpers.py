"""Shared helper functions for Gmail tools."""

from __future__ import annotations

from typing import Any

from .schemas import AttachmentInput, RecipientSet


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


def attachment_inputs(items: list[dict[str, Any]] | None) -> list[AttachmentInput]:
    return [AttachmentInput.model_validate(item) for item in (items or [])]
