"""Compact, person-aware representations for Google Chat resources."""

from __future__ import annotations

import re
from typing import Any

from .client import resolve_chat_users


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def user_envelope(user: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Present a Chat sender/member without exposing an opaque ID as the primary value."""
    profile = profile or {}
    return {
        "name": profile.get("displayName") or user.get("displayName") or user.get("name"),
        "email": profile.get("email"),
        "user_id": user.get("name"),
        "type": user.get("type") or profile.get("type"),
    }


def space_envelope(space: dict[str, Any], peer: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the small set of fields needed to choose a Chat conversation."""
    is_dm = space.get("spaceType") == "DIRECT_MESSAGE"
    return {
        "id": space.get("name"),
        "display_name": space.get("displayName") or (peer or {}).get("displayName"),
        "space_type": space.get("spaceType"),
        "is_direct_message": is_dm,
        "peer": (
            {
                "name": (peer or {}).get("displayName") or (peer or {}).get("name"),
                "email": (peer or {}).get("email"),
                "user_id": (peer or {}).get("name"),
            }
            if is_dm and peer
            else None
        ),
    }


def message_envelope(
    message: dict[str, Any], profile: dict[str, Any] | None = None, *, max_text: int | None = 500
) -> dict[str, Any]:
    """Return a triage-friendly Chat message while retaining reply/thread identity."""
    text = _clean_text(str(message.get("text") or message.get("formattedText") or ""))
    thread_name = message.get("thread", {}).get("name") if isinstance(message.get("thread"), dict) else None
    return {
        "id": message.get("name"),
        "space_id": str(message.get("name", "")).split("/messages/", 1)[0] or None,
        "author": user_envelope(message.get("sender", {}), profile),
        "text": text[:max_text] if max_text is not None else text,
        "text_truncated": max_text is not None and len(text) > max_text,
        "created_at": message.get("createTime"),
        "updated_at": message.get("lastUpdateTime"),
        "thread_id": thread_name,
        "has_attachments": bool(message.get("attachment")),
        "attachment_count": len(message.get("attachment", [])),
        "has_cards": bool(message.get("cardsV2") or message.get("cards")),
    }


async def enrich_messages(
    messages: list[dict[str, Any]], *, max_text: int | None = 500
) -> list[dict[str, Any]]:
    """Resolve unique human authors once, then return compact message envelopes."""
    user_names = {
        str(message.get("sender", {}).get("name"))
        for message in messages
        if isinstance(message.get("sender"), dict)
        and message["sender"].get("name")
        and message["sender"].get("type") != "BOT"
    }
    profiles = await resolve_chat_users(user_names)
    return [
        message_envelope(
            message, profiles.get(str(message.get("sender", {}).get("name"))), max_text=max_text
        )
        for message in messages
    ]
