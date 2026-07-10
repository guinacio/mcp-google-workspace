"""Shared OAuth2 helpers for Google Workspace API services."""

from __future__ import annotations

import logging
import json
import os
from pathlib import Path
from typing import Any

import httplib2
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from ..runtime import RuntimeSettings, get_runtime_settings
from ..runtime import get_token_storage_settings
from .identity import Principal, current_principal
from .token_store import EncryptedTokenStore, TokenStoreError

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.settings.basic",
]

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
]

SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

DOCS_SCOPES = [
    "https://www.googleapis.com/auth/documents",
]

TASKS_SCOPES = [
    "https://www.googleapis.com/auth/tasks",
]

PEOPLE_SCOPES = [
    "https://www.googleapis.com/auth/contacts",
]

FORMS_SCOPES = [
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/forms.responses.readonly",
]

SLIDES_SCOPES = [
    "https://www.googleapis.com/auth/presentations",
]

KEEP_SCOPES = [
    "https://www.googleapis.com/auth/keep",
]

CHAT_SCOPES = [
    "https://www.googleapis.com/auth/chat.messages",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/chat.memberships.readonly",
    "https://www.googleapis.com/auth/chat.spaces.readonly",
]

MEET_SCOPES = [
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/meetings.space.readonly",
]

LOGGER = logging.getLogger(__name__)


class GoogleAccountConnectionRequired(PermissionError):
    """Raised when the authenticated MCP user has not connected Google yet."""


class GoogleAccountReauthenticationRequired(PermissionError):
    """Raised after an invalid per-user Google refresh token is discarded."""


def _is_sse_oauth_mode() -> bool:
    return bool(os.getenv("MCP_GOOGLE_OAUTH_REDIRECT_URL", "").strip())


def _run_local_oauth(principal: Principal, credentials_path: Path, scopes: list[str]) -> Credentials:
    """Connect the local stdio principal without weakening the SSE security model."""
    settings = get_runtime_settings()
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes)
    credentials = flow.run_local_server(
        port=settings.oauth_port,
        open_browser=settings.oauth_open_browser,
    )
    get_token_store().save_credentials_json(principal, credentials.to_json())
    return credentials


def _env_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_keep_enabled() -> bool:
    """Feature flag for Keep integration."""
    return _env_truthy(os.getenv("ENABLE_KEEP"))


def is_chat_enabled() -> bool:
    """Feature flag for Chat integration."""
    return _env_truthy(os.getenv("ENABLE_CHAT"))


def is_apps_dashboard_enabled() -> bool:
    """Feature flag for MCP app-layer dashboard namespace."""
    return _env_truthy(os.getenv("ENABLE_APPS_DASHBOARD"))


def is_meet_enabled() -> bool:
    """Feature flag for Google Meet integration."""
    return _env_truthy(os.getenv("ENABLE_MEET"))


def is_gemini_enabled() -> bool:
    """Feature flag for Gemini media integration."""
    return _env_truthy(os.getenv("ENABLE_GEMINI"))


def get_google_scopes() -> list[str]:
    scopes = [
        *GMAIL_SCOPES,
        *CALENDAR_SCOPES,
        *DRIVE_SCOPES,
        *SHEETS_SCOPES,
        *DOCS_SCOPES,
        *TASKS_SCOPES,
        *PEOPLE_SCOPES,
        *FORMS_SCOPES,
        *SLIDES_SCOPES,
    ]
    if is_chat_enabled():
        scopes.extend(CHAT_SCOPES)
    if is_keep_enabled():
        scopes.extend(KEEP_SCOPES)
    if is_meet_enabled():
        scopes.extend(MEET_SCOPES)
    return sorted(set(scopes))


def resolve_client_credentials_path() -> Path:
    """Locate the server-owned Google OAuth client configuration, never a user token."""
    env_dir = os.environ.get("MCP_CREDENTIALS_DIR")
    if env_dir:
        directory = Path(env_dir)
        credentials = directory / "credentials.json"
        if credentials.exists():
            return credentials

    src_dir = Path(__file__).resolve().parent.parent.parent
    package_credentials = src_dir / "credentials" / "credentials.json"
    if package_credentials.exists():
        return package_credentials

    cwd = Path.cwd()
    credentials = cwd / "credentials.json"
    if credentials.exists():
        return credentials

    return cwd / "src" / "credentials" / "credentials.json"


