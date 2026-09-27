"""W6: dashboard operation manifest, tool visibility and UI resource metadata."""

from __future__ import annotations

import importlib
import re
from types import SimpleNamespace
from typing import Any

import anyio
import pytest
from fastmcp import Client, FastMCP

import mcp_google_workspace.apps.operations as operations_module
import mcp_google_workspace.apps.tools as apps_tools
import mcp_google_workspace.auth.google_oauth as google_oauth
import mcp_google_workspace.server as server_module
from mcp_google_workspace.apps.operations import DASHBOARD_OPERATIONS, app_callable
from mcp_google_workspace.apps.server import create_apps_server, mount_apps_dashboard
from mcp_google_workspace.apps.view_models import OPERATIONS_META_KEY
from mcp_google_workspace.common.app_state import (
    MemoryAppStateStore,
    reset_default_app_state_store,
)

READS = {
    "getDashboard",
    "getWeeklyCalendar",
    "getEventDetail",
    "getEmailDetail",
    "getEmailAttachment",
    "listCalendars",
}
VIEW_WRITES = {"patchState", "nextRange", "prevRange", "today"}
CALENDAR = {"getWeeklyCalendar", "getEventDetail", "listCalendars", "respondToEvent", "createEvent", "updateEvent", "deleteEvent"}
GMAIL = {"getEmailDetail", "getEmailAttachment", "markEmailRead", "markEmailUnread", "moveEmail", "deleteEmail", "untrashEmail", "markEmailSpam", "markEmailNotSpam"}
ALWAYS = {"getDashboard"} | VIEW_WRITES


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch: pytest.MonkeyPatch):
    async def fake_timezone() -> str:
        return "UTC"

    async def fake_payload(state, ctx) -> dict:
        return {"state": state.model_dump(mode="json")}

    async def fake_weekly(state, *, ctx, date_override=None, include_weekend_override=None) -> dict:
        return {"state": state.model_dump(mode="json")}

    reset_default_app_state_store(MemoryAppStateStore())
    monkeypatch.setattr(apps_tools, "resolve_user_timezone", fake_timezone)
    monkeypatch.setattr(apps_tools, "build_dashboard_payload_with_progress", fake_payload)
    monkeypatch.setattr(apps_tools, "build_weekly_calendar_payload_with_progress", fake_weekly)
    yield
    reset_default_app_state_store(None)


def _remote_principal(monkeypatch: pytest.MonkeyPatch, capabilities: list[str]) -> None:
    monkeypatch.setattr(operations_module, "get_access_token", lambda: SimpleNamespace(token="t"))
    monkeypatch.setattr(
        google_oauth,
        "google_connection_status",
        lambda capability=None: {"granted_capabilities": capabilities},
    )


@pytest.fixture(scope="module")
def workspace() -> FastMCP:
    """The real root composition with the dashboard enabled."""
    mp = pytest.MonkeyPatch()
    for name in ["ENABLE_CHAT", "ENABLE_GEMINI", "ENABLE_KEEP", "ENABLE_MEET"]:
        mp.delenv(name, raising=False)
    mp.setenv("ENABLE_APPS_DASHBOARD", "true")
    root = importlib.reload(server_module).workspace_mcp
    mp.undo()
    yield root
    importlib.reload(server_module)


def _launch(server: FastMCP, tool: str) -> tuple[dict[str, Any], Any]:
    async def run():
        async with Client(server) as client:
            return await client.call_tool(tool, {}, raise_on_error=False)

    result = anyio.run(run)
    assert result.is_error is False, result.content
    return result.meta[OPERATIONS_META_KEY], result


def test_local_principal_gets_every_registered_operation_with_real_tool_names(workspace) -> None:
    manifest, result = _launch(workspace, "apps_get_dashboard")

    assert manifest["version"] == 1
    assert set(manifest["operations"]) == set(DASHBOARD_OPERATIONS)
    names = {op: entry["tool"] for op, entry in manifest["operations"].items()}
    assert names["getDashboard"] == "apps_get_dashboard"
    assert names["patchState"] == "apps_patch_state"
    assert names["createEvent"] == "calendar_create_event"
    assert names["listCalendars"] == "calendar_list_calendars"
    assert names["markEmailSpam"] == "gmail_mark_as_spam"
    for op, entry in manifest["operations"].items():
        assert entry["mutates"] is (op not in READS), op
    # The manifest is UI metadata: never in the model-visible structured content.
    assert OPERATIONS_META_KEY not in (result.structured_content or {})
    # Every advertised tool really exists under that exact name.

    async def resolve():
        return [await workspace.get_tool(entry["tool"]) for entry in manifest["operations"].values()]

    assert all(tool is not None for tool in anyio.run(resolve))


