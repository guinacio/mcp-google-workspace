"""Authentication helpers for Google APIs."""

from .google_auth import (
    CALENDAR_SCOPES,
    CHAT_SCOPES,
    DRIVE_SCOPES,
    GMAIL_SCOPES,
    KEEP_SCOPES,
    build_calendar_service,
    build_chat_service,
    build_drive_service,
    build_gmail_service,
    build_keep_service,
    get_google_scopes,
    get_credentials,
    is_chat_enabled,
    is_apps_dashboard_enabled,
    is_keep_enabled,
)

__all__ = [
    "CALENDAR_SCOPES",
    "CHAT_SCOPES",
    "DRIVE_SCOPES",
    "GMAIL_SCOPES",
    "KEEP_SCOPES",
    "build_calendar_service",
    "build_chat_service",
    "build_drive_service",
    "build_gmail_service",
    "build_keep_service",
    "get_google_scopes",
    "get_credentials",
    "is_chat_enabled",
    "is_apps_dashboard_enabled",
    "is_keep_enabled",
]
