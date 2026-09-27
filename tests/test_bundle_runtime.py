from __future__ import annotations

import base64
import os
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastmcp.server.providers.addressing import hash_tool
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
    monkeypatch.setattr(bundle_entry, "configure_tool_search", lambda _server: False)

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
    # --no-sync/--frozen keep this subprocess from re-resolving or syncing the
    # shared project .venv (uv otherwise performs a package-check/sync pass on
    # every `uv run`, mutating the same environment the outer pytest process
    # is running from). The environment is already synced by the test runner,
    # so this only *runs* the bundle entrypoint without touching packages.
    transport = StdioTransport(
        command="uv",
        args=[
            "run",
            "--no-sync",
            "--frozen",
            "src/mcp_google_workspace/bundle_entry.py",
        ],
        cwd=str(ROOT),
        env=env,
        keep_alive=False,
        log_file=tmp_path / "bundle-stderr.log",
    )
    async with Client(transport) as client:
        # The stdio bundle serves MCP 2026-07-28 without an initialize handshake.
        assert client.protocol_version == "2026-07-28"
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        picker = next(tool for tool in tools if tool.name == "files_file_manager")
        uri = picker.meta["ui"]["resourceUri"]
        contents = await client.read_resource(uri)
        result = await client.call_tool("files_file_manager", {})
        action_tool = f"{hash_tool('Workspace Files', 'store_files')}_store_files"
        backend = await client.call_tool(action_tool, {"files": []})
        diagnostics = await client.call_tool("get_mcp_apps_diagnostics", {})
    return names, picker.meta, uri, contents, result, action_tool, backend, diagnostics


def test_bundle_stdio_lists_and_calls_prefab_file_manager(tmp_path) -> None:
    names, meta, uri, contents, result, action_tool, backend, diagnostics = anyio.run(
        _call_picker_over_bundle_stdio, tmp_path
    )

    assert "files_file_manager" in names
    assert len(names) <= 16
    assert {"search_tools", "call_tool"} <= names
    assert meta["ui"]["resourceUri"] == uri
    assert "ui/resourceUri" not in meta  # W6: flat alias removed
    assert contents[0].mime_type == "text/html;profile=mcp-app"
    assert result.is_error is False
    assert action_tool in json.dumps(result.structured_content)
    assert backend.is_error is False
    assert diagnostics.structured_content["hidden_callbacks"]["store_files"] == action_tool


def _bundle_transport(tmp_path: Path) -> StdioTransport:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["UV_CACHE_DIR"] = str(tmp_path / "uv-cache")
    return StdioTransport(
        command="uv",
        args=["run", "--no-sync", "--frozen", "src/mcp_google_workspace/bundle_entry.py"],
        cwd=str(ROOT),
        env=env,
        keep_alive=False,
        log_file=tmp_path / "bundle-stderr.log",
    )


async def _upload_lifecycle_over_bundle_stdio(tmp_path: Path):
    store_tool = f"{hash_tool('Workspace Files', 'store_files')}_store_files"
    delete_tool = f"{hash_tool('Workspace Files', 'delete_file')}_delete_file"
    content = b"bundle upload persists"
    async with Client(_bundle_transport(tmp_path)) as client:
        assert client.protocol_version == "2026-07-28"
        # Request 1: store. Every later call is its own MCP 2026-07-28 request
        # with a fresh server-side session; nothing may rely on connection scope.
        stored = await client.call_tool(
            store_tool,
            {
                "files": [
                    {
                        "name": "bundle.txt",
                        "size": len(content),
                        "type": "text/plain",
                        "data": base64.b64encode(content).decode("ascii"),
                    }
                ]
            },
        )
        upload_id = stored.structured_content["result"][0]["upload_id"]
        listed = await client.call_tool("files_list_files", {})
        page = await client.call_tool("files_list_files_page", {"limit": 10})
        read = await client.call_tool(
            "call_tool", {"name": "files_read_file", "arguments": {"name": upload_id}}
        )
        deleted = await client.call_tool(delete_tool, {"name": upload_id})
        after = await client.call_tool("files_list_files", {})
    return upload_id, listed, page, read, deleted, after


def test_bundle_stdio_uploads_persist_across_requests(tmp_path) -> None:
    upload_id, listed, page, read, deleted, after = anyio.run(
        _upload_lifecycle_over_bundle_stdio, tmp_path
    )

    assert upload_id.startswith("upl_")
    assert [item["upload_id"] for item in listed.structured_content["result"]] == [upload_id]
    assert listed.structured_content["result"][0]["display_name"] == "bundle.txt"
    assert page.structured_content["count"] == 1
    assert "bundle upload persists" in json.dumps(read.structured_content)
    assert deleted.structured_content == {"status": "deleted", "name": upload_id}
    assert after.structured_content["result"] == []


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
