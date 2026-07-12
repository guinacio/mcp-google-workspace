from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import anyio
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

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
        bundle_entry.workspace_mcp,
        "run",
        lambda transport, **kwargs: calls.append(f"{transport}:{kwargs.get('show_banner')}"),
    )

    bundle_entry.main()

    assert calls == ["stdio:False"]


def test_bundle_exits_cleanly_when_host_closes_stdin() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(ROOT / "src" / "mcp_google_workspace" / "bundle_entry.py")],
        cwd=ROOT,
        input="",
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert time.monotonic() - started < 15


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


async def _call_picker_over_bundle_stdio(tmp_path: Path):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["UV_CACHE_DIR"] = str(tmp_path / "uv-cache")
    transport = StdioTransport(
        command="uv",
        args=["run", "src/mcp_google_workspace/bundle_entry.py"],
        cwd=str(ROOT),
        env=env,
        keep_alive=False,
        log_file=tmp_path / "bundle-stderr.log",
    )
    async with Client(transport) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        picker = next(tool for tool in tools if tool.name == "files_file_manager")
        uri = picker.meta["ui"]["resourceUri"]
        contents = await client.read_resource(uri)
        result = await client.call_tool("files_file_manager", {})
    return names, picker.meta, uri, contents, result


def test_bundle_stdio_lists_and_calls_prefab_file_manager(tmp_path) -> None:
    names, meta, uri, contents, result = anyio.run(
        _call_picker_over_bundle_stdio, tmp_path
    )

    assert "files_file_manager" in names
    assert meta["ui/resourceUri"] == uri
    assert contents[0].mimeType == "text/html;profile=mcp-app"
    assert result.is_error is False


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
    monkeypatch.setattr(google_auth, "get_credentials", lambda scopes=None: object())
    monkeypatch.setattr(
        google_auth,
        "_build_authorized_http",
        lambda credentials, settings, api_name: "AUTHORIZED_HTTP",
    )

    def fake_build(api_name, version, **kwargs):
        captured["api_name"] = api_name
        captured["version"] = version
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(google_auth, "build", fake_build)

    lazy_service = google_auth.build_drive_service()
    result = lazy_service.materialize()

    assert result == {"ok": True}
    assert captured["api_name"] == "drive"
    assert captured["version"] == "v3"
    assert captured["http"] == "AUTHORIZED_HTTP"
    assert captured["cache_discovery"] is False
    assert captured["num_retries"] == 3
