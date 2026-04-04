"""Google Chat API client helpers."""

from __future__ import annotations

import logging
import time
from threading import Lock
from typing import Any

from googleapiclient.errors import HttpError

from ..auth import build_chat_service, build_gmail_service, build_people_service
from ..common.async_ops import execute_google_request

LOGGER = logging.getLogger(__name__)

_USER_CACHE_TTL = 900  # 15 minutes
_USER_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_USER_CACHE_LOCK = Lock()

_SELF_EMAIL: str | None = None
_SELF_EMAIL_LOCK = Lock()

_SELF_USER_NAME: str | None = None
_SELF_USER_NAME_LOCK = Lock()


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


def _get_cached_user(user_name: str) -> dict[str, Any] | None:
    with _USER_CACHE_LOCK:
        entry = _USER_CACHE.get(user_name)
        if entry is None:
            return None
        ts, data = entry
        if time.monotonic() - ts > _USER_CACHE_TTL:
            del _USER_CACHE[user_name]
            return None
        return data


def _set_cached_user(user_name: str, data: dict[str, Any]) -> None:
    with _USER_CACHE_LOCK:
        _USER_CACHE[user_name] = (time.monotonic(), data)


def _extract_email_hint(user_name: str) -> str | None:
    """Extract email from resource name like ``users/user@example.com``."""
    _, _, identifier = user_name.partition("/")
    if "@" in identifier:
        return identifier
    return None


def _describe_http_error(exc: HttpError) -> str:
    status = getattr(exc.resp, "status", "unknown")
    reason = getattr(exc.resp, "reason", "unknown")
    body = exc.content.decode("utf-8", errors="replace").strip() if exc.content else ""
    if len(body) > 500:
        body = f"{body[:497]}..."
    return f"status={status} reason={reason} body={body}"


async def _get_self_email() -> str | None:
    """Resolve the authenticated user's email via Gmail ``users.getProfile``.

    Cached for the lifetime of the process.
    """
    global _SELF_EMAIL  # noqa: PLW0603
    with _SELF_EMAIL_LOCK:
        if _SELF_EMAIL is not None:
            return _SELF_EMAIL
    try:
        service = build_gmail_service()
        profile = await execute_google_request(
            service.users().getProfile(userId="me")
        )
        email = profile.get("emailAddress")
        if email:
            with _SELF_EMAIL_LOCK:
                _SELF_EMAIL = email
            LOGGER.debug("Resolved self email: %s", email)
            return email
    except Exception:
        LOGGER.debug("Could not resolve self email via Gmail.", exc_info=True)
    return None


async def _resolve_self_user_name(space_name: str) -> str | None:
    """Resolve the authenticated user's Chat user resource name.

    Uses Chat's own ``spaces.members.get`` with the email alias
    (``spaces/{space}/members/{email}``) so the returned
    ``member.name`` is the *exact* Chat-internal ``users/{id}``
    that appears in membership lists.  Cached for the process lifetime.
    """
    global _SELF_USER_NAME  # noqa: PLW0603
    with _SELF_USER_NAME_LOCK:
        if _SELF_USER_NAME is not None:
            return _SELF_USER_NAME
    self_email = await _get_self_email()
    if not self_email:
        return None
    try:
        service = chat_service()
        membership = await execute_google_request(
            service.spaces().members().get(
                name=f"{space_name}/members/{self_email}"
            )
        )
        member = membership.get("member", {})
        user_name = member.get("name")
        if user_name:
            with _SELF_USER_NAME_LOCK:
                _SELF_USER_NAME = user_name
            LOGGER.debug("Resolved self Chat user name: %s", user_name)
            return user_name
    except Exception:
        LOGGER.debug(
            "Could not resolve self via spaces.members.get for %s.",
            space_name,
            exc_info=True,
        )
    return None


async def _resolve_user_profile(user_name: str) -> dict[str, Any]:
    """Resolve a Chat user's display name and email via the People API.

    The numeric ID in ``users/{id}`` is the same Google account ID used
    by the People API (``people/{id}``).  Under user-auth the Chat API
    only returns ``name`` and ``type`` for human members — ``displayName``
    is always empty — so we fall back to People API for profile data.

    Results are merged into the user cache.
    """
    cached = _get_cached_user(user_name)
    if cached and cached.get("displayName"):
        return cached

    _, _, numeric_id = user_name.partition("/")
    if not numeric_id:
        return cached or {"name": user_name}

    try:
        service = build_people_service()
        person = await execute_google_request(
            service.people().get(
                resourceName=f"people/{numeric_id}",
                personFields="names,emailAddresses",
            )
        )
        names = person.get("names", [])
        emails = person.get("emailAddresses", [])
        display_name = names[0].get("displayName") if names else None
        email = emails[0].get("value") if emails else None

        data: dict[str, Any] = {"name": user_name, "type": "HUMAN"}
        if display_name:
            data["displayName"] = display_name
        if email:
            data["email"] = email
        _set_cached_user(user_name, data)
        LOGGER.debug("People API resolved %s → %s <%s>", user_name, display_name, email)
        return data
    except HttpError as exc:
        LOGGER.warning(
            "People API lookup failed for %s (%s).",
            user_name,
            _describe_http_error(exc),
        )
        LOGGER.debug("People API lookup traceback for %s.", user_name, exc_info=True)
        return cached or {"name": user_name, "type": "HUMAN"}
    except Exception:
        LOGGER.debug("People API lookup failed for %s.", user_name, exc_info=True)
        return cached or {"name": user_name, "type": "HUMAN"}


async def resolve_space_members(
    space_name: str,
    *,
    exclude_self: bool = True,
    resolve_profiles: bool = True,
) -> list[dict[str, Any]]:
    """Fetch members of a space and return user data for each.

    When *exclude_self* is ``True`` the authenticated user is filtered
    out by matching their Chat user resource name, resolved once via
    ``spaces.members.get(email)``.

    When *resolve_profiles* is ``True`` (default), each remaining
    member's display name and email are resolved via the People API,
    since the Chat API does not return ``displayName`` under user-auth.
    """
    name = normalize_space_name(space_name)
    service = chat_service()
    result = await execute_google_request(
        service.spaces().members().list(parent=name, pageSize=100)
    )
    memberships = result.get("memberships", [])

    self_user_name: str | None = None
    if exclude_self:
        self_user_name = await _resolve_self_user_name(name)
        LOGGER.debug("Self-exclusion filter: %s", self_user_name)

    members: list[dict[str, Any]] = []
    for membership in memberships:
        member = membership.get("member", {})
        member_name = member.get("name", "")
        if not member_name:
            continue
        if member.get("type") == "BOT":
            continue
        if self_user_name and member_name == self_user_name:
            LOGGER.debug("Excluding self: %s", member_name)
            continue

        if resolve_profiles:
            user_data = await _resolve_user_profile(member_name)
        else:
            user_data = {"name": member_name, "type": member.get("type")}
            email_hint = _extract_email_hint(member_name)
            if email_hint:
                user_data["email"] = email_hint

        _set_cached_user(member_name, user_data)
        members.append(user_data)
        LOGGER.debug("Keeping peer: %s (%s)", member_name, user_data.get("displayName"))

    return members