def get_token_store() -> EncryptedTokenStore:
    settings = get_token_storage_settings()
    return EncryptedTokenStore(settings.user_token_dir, settings.token_encryption_key)


def delete_cached_token(principal: Principal | None = None) -> bool:
    """Delete credentials only for the authenticated user that owns the token."""
    principal = principal or current_principal()
    try:
        get_token_store().delete_credentials(principal)
        LOGGER.info("Deleted invalid Google OAuth token for principal %s.", principal.storage_key)
    except TokenStoreError as exc:
        LOGGER.warning("Failed to delete invalid Google OAuth token for principal %s: %s", principal.storage_key, exc)
        return False
    return True


def get_credentials() -> Credentials:
    """Load and refresh credentials isolated to the authenticated MCP user."""
    principal = current_principal()
    credentials_path = resolve_client_credentials_path()
    scopes = get_google_scopes()
    if not credentials_path.exists():
        raise FileNotFoundError(
            "Google OAuth client credentials.json not found. Set MCP_CREDENTIALS_DIR or place it in the project root."
        )

    token_json = get_token_store().load_credentials_json(principal)
    if token_json is None:
        if not _is_sse_oauth_mode():
            return _run_local_oauth(principal, credentials_path, scopes)
        raise GoogleAccountConnectionRequired(
            "Google Workspace is not connected for this user. Call connect_google_workspace and complete consent."
        )
    creds = Credentials.from_authorized_user_info(json.loads(token_json), scopes=scopes)
    if creds and not creds.has_scopes(scopes):
        delete_cached_token(principal)
        if not _is_sse_oauth_mode():
            return _run_local_oauth(principal, credentials_path, scopes)
        raise GoogleAccountConnectionRequired(
            "The Google connection is missing required scopes. Call connect_google_workspace to reconnect."
        )

    if creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        LOGGER.info("Refreshing Google OAuth token.")
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            LOGGER.warning(
                "Refresh failed for principal %s (%s); deleting that user's stale token.",
                principal.storage_key,
                exc,
            )
            delete_cached_token(principal)
            if not _is_sse_oauth_mode():
                return _run_local_oauth(principal, credentials_path, scopes)
            raise GoogleAccountReauthenticationRequired(
                "Google authorization expired for this user. Call connect_google_workspace to reconnect."
            ) from exc

    if not creds or not creds.valid:
        raise GoogleAccountConnectionRequired(
            "Google Workspace is not connected for this user. Call connect_google_workspace and complete consent."
        )

    get_token_store().save_credentials_json(principal, creds.to_json())
    return creds


def _build_authorized_http(
    credentials: Credentials, settings: RuntimeSettings
) -> AuthorizedHttp:
    return AuthorizedHttp(
        credentials, http=httplib2.Http(timeout=settings.http_timeout_seconds)
    )


def _build_service(api_name: str, version: str) -> Any:
    settings = get_runtime_settings()
    credentials = get_credentials()
    LOGGER.debug(
        "Building Google API client for %s %s with timeout=%ss retries=%s.",
        api_name,
        version,
        settings.http_timeout_seconds,
        settings.http_retries,
    )
    return build(
        api_name,
        version,
        http=_build_authorized_http(credentials, settings),
        cache_discovery=False,
        num_retries=settings.http_retries,
    )


def build_gmail_service() -> Any:
    return _build_service("gmail", "v1")


def build_calendar_service() -> Any:
    return _build_service("calendar", "v3")


def build_drive_service() -> Any:
    return _build_service("drive", "v3")


def build_sheets_service() -> Any:
    return _build_service("sheets", "v4")


def build_docs_service() -> Any:
    return _build_service("docs", "v1")


def build_tasks_service() -> Any:
    return _build_service("tasks", "v1")


def build_people_service() -> Any:
    return _build_service("people", "v1")


def build_forms_service() -> Any:
    return _build_service("forms", "v1")


def build_slides_service() -> Any:
    return _build_service("slides", "v1")


def build_keep_service() -> Any:
    return _build_service("keep", "v1")


def build_chat_service() -> Any:
    return _build_service("chat", "v1")


def build_meet_service() -> Any:
    return _build_service("meet", "v2")
