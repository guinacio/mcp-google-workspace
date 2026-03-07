"""Runtime configuration helpers for local MCP bundle execution."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass


_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    http_retries: int
    http_timeout_seconds: float
    log_level: str
    oauth_port: int
    oauth_open_browser: bool


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


def get_runtime_settings() -> RuntimeSettings:
    log_level = os.getenv("MCP_GOOGLE_LOG_LEVEL", "INFO").strip().upper() or "INFO"
    if log_level not in _LOG_LEVELS:
        raise ValueError(
            "MCP_GOOGLE_LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, or CRITICAL."
        )
    return RuntimeSettings(
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
