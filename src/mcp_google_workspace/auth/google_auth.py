"""Shared OAuth2 helpers for Google Workspace API services."""

from __future__ import annotations

import logging
import json
from hashlib import sha256
import os
from threading import Lock, RLock
from pathlib import Path
from typing import Any
from weakref import WeakValueDictionary

import httplib2
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import HttpRequest

from ..runtime import RuntimeSettings, get_runtime_settings
from ..runtime import get_token_storage_settings
from .identity import Principal, current_principal
from .token_store import (
    EncryptedTokenStore,
    RedisEncryptedTokenStore,
    TokenStore,
    TokenStoreError,
)

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.settings.basic",
]

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]

ACCOUNT_TIMEZONE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.settings.readonly",
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

CAPABILITY_SCOPES: dict[str, list[str]] = {
    "gmail": GMAIL_SCOPES,
    "calendar": CALENDAR_SCOPES,
    "drive": DRIVE_SCOPES,
    "sheets": SHEETS_SCOPES,
    "docs": DOCS_SCOPES,
    "tasks": TASKS_SCOPES,
    "people": PEOPLE_SCOPES,
    "forms": FORMS_SCOPES,
    "slides": SLIDES_SCOPES,
    "keep": KEEP_SCOPES,
    "chat": CHAT_SCOPES,
    "meet": MEET_SCOPES,
}

# These namespaces normalize provider timestamps in the user's Google Calendar
# timezone. They need only Calendar settings metadata, not broad Calendar access.
TIMEZONE_DEPENDENT_CAPABILITIES = {
    "gmail",
    "drive",
    "tasks",
    "forms",
    "keep",
    "chat",
    "meet",
}

API_CAPABILITY = {
    "gmail": "gmail",
    "calendar": "calendar",
    "drive": "drive",
    "sheets": "sheets",
    "docs": "docs",
    "tasks": "tasks",
    "people": "people",
    "forms": "forms",
    "slides": "slides",
    "keep": "keep",
    "chat": "chat",
    "meet": "meet",
}

LOGGER = logging.getLogger(__name__)
_CREDENTIAL_LOCKS_GUARD = Lock()
_CREDENTIAL_LOCKS: WeakValueDictionary[str, RLock] = WeakValueDictionary()


def _credential_lock(storage_key: str) -> RLock:
    with _CREDENTIAL_LOCKS_GUARD:
        return _CREDENTIAL_LOCKS.setdefault(storage_key, RLock())


class GoogleAccountConnectionRequired(PermissionError):
    """Raised when the authenticated MCP user has not connected Google yet."""


class GoogleAccountReauthenticationRequired(PermissionError):
    """Raised after an invalid per-user Google refresh token is discarded."""


def is_remote_oauth_mode() -> bool:
    """Return whether OAuth uses the authenticated remote callback flow."""
    return bool(os.getenv("MCP_GOOGLE_OAUTH_REDIRECT_URL", "").strip())


