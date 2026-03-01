"""Shared OAuth2 helpers for Gmail + Calendar + Keep + Chat + Drive services."""

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

KEEP_SCOPES = [
    "https://www.googleapis.com/auth/keep",
]

CHAT_SCOPES = [
    "https://www.googleapis.com/auth/chat.messages",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/chat.spaces.readonly",
]

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
]


def _env_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_keep_enabled() -> bool:
    """Feature flag for Keep integration.

    Keep API often requires enterprise/admin setup. Keep is disabled by default
    to avoid invalid_scope failures in standard OAuth flows.
    """
    return _env_truthy(os.getenv("ENABLE_KEEP"))


def is_chat_enabled() -> bool:
    """Feature flag for Chat integration.

    Google Chat scopes commonly require a Google Workspace account and are
    disabled by default to avoid invalid_scope failures for consumer accounts.
    """
    return _env_truthy(os.getenv("ENABLE_CHAT"))


def is_apps_dashboard_enabled() -> bool:
    """Feature flag for MCP app-layer dashboard namespace."""
    return _env_truthy(os.getenv("ENABLE_APPS_DASHBOARD"))


def get_google_scopes() -> list[str]:
    scopes = [*GMAIL_SCOPES, *CALENDAR_SCOPES, *DRIVE_SCOPES]
    if is_chat_enabled():
        scopes.extend(CHAT_SCOPES)
    if is_keep_enabled():
        scopes.extend(KEEP_SCOPES)
    return sorted(set(scopes))


def _credentials_paths() -> tuple[Path, Path]:
    # 1. Explicit env-var override (set by host processes like Sentinel)
    env_dir = os.environ.get("MCP_CREDENTIALS_DIR")
    if env_dir:
        d = Path(env_dir)
        creds, tok = d / "credentials.json", d / "token.json"
        if creds.exists():
            return creds, tok

    # 2. Relative to package source tree (__file__-based, works regardless of cwd)
    #    google_auth.py → auth/ → mcp_google_workspace/ → src/ → repo-root/
    _src_dir = Path(__file__).resolve().parent.parent.parent
    for rel in ("credentials", ):
        pkg_creds = _src_dir / rel / "credentials.json"
        pkg_token = _src_dir / rel / "token.json"
        if pkg_creds.exists():
            return pkg_creds, pkg_token

    # 3. Relative to cwd (original behavior, works when cwd is the repo root)
    cwd = Path.cwd()
    credentials = cwd / "credentials.json"
    token = cwd / "token.json"
    if credentials.exists():
        return credentials, token

    alt_credentials = cwd / "src" / "credentials" / "credentials.json"
    alt_token = cwd / "src" / "credentials" / "token.json"
    return alt_credentials, alt_token


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
        # Load token with its original granted scopes, then validate against
        # current required scopes. Passing requested scopes here can mask
        # missing grants and cause runtime insufficientPermissions errors.
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


def build_gmail_service() -> Any:
    return build("gmail", "v1", credentials=get_credentials())


def build_calendar_service() -> Any:
    return build("calendar", "v3", credentials=get_credentials())


def build_keep_service() -> Any:
    return build("keep", "v1", credentials=get_credentials())


def build_chat_service() -> Any:
    return build("chat", "v1", credentials=get_credentials())


def build_drive_service() -> Any:
    return build("drive", "v3", credentials=get_credentials())
