"""MCP Tasks extension bootstrap (FastMCP 4 / fastmcp-tasks)."""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from typing import Any

import anyio
import pytest
from fastmcp import Client, FastMCP
from fastmcp_tasks import TasksExtension, call_tool_task

import mcp_google_workspace.auth.google_auth as google_auth
import mcp_google_workspace.server as server_module
from mcp_google_workspace import tool_discovery
from mcp_google_workspace.common.fastmcp_compat import registered_extensions
from mcp_google_workspace.common.task_backend import (
    DEFAULT_TASK_QUEUE_NAME,
    TASKS_EXTENSION_ID,
    build_tasks_extension,
    install_tasks_extension,
    resolve_task_backend_config,
    server_tasks_extension,
)

_DEFAULT_TASK_TOOLS = {
    "calendar_download_event_attachment",
    "docs_batch_update_document",
    "drive_download_file",
    "drive_export_google_file",
    "drive_upload_file",
    "forms_batch_update_form",
    "gmail_download_attachment",
    "sheets_batch_update_spreadsheet",
    "slides_batch_update_presentation",
}
_GEMINI_TASK_TOOLS = {
    "gemini_analyze_audio",
    "gemini_describe_video",
    "gemini_edit_image",
    "gemini_generate_image",
}
_TRACKED_ENV = (
    "ENABLE_APPS_DASHBOARD",
    "ENABLE_CHAT",
    "ENABLE_GEMINI",
    "ENABLE_KEEP",
    "ENABLE_MEET",
    "GEMINI_API_KEY",
    "MCP_TOOL_SEARCH",
    "MCP_CLIENT_MODEL",
)


@pytest.fixture
def backend_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in (
        "FASTMCP_DOCKET_URL",
        "FASTMCP_DOCKET_NAME",
        "FASTMCP_TASKS_ENCRYPTION_KEY",
        "MCP_REDIS_URL",
        "MCP_RUNTIME_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    # DocketSettings also reads FASTMCP_ENV_FILE (default ".env"); point it at
    # a file that does not exist so a developer's local .env cannot leak in.
    monkeypatch.setenv("FASTMCP_ENV_FILE", os.devnull)
    return monkeypatch


def test_default_backend_is_in_memory_and_needs_no_redis(backend_env) -> None:
    config = resolve_task_backend_config()
    assert config.url == "memory://"
    assert config.url_source == "default"
    assert config.name == DEFAULT_TASK_QUEUE_NAME
    assert config.distributed is False
    assert config.snapshot_encryption is False


def test_mcp_redis_url_selects_the_shared_queue(backend_env) -> None:
    backend_env.setenv("MCP_REDIS_URL", "redis://redis.internal:6379/0")
    backend_env.setenv("FASTMCP_TASKS_ENCRYPTION_KEY", "k" * 32)
    config = resolve_task_backend_config()
    assert (config.url, config.url_source) == ("redis://redis.internal:6379/0", "MCP_REDIS_URL")
    assert config.snapshot_encryption is True
    extension = build_tasks_extension(config)
    assert extension.docket_settings.url == "redis://redis.internal:6379/0"
    assert extension.docket_settings.name == DEFAULT_TASK_QUEUE_NAME
    # Diagnostics never include the URL (it may embed credentials).
    assert "redis.internal" not in repr(config.diagnostics())


def test_explicit_fastmcp_docket_settings_take_precedence(backend_env) -> None:
    backend_env.setenv("MCP_REDIS_URL", "redis://app:6379/0")
    backend_env.setenv("FASTMCP_DOCKET_URL", "redis://docket:6379/1")
    backend_env.setenv("FASTMCP_DOCKET_NAME", "custom-queue")
    config = resolve_task_backend_config()
    assert (config.url, config.url_source, config.name) == (
        "redis://docket:6379/1",
        "FASTMCP_DOCKET_URL",
        "custom-queue",
    )


def test_stdio_bundle_never_joins_a_shared_queue_implicitly(backend_env) -> None:
    backend_env.setenv("MCP_REDIS_URL", "redis://redis.internal:6379/0")
    backend_env.setenv("MCP_RUNTIME_MODE", "bundle")
    assert resolve_task_backend_config().url == "memory://"


def test_install_is_idempotent_per_server(backend_env) -> None:
    server = FastMCP("idempotent-install")
    first = install_tasks_extension(server)
    assert install_tasks_extension(server) is first
    assert server_tasks_extension(server) is first
    assert list(registered_extensions(server)) == [TASKS_EXTENSION_ID]


def test_root_composition_registers_exactly_one_tasks_extension() -> None:
    extension = server_tasks_extension(server_module.workspace_mcp)
    assert isinstance(extension, TasksExtension)
    assert list(registered_extensions(server_module.workspace_mcp)) == [TASKS_EXTENSION_ID]


def _reload(env: dict[str, str]) -> FastMCP:
    for name in _TRACKED_ENV:
        os.environ.pop(name, None)
    os.environ.update(env)
    return importlib.reload(server_module).workspace_mcp


@pytest.fixture
def restore_workspace() -> Iterator[None]:
    previous = {name: os.environ.get(name) for name in _TRACKED_ENV}
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        tool_discovery._CONFIGURED_SERVERS.clear()
        importlib.reload(server_module)


async def _task_capable_tools(server: FastMCP) -> tuple[set[str], set[str], dict[str, Any]]:
    declared = {
        tool.name
        for tool in await server.list_tools(run_middleware=False)
        if tool.task_config.supports_tasks()
    }
    async with Client(server) as modern:
        assert modern.protocol_version == "2026-07-28"
        capabilities = modern.server_capabilities
    extensions = dict(capabilities.extensions or {}) if capabilities is not None else {}
    # 2026-07-28 drops Tool.execution from the wire (tasks are negotiated via
    # the extension); handshake-era listings still carry execution.taskSupport.
    async with Client(server, mode="legacy") as legacy:
        legacy_tools = await legacy.list_tools()
    advertised = {
        tool.name
        for tool in legacy_tools
        if tool.execution is not None and tool.execution.task_support == "optional"
    }
    return declared, advertised, extensions


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, _DEFAULT_TASK_TOOLS),
        ({"ENABLE_GEMINI": "true", "GEMINI_API_KEY": "test-key"}, _DEFAULT_TASK_TOOLS | _GEMINI_TASK_TOOLS),
    ],
    ids=["default", "gemini"],
)
def test_root_negotiates_task_capable_tools(
    restore_workspace: None, env: dict[str, str], expected: set[str]
) -> None:
    server = _reload(env)
    declared, advertised, extensions = anyio.run(_task_capable_tools, server)
    assert declared == expected
    assert advertised == expected
    assert len(declared) == (9 if not env else 13)
    assert TASKS_EXTENSION_ID in extensions


