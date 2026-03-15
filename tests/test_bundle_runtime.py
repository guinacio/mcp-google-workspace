from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import mcp_google_workspace.auth.google_auth as google_auth
import mcp_google_workspace.bundle_entry as bundle_entry
import mcp_google_workspace.runtime as runtime_module


ROOT = Path(__file__).resolve().parent.parent


def test_runtime_settings_read_timeout_retry_and_logging(monkeypatch) -> None:
    monkeypatch.setenv("MCP_GOOGLE_HTTP_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("MCP_GOOGLE_HTTP_RETRIES", "4")
    monkeypatch.setenv("MCP_GOOGLE_LOG_LEVEL", "debug")
    monkeypatch.setenv("MCP_GOOGLE_OAUTH_PORT", "8123")
    monkeypatch.setenv("MCP_GOOGLE_OAUTH_OPEN_BROWSER", "false")
    monkeypatch.setenv("ENABLE_GEMINI", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_OUTPUT_DIR", "tmp/custom-gemini")
    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "222")

    settings = runtime_module.get_runtime_settings()

    assert settings.http_timeout_seconds == 45.0
    assert settings.http_retries == 4
    assert settings.log_level == "DEBUG"
    assert settings.oauth_port == 8123
    assert settings.oauth_open_browser is False
    assert settings.gemini_enabled is True
    assert settings.gemini_api_key == "test-key"
    assert settings.gemini_output_dir == "tmp/custom-gemini"
    assert settings.gemini_timeout_seconds == 222.0


def test_runtime_settings_reject_invalid_log_level(monkeypatch) -> None:
    monkeypatch.setenv("MCP_GOOGLE_LOG_LEVEL", "verbose")

    with pytest.raises(ValueError, match="MCP_GOOGLE_LOG_LEVEL"):
        runtime_module.get_runtime_settings()


def test_runtime_settings_require_gemini_api_key_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_GEMINI", "true")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        runtime_module.get_runtime_settings()


def test_bundle_entry_runs_workspace_over_stdio(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        bundle_entry,
        "configure_logging",
        lambda: runtime_module.RuntimeSettings(
            None,
            "gemini-3-flash-preview",
            False,
            "gemini-3.1-flash-image-preview",
            "gemini-3.1-flash-image-preview",
            "tmp/gemini",
            "gemini-3.1-pro-preview",
            180.0,
            "gemini-3-flash-preview",
            2,
            30.0,
            "INFO",
            0,
            True,
        ),
    )
    monkeypatch.setattr(
        bundle_entry.workspace_mcp, "run", lambda transport: calls.append(transport)
    )

    bundle_entry.main()

    assert calls == ["stdio"]


def test_bundle_entry_script_bootstrap_supports_file_execution() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["MCP_GOOGLE_LOG_LEVEL"] = "verbose"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "src" / "mcp_google_workspace" / "bundle_entry.py"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 2
    assert "Invalid MCP Google Workspace runtime configuration" in result.stderr


def test_google_service_builder_uses_runtime_timeout_and_retry(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        google_auth,
        "get_runtime_settings",
        lambda: runtime_module.RuntimeSettings(
            None,
            "gemini-3-flash-preview",
            False,
            "gemini-3.1-flash-image-preview",
            "gemini-3.1-flash-image-preview",
            "tmp/gemini",
            "gemini-3.1-pro-preview",
            180.0,
            "gemini-3-flash-preview",
            3,
            33.0,
            "INFO",
            0,
            True,
        ),
    )
    monkeypatch.setattr(google_auth, "get_credentials", lambda: object())
    monkeypatch.setattr(
        google_auth,
        "_build_authorized_http",
        lambda credentials, settings: "AUTHORIZED_HTTP",
    )

    def fake_build(api_name, version, **kwargs):
        captured["api_name"] = api_name
        captured["version"] = version
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(google_auth, "build", fake_build)

    result = google_auth.build_drive_service()

    assert result == {"ok": True}
    assert captured["api_name"] == "drive"
    assert captured["version"] == "v3"
    assert captured["http"] == "AUTHORIZED_HTTP"
    assert captured["cache_discovery"] is False
    assert captured["num_retries"] == 3
