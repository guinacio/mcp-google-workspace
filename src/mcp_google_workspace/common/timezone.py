"""Shared account-timezone lookup for Workspace features."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytz

from ..auth import build_calendar_service
from .async_ops import execute_google_request


class AccountTimezoneUnavailableError(RuntimeError):
    """Raised instead of guessing when the account timezone cannot be trusted."""


async def resolve_user_timezone() -> str:
    """Return the authenticated user's Calendar timezone.

    Calendar is the account-level source of truth for interpreting local dates and
    deadline times across Gmail and Calendar features. The server deliberately
    refuses to silently substitute UTC: a wrong timezone is worse than an
    actionable connection/configuration error.
    """
    service = build_calendar_service()
    settings: Any = await execute_google_request(
        service.settings().get(setting="timezone")
    )
    timezone = settings.get("value") if isinstance(settings, dict) else None
    if not isinstance(timezone, str) or not timezone.strip():
        raise AccountTimezoneUnavailableError(
            "The authenticated Google account does not expose a valid Calendar timezone."
        )
    timezone = timezone.strip()
    try:
        pytz.timezone(timezone)
    except pytz.UnknownTimeZoneError:
        raise AccountTimezoneUnavailableError(
            f"The authenticated Google account returned an invalid timezone: {timezone!r}."
        ) from None
    return timezone


def user_now(timezone_name: str) -> datetime:
    """Return the current aware datetime in a validated account timezone."""
    return datetime.now(pytz.timezone(timezone_name))


def in_account_timezone(value: str | None, account_timezone: str) -> str | None:
    """Normalize an RFC3339 provider timestamp into the account's IANA timezone."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Provider returned an invalid RFC3339 timestamp: {value!r}.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"Provider returned a timestamp without an offset: {value!r}.")
    return parsed.astimezone(pytz.timezone(account_timezone)).isoformat()