def test_weekly_launch_carries_the_same_manifest(workspace) -> None:
    dashboard, _ = _launch(workspace, "apps_get_dashboard")
    weekly, _ = _launch(workspace, "apps_get_weekly_calendar_view")
    assert weekly == dashboard


@pytest.mark.parametrize(
    ("capabilities", "expected"),
    [
        ([], ALWAYS),
        (["calendar"], ALWAYS | CALENDAR),
        (["gmail"], ALWAYS | GMAIL),
        (["calendar", "gmail", "drive"], ALWAYS | CALENDAR | GMAIL),
    ],
)
def test_remote_manifest_follows_granted_google_scopes(
    workspace, monkeypatch: pytest.MonkeyPatch, capabilities: list[str], expected: set[str]
) -> None:
    _remote_principal(monkeypatch, capabilities)
    manifest, _ = _launch(workspace, "apps_get_dashboard")
    assert set(manifest["operations"]) == expected


def test_remote_manifest_fails_closed_when_grants_cannot_be_read(
    workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(operations_module, "get_access_token", lambda: SimpleNamespace(token="t"))

    def broken(capability=None):
        raise RuntimeError("token store offline")

    monkeypatch.setattr(google_oauth, "google_connection_status", broken)
    manifest, _ = _launch(workspace, "apps_get_dashboard")
    assert set(manifest["operations"]) == ALWAYS


def test_subserver_only_manifest_lists_local_names_and_no_integration_tools() -> None:
    server = create_apps_server()
    manifest, _ = _launch(server, "get_dashboard")
    assert set(manifest["operations"]) == {
        op for op, spec in DASHBOARD_OPERATIONS.items() if spec.namespace == "apps"
    }
    assert manifest["operations"]["nextRange"] == {"tool": "next_range", "mutates": True}
    assert manifest["operations"]["getEventDetail"] == {"tool": "get_event_detail", "mutates": False}


def test_custom_namespace_manifest_uses_the_mount_prefix() -> None:
    root = FastMCP("custom-root")
    mount_apps_dashboard(root, create_apps_server(), namespace="board")
    manifest, _ = _launch(root, "board_get_dashboard")
    assert manifest["operations"]["today"]["tool"] == "board_today"
    assert "createEvent" not in manifest["operations"]  # calendar is not composed here


def test_manifest_sees_tools_hidden_by_progressive_discovery() -> None:
    from fastmcp.server.transforms.search import BM25SearchTransform

    calendar = FastMCP("calendar-stub")

    @calendar.tool(name="create_event")
    def create_event(summary: str) -> str:
        return summary

    root = FastMCP("discovery-root")
    root.mount(calendar, namespace="calendar")
    mount_apps_dashboard(root, create_apps_server(), namespace="apps")
    root.add_transform(BM25SearchTransform(max_results=3, always_visible=["apps_get_dashboard"]))

    async def listed() -> set[str]:
        async with Client(root) as client:
            return {tool.name for tool in await client.list_tools()}

    visible = anyio.run(listed)
    assert "calendar_create_event" not in visible and "apps_next_range" not in visible
    manifest, _ = _launch(root, "apps_get_dashboard")
    assert manifest["operations"]["createEvent"]["tool"] == "calendar_create_event"
    assert manifest["operations"]["nextRange"]["tool"] == "apps_next_range"


def test_operations_the_host_would_reject_are_not_advertised(monkeypatch: pytest.MonkeyPatch) -> None:
    server = create_apps_server()
    original = server.get_tool

    async def model_only_next_range(name, version=None):
        tool = await original(name, version)
        if tool is not None and name == "next_range":
            return tool.model_copy(update={"meta": {"ui": {"visibility": ["model"]}}})
        return tool

    monkeypatch.setattr(server, "get_tool", model_only_next_range)
    manifest, _ = _launch(server, "get_dashboard")
    assert "nextRange" not in manifest["operations"]
    assert "prevRange" in manifest["operations"]


def test_app_callable_follows_the_spec_default() -> None:
    def tool(meta):
        return SimpleNamespace(meta=meta)

    assert app_callable(tool(None)) is True
    assert app_callable(tool({"ui": {"resourceUri": "ui://x"}})) is True
    assert app_callable(tool({"ui": {"visibility": ["app"]}})) is True
    assert app_callable(tool({"ui": {"visibility": ["model", "app"]}})) is True
    assert app_callable(tool({"ui": {"visibility": ["model"]}})) is False
    assert app_callable(tool({"ui": {"visibility": []}})) is False


# --- Visibility ----------------------------------------------------------------

EXPECTED_VISIBILITY = {
    "apps_get_dashboard": ["model", "app"],
    "apps_get_weekly_calendar_view": ["model", "app"],
    "apps_get_state": ["app"],
    "apps_set_state": ["app"],
    "apps_patch_state": ["app"],
    "apps_next_range": ["app"],
    "apps_prev_range": ["app"],
    "apps_today": ["app"],
    "apps_get_event_detail": ["app"],
    "apps_get_email_detail": ["app"],
    "apps_get_email_attachment": ["app"],
    "files_file_manager": ["model"],
    "files_store_files": ["app"],
    "files_delete_file": ["app", "model"],
    "files_list_files": ["model"],
    "files_list_files_page": ["model"],
    "files_read_file": ["model"],
}


def test_every_apps_and_files_tool_declares_its_intended_visibility(workspace) -> None:
    async def listing():
        async with Client(workspace) as client:
            return {tool.name: tool for tool in await client.list_tools()}

    tools = anyio.run(listing)
    declared = {
        name: tool.meta["ui"]["visibility"]
        for name, tool in tools.items()
        if name.startswith(("apps_", "files_"))
    }
    assert declared == EXPECTED_VISIBILITY


def test_app_only_tools_stay_authorized_server_side_when_called_directly(workspace) -> None:
    """Visibility is a host hint: a direct call still needs a valid, owned view handle."""

    async def run():
        async with Client(workspace) as client:
            return await client.call_tool(
                "apps_next_range", {"view_handle": "wsv_" + "x" * 43}, raise_on_error=False
            )

    result = anyio.run(run)
    assert result.is_error is True
    assert result.structured_content["code"] == "view_handle_invalid"


# --- Prefab picker delivery ----------------------------------------------------------


def test_prefab_picker_is_served_bundled_without_external_csp_domains(workspace) -> None:
    async def run():
        async with Client(workspace) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            uri = tools["files_file_manager"].meta["ui"]["resourceUri"]
            resources = {str(item.uri): item for item in await client.list_resources()}
            contents = await client.read_resource(uri)
            diagnostics = await client.call_tool("get_mcp_apps_diagnostics", {})
            return uri, resources, contents, diagnostics

    uri, resources, contents, diagnostics = anyio.run(run)
    meta = resources[uri].meta or {}
    csp = (meta.get("ui") or {}).get("csp") or {}
    assert not any(csp.get(key) for key in ("resourceDomains", "connectDomains", "frameDomains", "baseUriDomains"))
    html = contents[0].text
    # Self-contained: the document's renderer script is inline (the CDN stub is a
    # two-tag document whose <script> has src=...jsdelivr...). The bundled script
    # merely contains that stub as a string literal.
    first_script = re.search(r"<script\b[^>]*>", html)
    assert first_script is not None and "src=" not in first_script.group(0)
    head = html[: first_script.start()]
    assert not re.search(r"<link\b[^>]*href=\"https?://", head)
    assert len(html) > 1_000_000
    assert diagnostics.structured_content["renderer_mode"] == "bundled"


def test_dashboard_ui_resource_declares_no_csp_domains(workspace) -> None:
    async def run():
        async with Client(workspace) as client:
            resources = {str(item.uri): item for item in await client.list_resources()}
            contents = await client.read_resource("ui://apps/dashboard-ui")
            return resources, contents

    resources, contents = anyio.run(run)
    meta = resources["ui://apps/dashboard-ui"].meta or {}
    assert "csp" not in (meta.get("ui") or {})
    html = contents[0].text
    # No external stylesheet/font/script loads in the shipped single file.
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html
    assert "<link" not in html.lower()