def _run_local_oauth(principal: Principal, credentials_path: Path, scopes: list[str]) -> Credentials:
    """Connect the local stdio principal without weakening the remote security model."""
    settings = get_runtime_settings()
    timeout_seconds = int(os.getenv("MCP_LOCAL_OAUTH_TIMEOUT_SECONDS", "180"))
    if not 30 <= timeout_seconds <= 900:
        raise ValueError("MCP_LOCAL_OAUTH_TIMEOUT_SECONDS must be between 30 and 900.")
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes)
    credentials = flow.run_local_server(
        port=settings.oauth_port,
        open_browser=settings.oauth_open_browser,
        timeout_seconds=timeout_seconds,
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


def get_google_scopes(capabilities: list[str] | None = None) -> list[str]:
    if capabilities is not None:
        unknown = sorted(set(capabilities) - set(CAPABILITY_SCOPES))
        if unknown:
            raise ValueError(
                f"Unknown Google capabilities: {', '.join(unknown)}. "
                f"Choose from: {', '.join(sorted(CAPABILITY_SCOPES))}."
            )
        capability_scopes = {
            scope
            for capability in capabilities
            for scope in CAPABILITY_SCOPES[capability]
        }
        if set(capabilities) & TIMEZONE_DEPENDENT_CAPABILITIES:
            capability_scopes.update(ACCOUNT_TIMEZONE_SCOPES)
        return sorted(capability_scopes)
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
        *ACCOUNT_TIMEZONE_SCOPES,
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


def get_token_store() -> TokenStore:
    settings = get_token_storage_settings()
    redis_url = os.getenv("MCP_TOKEN_REDIS_URL", "").strip()
    if not redis_url and is_remote_oauth_mode():
        redis_url = os.getenv("MCP_REDIS_URL", "").strip()
    if redis_url:
        return RedisEncryptedTokenStore(redis_url, settings.keyring)
    return EncryptedTokenStore(settings.user_token_dir, settings.keyring)


def delete_cached_token(
    principal: Principal | None = None,
    *,
    expected_fingerprint: str | None = None,
) -> bool:
    """Delete credentials only for the authenticated user that owns the token."""
    principal = principal or current_principal()
    try:
        if expected_fingerprint is None:
            get_token_store().delete_credentials(principal)
        else:
            deleted = get_token_store().delete_credentials_if_fingerprint(
                principal, expected_fingerprint
            )
            if not deleted:
                LOGGER.info(
                    "Preserved newer Google OAuth token for principal %s.",
                    principal.storage_key,
                )
                return False
        LOGGER.info("Deleted invalid Google OAuth token for principal %s.", principal.storage_key)
    except TokenStoreError as exc:
        LOGGER.warning("Failed to delete invalid Google OAuth token for principal %s: %s", principal.storage_key, exc)
        return False
    return True


def _get_credentials_unlocked(required_scopes: list[str] | None = None) -> Credentials:
    """Load and refresh credentials isolated to the authenticated MCP user."""
    principal = current_principal()
    credentials_path = resolve_client_credentials_path()
    remote_mode = is_remote_oauth_mode()
    # A local MCPB has one trusted user and one stored grant. Request the complete
    # enabled catalog once so cross-service helpers (for example Calendar timezone
    # normalization inside Gmail) do not replace each other's narrower grants and
    # launch multiple browser consent flows on every tool call. Remote HTTP remains
    # capability-incremental because each principal has an isolated grant/catalog.
    scopes = (
        required_scopes or get_google_scopes()
        if remote_mode
        else get_google_scopes()
    )
    if not credentials_path.exists():
        raise FileNotFoundError(
            "Google OAuth client credentials.json not found. Set MCP_CREDENTIALS_DIR or place it in the project root."
        )

    token_json = get_token_store().load_credentials_json(principal)
    if token_json is None:
        if not remote_mode:
            return _run_local_oauth(principal, credentials_path, scopes)
        raise GoogleAccountConnectionRequired(
            "Google Workspace is not connected for this user. Call connect_google_workspace and complete consent."
        )
    creds = Credentials.from_authorized_user_info(json.loads(token_json))
    setattr(creds, "_mcp_token_fingerprint", sha256(token_json.encode("utf-8")).hexdigest())
    if creds and not creds.has_scopes(scopes):
        if not remote_mode:
            return _run_local_oauth(principal, credentials_path, scopes)
        raise GoogleAccountConnectionRequired(
            "The Google connection is missing scopes for this capability. "
            "Call connect_google_workspace with the required capability to grant it incrementally."
        )

    if creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        LOGGER.info("Refreshing Google OAuth token.")
        try:
            creds.refresh(Request())
            from ..common.production import OAUTH_REFRESHES

            OAUTH_REFRESHES.labels("ok").inc()
        except RefreshError as exc:
            from ..common.production import OAUTH_REFRESHES

            OAUTH_REFRESHES.labels("error").inc()
            LOGGER.warning(
                "Refresh failed for principal %s (%s); deleting that user's stale token.",
                principal.storage_key,
                exc,
            )
            delete_cached_token(principal)
            if not remote_mode:
                return _run_local_oauth(principal, credentials_path, scopes)
            raise GoogleAccountReauthenticationRequired(
                "Google authorization expired for this user. Call connect_google_workspace to reconnect."
            ) from exc

    if not creds or not creds.valid:
        raise GoogleAccountConnectionRequired(
            "Google Workspace is not connected for this user. Call connect_google_workspace and complete consent."
        )

    refreshed_json = creds.to_json()
    get_token_store().save_credentials_json(principal, refreshed_json)
    setattr(
        creds,
        "_mcp_token_fingerprint",
        sha256(refreshed_json.encode("utf-8")).hexdigest(),
    )
    return creds


def get_credentials(required_scopes: list[str] | None = None) -> Credentials:
    """Load/refresh credentials under local and cross-replica critical sections."""
    principal = current_principal()
    store = get_token_store()
    with _credential_lock(principal.storage_key), store.credential_lock(principal):
        return _get_credentials_unlocked(required_scopes)


def _build_authorized_http(
    credentials: Credentials, settings: RuntimeSettings, api_name: str
) -> AuthorizedHttp:
    return InstrumentedAuthorizedHttp(
        credentials,
        api_name=api_name,
        http=httplib2.Http(timeout=settings.http_timeout_seconds),
    )


class InstrumentedAuthorizedHttp(AuthorizedHttp):
    def __init__(self, credentials: Credentials, *, api_name: str, http: Any) -> None:
        super().__init__(credentials, http=http)
        self.api_name = api_name

    def request(self, *args: Any, **kwargs: Any) -> Any:
        from ..common.production import GOOGLE_HTTP_ATTEMPTS

        GOOGLE_HTTP_ATTEMPTS.labels(self.api_name).inc()
        return super().request(*args, **kwargs)


class RetryingHttpRequest(HttpRequest):
    """Apply the configured retry budget to every Google API request."""

    def execute(self, http: Any = None, num_retries: int = 0) -> Any:
        configured_retries = get_runtime_settings().http_retries
        try:
            return super().execute(
                http=http, num_retries=max(num_retries, configured_retries)
            )
        except Exception as exc:
            _conditionally_invalidate_failed_credentials(self, exc)
            raise


def _conditionally_invalidate_failed_credentials(request: Any, exc: Exception) -> None:
    status = getattr(getattr(exc, "resp", None), "status", None)
    message = str(exc).lower()
    if status != 401 and not any(
        marker in message
        for marker in ("invalid_grant", "invalid_token", "unauthenticated", "token has been expired")
    ):
        return
    authorized_http = getattr(request, "http", None)
    credentials = getattr(authorized_http, "credentials", None)
    fingerprint = getattr(credentials, "_mcp_token_fingerprint", None)
    if isinstance(fingerprint, str):
        delete_cached_token(expected_fingerprint=fingerprint)
    setattr(exc, "_mcp_credentials_checked", True)


class LazyGoogleRequest:
    """Record client calls and materialize them only where execution occurs."""

    def __init__(
        self,
        api_name: str,
        version: str,
        operations: tuple[tuple[str, Any], ...] = (),
    ) -> None:
        self._api_name = api_name
        self._version = version
        self._operations = operations

    def __getattr__(self, name: str) -> "LazyGoogleRequest":
        return LazyGoogleRequest(
            self._api_name,
            self._version,
            (*self._operations, ("attribute", name)),
        )

    def __call__(self, *args: Any, **kwargs: Any) -> "LazyGoogleRequest":
        return LazyGoogleRequest(
            self._api_name,
            self._version,
            (*self._operations, ("call", (args, kwargs))),
        )

    def materialize(self) -> Any:
        value = _build_service_now(self._api_name, self._version)
        for operation, payload in self._operations:
            if operation == "attribute":
                value = getattr(value, payload)
            else:
                args, kwargs = payload
                value = value(*args, **kwargs)
        return value

    def execute(self, http: Any = None, num_retries: int = 0) -> Any:
        request = self.materialize()
        configured_retries = get_runtime_settings().http_retries
        return request.execute(
            http=http,
            num_retries=max(num_retries, configured_retries),
        )


def materialize_google_request(request: Any) -> Any:
    """Materialize a lazy request for chunked downloader consumers."""
    if isinstance(request, LazyGoogleRequest):
        return request.materialize()
    return request


def _build_service_now(api_name: str, version: str) -> Any:
    settings = get_runtime_settings()
    capability = API_CAPABILITY.get(api_name)
    required_scopes = get_google_scopes([capability]) if capability else get_google_scopes()
    credentials = get_credentials(required_scopes)
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
        http=_build_authorized_http(credentials, settings, api_name),
        cache_discovery=False,
        num_retries=settings.http_retries,
        requestBuilder=RetryingHttpRequest,
    )


def _build_service(api_name: str, version: str) -> LazyGoogleRequest:
    return LazyGoogleRequest(api_name, version)


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
