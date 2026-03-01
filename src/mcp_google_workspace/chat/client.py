"""Google Chat API client helpers."""

from __future__ import annotations

from typing import Any

from ..auth import build_chat_service


def chat_service() -> Any:
    return build_chat_service()


def normalize_space_name(space_name: str) -> str:
    if space_name.startswith("spaces/"):
        return space_name
    return f"spaces/{space_name}"


def normalize_message_name(message_name: str) -> str:
    if message_name.startswith("spaces/") and "/messages/" in message_name:
        return message_name
    raise ValueError("message_name must be in format spaces/{space}/messages/{message}")


def normalize_user_name(user_name: str) -> str:
    if user_name.startswith("users/"):
        return user_name
    return f"users/{user_name}"
