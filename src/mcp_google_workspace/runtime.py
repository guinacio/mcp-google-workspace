"""Runtime configuration helpers for local MCP bundle execution."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .common.crypto import FernetKeyring


_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    gemini_api_key: str | None
    gemini_audio_understanding_model: str
    gemini_enabled: bool
    gemini_image_edit_model: str
    gemini_image_generate_model: str
    gemini_output_dir: str
    gemini_reasoning_model: str
    gemini_timeout_seconds: float
    gemini_video_understanding_model: str
    http_retries: int
    http_timeout_seconds: float
    log_level: str
    oauth_port: int
    oauth_open_browser: bool


@dataclass(frozen=True, slots=True)
class RemoteSecuritySettings:
    """Required configuration for authenticated multi-user Streamable HTTP."""

    base_url: str
    google_oauth_redirect_url: str
    jwt_audience: str
    jwt_issuer: str
    jwt_jwks_uri: str
    token_encryption_key: str
    user_token_dir: Path


@dataclass(frozen=True, slots=True)
class TokenStorageSettings:
    """Encrypted credential storage shared by local and remote deployments."""

    token_encryption_key: str
    user_token_dir: Path
    keyring: FernetKeyring


def _env_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, received {raw!r}.") from exc
    if value < minimum or value > maximum:
        raise ValueError(
            f"{name} must be between {minimum} and {maximum}, received {value}."
        )
    return value


def _parse_float_env(
    name: str, default: float, *, minimum: float, maximum: float
) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, received {raw!r}.") from exc
    if value < minimum or value > maximum:
        raise ValueError(
            f"{name} must be between {minimum} and {maximum}, received {value}."
        )
    return value


def _parse_str_env(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    return value or default


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required for authenticated remote HTTP mode.")
    return value


def _require_secure_url(name: str, value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError(f"{name} must be an absolute HTTP(S) URL.")
    is_loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not is_loopback:
        raise ValueError(f"{name} must use HTTPS outside a loopback development host.")
    return value.rstrip("/")


def get_remote_security_settings() -> RemoteSecuritySettings:
    """Load the non-optional security contract for remote HTTP hosting."""
    base_url = _require_secure_url("MCP_HTTP_BASE_URL", _required_env("MCP_HTTP_BASE_URL"))
    redirect_url = _require_secure_url(
        "MCP_GOOGLE_OAUTH_REDIRECT_URL",
        _required_env("MCP_GOOGLE_OAUTH_REDIRECT_URL"),
    )
    if not redirect_url.startswith(f"{base_url}/"):
        raise ValueError("MCP_GOOGLE_OAUTH_REDIRECT_URL must be served below MCP_HTTP_BASE_URL.")
    storage = get_token_storage_settings()
    return RemoteSecuritySettings(
        base_url=base_url,
        google_oauth_redirect_url=redirect_url,
        jwt_audience=_required_env("MCP_HTTP_JWT_AUDIENCE"),
        jwt_issuer=_required_env("MCP_HTTP_JWT_ISSUER"),
        jwt_jwks_uri=_require_secure_url("MCP_HTTP_JWKS_URI", _required_env("MCP_HTTP_JWKS_URI")),
        token_encryption_key=storage.token_encryption_key or "keyring-configured",
        user_token_dir=storage.user_token_dir,
    )


def get_token_storage_settings() -> TokenStorageSettings:
    """Load encrypted per-user token storage without requiring a remote deployment."""
    raw_dir = os.getenv("MCP_USER_TOKEN_DIR", "").strip()
    if not raw_dir:
        credentials_dir = os.getenv("MCP_CREDENTIALS_DIR", "").strip()
        if not credentials_dir:
            raise ValueError("MCP_USER_TOKEN_DIR is required when MCP_CREDENTIALS_DIR is not configured.")
        raw_dir = str(Path(credentials_dir) / "workspace-user-tokens")
    keyring = FernetKeyring.from_environment()
    legacy_key = os.getenv("MCP_TOKEN_ENCRYPTION_KEY", "").strip()
    return TokenStorageSettings(
        token_encryption_key=legacy_key,
        user_token_dir=Path(raw_dir).expanduser().resolve(),
        keyring=keyring,
    )


def get_runtime_settings() -> RuntimeSettings:
    log_level = os.getenv("MCP_GOOGLE_LOG_LEVEL", "INFO").strip().upper() or "INFO"
    if log_level not in _LOG_LEVELS:
        raise ValueError(
            "MCP_GOOGLE_LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, or CRITICAL."
        )
    gemini_enabled = _env_truthy(os.getenv("ENABLE_GEMINI"))
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if gemini_api_key is not None:
        gemini_api_key = gemini_api_key.strip() or None
    if gemini_enabled and not gemini_api_key:
        raise ValueError(
            "ENABLE_GEMINI requires GEMINI_API_KEY to be configured."
        )
    return RuntimeSettings(
        gemini_api_key=gemini_api_key,
        gemini_audio_understanding_model=_parse_str_env(
            "GEMINI_AUDIO_UNDERSTANDING_MODEL",
            "gemini-3-flash-preview",
        ),
        gemini_enabled=gemini_enabled,
        gemini_image_edit_model=_parse_str_env(
            "GEMINI_IMAGE_EDIT_MODEL",
            "gemini-3.1-flash-image-preview",
        ),
        gemini_image_generate_model=_parse_str_env(
            "GEMINI_IMAGE_GENERATE_MODEL",
            "gemini-3.1-flash-image-preview",
        ),
        gemini_output_dir=_parse_str_env("GEMINI_OUTPUT_DIR", "tmp/gemini"),
        gemini_reasoning_model=_parse_str_env(
            "GEMINI_REASONING_MODEL",
            "gemini-3.1-pro-preview",
        ),
        gemini_timeout_seconds=_parse_float_env(
            "GEMINI_TIMEOUT_SECONDS",
            180.0,
            minimum=5.0,
            maximum=600.0,
        ),
        gemini_video_understanding_model=_parse_str_env(
            "GEMINI_VIDEO_UNDERSTANDING_MODEL",
            "gemini-3-flash-preview",
        ),
        http_retries=_parse_int_env(
            "MCP_GOOGLE_HTTP_RETRIES", 2, minimum=0, maximum=10
        ),
        http_timeout_seconds=_parse_float_env(
            "MCP_GOOGLE_HTTP_TIMEOUT_SECONDS",
            120.0,
            minimum=5.0,
            maximum=600.0,
        ),
        log_level=log_level,
        oauth_port=_parse_int_env("MCP_GOOGLE_OAUTH_PORT", 0, minimum=0, maximum=65535),
        oauth_open_browser=_env_truthy(
            os.getenv("MCP_GOOGLE_OAUTH_OPEN_BROWSER", "true")
        ),
    )


def configure_logging() -> RuntimeSettings:
    settings = get_runtime_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
    return settings