class _SheetsRecorder:
    def __init__(self, calls: list[str], path: tuple[str, ...] = ()) -> None:
        self._calls = calls
        self._path = path

    def __getattr__(self, name: str) -> "_SheetsRecorder":
        if name.startswith("__"):
            raise AttributeError(name)
        return _SheetsRecorder(self._calls, (*self._path, name))

    def __call__(self, *_args: Any, **_kwargs: Any) -> "_SheetsRecorder":
        return self

    def execute(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self._calls.append(".".join(self._path))
        return {"spreadsheetId": "s1", "replies": [{}]}


_SHEETS_ARGS = {
    "spreadsheet_id": "s1",
    "requests": [{"addSheet": {"properties": {"title": "New tab"}}}],
}


def test_task_tool_runs_as_background_task_on_the_root_queue(
    restore_workspace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        google_auth, "_build_service_now", lambda *_a, **_k: _SheetsRecorder(calls)
    )
    server = _reload({})

    async def scenario() -> tuple[str, Any]:
        async with Client(server) as client:
            task = await call_tool_task(client, "sheets_batch_update_spreadsheet", _SHEETS_ARGS)
            result = await task.result()
        return task.task_id, result

    task_id, result = anyio.run(scenario)
    assert task_id
    assert result.is_error is False
    assert calls == ["spreadsheets.batchUpdate"]


def test_search_discovered_task_tool_runs_through_the_call_proxy(
    restore_workspace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        google_auth, "_build_service_now", lambda *_a, **_k: _SheetsRecorder(calls)
    )
    server = _reload({"MCP_TOOL_SEARCH": "on"})
    tool_discovery._CONFIGURED_SERVERS.clear()
    assert tool_discovery.configure_tool_search(server)

    async def scenario() -> tuple[set[str], str, Any]:
        async with Client(server) as client:
            visible = {tool.name for tool in await client.list_tools()}
            matches = await client.call_tool(
                "search_tools", {"query": "sheets batch update spreadsheet"}
            )
            # FastMCP 4.0.10 runs a task tool invoked by another tool (the
            # search proxy) in the foreground, returning the result inline.
            called = await client.call_tool(
                "call_tool",
                {"name": "sheets_batch_update_spreadsheet", "arguments": _SHEETS_ARGS},
            )
        return visible, matches.content[0].text, called

    visible, matches_text, called = anyio.run(scenario)
    assert "sheets_batch_update_spreadsheet" not in visible
    assert {"search_tools", "call_tool"} <= visible
    assert "sheets_batch_update_spreadsheet" in matches_text
    assert called.is_error is False
    assert calls == ["spreadsheets.batchUpdate"]
