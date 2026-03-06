"""Shared OAuth2 helpers for Google Workspace API services."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

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
    "https://www.googleapis.com/auth/chat.spaces.readonly",
]

MEET_SCOPES = [
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/meetings.space.readonly",
]


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


def _credentials_paths() -> tuple[Path, Path]:
    env_dir = os.environ.get("MCP_CREDENTIALS_DIR")
    if env_dir:
        directory = Path(env_dir)
        creds, token = directory / "credentials.json", directory / "token.json"
        if creds.exists():
            return creds, token

    src_dir = Path(__file__).resolve().parent.parent.parent
    package_credentials = src_dir / "credentials" / "credentials.json"
    package_token = src_dir / "credentials" / "token.json"
    if package_credentials.exists():
        return package_credentials, package_token

    cwd = Path.cwd()
    credentials = cwd / "credentials.json"
    token = cwd / "token.json"
    if credentials.exists():
        return credentials, token

    return cwd / "src" / "credentials" / "credentials.json", cwd / "src" / "credentials" / "token.json"


def get_credentials() -> Credentials:
    """Load or create OAuth credentials with browser-based consent flow."""
    credentials_path, token_path = _credentials_paths()
    scopes = get_google_scopes()
    if not credentials_path.exists():
        raise FileNotFoundError(
            "credentials.json not found. Place it at project root or src/credentials/credentials.json."
        )

    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path))
        if creds and not creds.has_scopes(scopes):
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes)
        creds = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _build_service(api_name: str, version: str) -> Any:
    return build(api_name, version, credentials=get_credentials())


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
