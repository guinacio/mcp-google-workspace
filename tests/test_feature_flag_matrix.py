"""W7b: startup + list + one safe call, over every optional integration flag.

The plan (docs/MIGRATION_FASTMCP4_MCP_2026-07-28.md section 2) requires every
optional-service combination -- Apps, Keep, Chat, Meet, Gemini -- to be
tested independently and together, through both the root composition
(``mcp_google_workspace.server.workspace_mcp``, which mounts each namespace
only when its ``ENABLE_*`` flag is set) and a direct client to the
namespace's own subserver (``apps_mcp``, ``chat_mcp``, ``keep_mcp``,
``meet_mcp``, ``gemini_mcp``), which registers its tools unconditionally --
mounting is what the flag gates, not tool registration (see
``tests/test_composition.py``).

Every call here is mocked: no live Google/Gemini network access, matching
the rest of the suite (``tests/test_http_wire.py``'s ``_GoogleRecorder``,
``tests/test_gemini_tools.py``'s fake client). This is a composition/wiring
smoke test, not a business-logic test -- each namespace's own test module
already covers its read/mutation/error behavior in depth.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastmcp import Client, FastMCP

import mcp_google_workspace.apps.tools as apps_tools
import mcp_google_workspace.auth.google_auth as google_auth
import mcp_google_workspace.gemini.tools as gemini_tools
import mcp_google_workspace.keep.tools as keep_tools
import mcp_google_workspace.meet.tools as meet_tools
import mcp_google_workspace.server as server_module
from mcp_google_workspace.apps.server import apps_mcp
from mcp_google_workspace.chat.server import chat_mcp
from mcp_google_workspace.gemini.server import gemini_mcp
from mcp_google_workspace.keep.server import keep_mcp
from mcp_google_workspace.meet.server import meet_mcp

_FLAG_NAMES = ["ENABLE_APPS_DASHBOARD", "ENABLE_CHAT", "ENABLE_GEMINI", "ENABLE_KEEP", "ENABLE_MEET"]


class _GoogleRecorder:
    """No-network stand-in for every ``google_auth._build_service_now`` call."""

    def __init__(self, calls: list[str], path: tuple[str, ...] = ()) -> None:
        self._calls = calls
        self._path = path

    def __getattr__(self, name: str) -> "_GoogleRecorder":
        if name.startswith("__"):
            raise AttributeError(name)
        return _GoogleRecorder(self._calls, (*self._path, name))

    def __call__(self, *_args: Any, **_kwargs: Any) -> "_GoogleRecorder":
        return self

    def execute(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self._calls.append(".".join(self._path))
        return {}


class _FakeGeminiClient:
    def resolve_model(self, capability: str, override: str | None = None) -> str:
        return override or f"default-{capability}"

    def generate_image(self, *, prompt: str, model: str, aspect_ratio: str | None = None) -> dict[str, Any]:
        assert prompt
        return {"image_bytes": b"png-bytes", "mime_type": "image/png", "model": model, "model_version": model}


async def _fake_timezone() -> str:
    return "UTC"


async def _fake_dashboard(state: Any, ctx: Any) -> dict[str, Any]:
    return {"state": state.model_dump(mode="json")}


@pytest.fixture
def patched_providers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """Mock every optional integration's outbound call, uniformly.

    Google Workspace APIs all funnel through ``google_auth._build_service_now``
    (see ``auth/google_auth.py``'s ``LazyGoogleRequest``); Calendar-timezone
    resolution (used by Apps/Keep/Meet) and the dashboard payload builder are
    patched the same way ``tests/test_http_replicas.py``'s ``fleet`` fixture
    does; Gemini goes through its own ``GeminiMediaClient``, matching
    ``tests/test_gemini_tools.py``.
    """
    calls: list[str] = []
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _GoogleRecorder(calls))
    monkeypatch.setattr(apps_tools, "resolve_user_timezone", _fake_timezone)
    monkeypatch.setattr(apps_tools, "build_dashboard_payload_with_progress", _fake_dashboard)
    monkeypatch.setattr(keep_tools, "resolve_user_timezone", _fake_timezone)
    monkeypatch.setattr(meet_tools, "resolve_user_timezone", _fake_timezone)
    monkeypatch.setattr(gemini_tools, "GeminiMediaClient", _FakeGeminiClient)
    monkeypatch.setenv("GEMINI_OUTPUT_DIR", str(tmp_path))
    return calls


# (flag, marker_tool, arguments) -- one safe (read-or-local, fully mocked) call
# per optional integration.
_SAFE_CALLS: list[tuple[str, str, dict[str, Any]]] = [
    ("ENABLE_APPS_DASHBOARD", "get_dashboard", {}),
    ("ENABLE_CHAT", "list_spaces", {"request": {}}),
    ("ENABLE_GEMINI", "generate_image", {"prompt": "a cat"}),
    ("ENABLE_KEEP", "list_notes", {"request": {}}),
    ("ENABLE_MEET", "list_conference_records", {}),
]
_ROOT_TOOL_PREFIX = {
    "ENABLE_APPS_DASHBOARD": "apps_",
    "ENABLE_CHAT": "chat_",
    "ENABLE_GEMINI": "gemini_",
    "ENABLE_KEEP": "keep_",
    "ENABLE_MEET": "meet_",
}


def _reload_workspace(**flags: str) -> FastMCP:
    for name in _FLAG_NAMES:
        os.environ.pop(name, None)
    os.environ.pop("GEMINI_API_KEY", None)
    for name, value in flags.items():
        os.environ[name] = value
    return importlib.reload(server_module).workspace_mcp


@pytest.fixture(autouse=True)
def _restore_server_module() -> Iterator[None]:
    """Every test reloads server.py with its own flags; restore the default after."""
    try:
        yield
    finally:
        for name in [*_FLAG_NAMES, "GEMINI_API_KEY"]:
            os.environ.pop(name, None)
        importlib.reload(server_module)


async def _startup_list_and_call(
    server: FastMCP, tool: str, arguments: dict[str, Any]
) -> tuple[list[str], bool, Any]:
    async with Client(server) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    return names, result.is_error, result.structured_content


@pytest.mark.parametrize(("flag", "tool", "arguments"), _SAFE_CALLS, ids=[flag for flag, _, _ in _SAFE_CALLS])
def test_root_composition_with_one_flag_enabled(
    patched_providers: list[str], flag: str, tool: str, arguments: dict[str, Any]
) -> None:
    kwargs = {flag: "true"}
    if flag == "ENABLE_GEMINI":
        kwargs["GEMINI_API_KEY"] = "test-key"
    workspace = _reload_workspace(**kwargs)

    prefix = _ROOT_TOOL_PREFIX[flag]
    root_tool = f"{prefix}{tool}"
    names, is_error, structured = anyio.run(_startup_list_and_call, workspace, root_tool, arguments)

    assert root_tool in names
    # Every *other* optional namespace stays unmounted.
    for other_flag, other_prefix in _ROOT_TOOL_PREFIX.items():
        if other_flag != flag:
            assert not any(name.startswith(other_prefix) for name in names), (other_flag, names)
    assert is_error is False, structured


def test_root_composition_with_every_optional_flag_enabled(patched_providers: list[str]) -> None:
    workspace = _reload_workspace(
        ENABLE_APPS_DASHBOARD="true",
        ENABLE_CHAT="true",
        ENABLE_GEMINI="true",
        GEMINI_API_KEY="test-key",
        ENABLE_KEEP="true",
        ENABLE_MEET="true",
    )

    async def scenario() -> list[tuple[str, bool, Any]]:
        results = []
        async with Client(workspace) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
            for flag, tool, arguments in _SAFE_CALLS:
                root_tool = f"{_ROOT_TOOL_PREFIX[flag]}{tool}"
                assert root_tool in names, (root_tool, sorted(names))
                result = await client.call_tool(root_tool, arguments, raise_on_error=False)
                results.append((root_tool, result.is_error, result.structured_content))
        return results

    for root_tool, is_error, structured in anyio.run(scenario):
        assert is_error is False, (root_tool, structured)


_SUBSERVERS: dict[str, FastMCP] = {
    "ENABLE_APPS_DASHBOARD": apps_mcp,
    "ENABLE_CHAT": chat_mcp,
    "ENABLE_GEMINI": gemini_mcp,
    "ENABLE_KEEP": keep_mcp,
    "ENABLE_MEET": meet_mcp,
}


@pytest.mark.parametrize(("flag", "tool", "arguments"), _SAFE_CALLS, ids=[flag for flag, _, _ in _SAFE_CALLS])
def test_direct_subserver_client_startup_list_and_safe_call(
    patched_providers: list[str], flag: str, tool: str, arguments: dict[str, Any]
) -> None:
    """A client connected straight to the namespace's own subserver.

    Subserver modules register their tools unconditionally at import time
    (``ENABLE_*`` only gates *mounting* onto ``workspace_mcp`` in
    ``server.py``), so this needs no reload/flag and exercises every
    subserver regardless of which flags are set elsewhere in the suite.
    """
    server = _SUBSERVERS[flag]
    names, is_error, structured = anyio.run(_startup_list_and_call, server, tool, arguments)

    assert tool in names
    assert is_error is False, structured
