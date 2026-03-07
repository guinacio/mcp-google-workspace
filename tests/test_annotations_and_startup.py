from __future__ import annotations

import anyio
from fastmcp import Client

import mcp_google_workspace.auth.google_auth as google_auth
from mcp_google_workspace.apps.server import apps_mcp
from mcp_google_workspace.server import workspace_mcp


async def _list_workspace_tools_via_client():
    async with Client(workspace_mcp) as client:
        tools = await client.list_tools()
    return {tool.name: tool for tool in tools}


async def _list_server_tools(server):
    tools = await server.list_tools(run_middleware=False)
    return {tool.name: tool for tool in tools}


def _meta(tool) -> dict | None:
    annotations = tool.annotations
    assert annotations is not None
    return annotations._meta

def test_workspace_tool_catalog_has_strong_metadata() -> None:
    tools = anyio.run(_list_server_tools, workspace_mcp)

    missing_descriptions: list[str] = []
    weak_titles: list[str] = []
    weak_tags: list[str] = []
    missing_param_descriptions: list[str] = []
    missing_required_arrays: list[str] = []

    for tool in tools.values():
        namespace = tool.name.split("_", 1)[0] if "_" in tool.name else ""
        if not (tool.description or "").strip():
            missing_descriptions.append(tool.name)
        if namespace and not (tool.title or "").lower().startswith(namespace.lower()):
            weak_titles.append(tool.name)
        if namespace and namespace not in set(tool.tags or []):
            weak_tags.append(tool.name)
        params = tool.parameters or {}
        properties = params.get("properties", {}) if isinstance(params, dict) else {}
        if properties and "required" not in params:
            missing_required_arrays.append(tool.name)
        for param_name, schema in properties.items():
            if not isinstance(schema, dict) or not (schema.get("description") or "").strip():
                missing_param_descriptions.append(f"{tool.name}:{param_name}")

    assert missing_descriptions == []
    assert weak_titles == []
    assert weak_tags == []
    assert missing_param_descriptions == []
    assert missing_required_arrays == []


def test_workspace_startup_does_not_fetch_google_credentials(monkeypatch) -> None:
    def fail_get_credentials():
        raise AssertionError("get_credentials should not be called during startup")

    monkeypatch.setattr(google_auth, "get_credentials", fail_get_credentials)
    tools = anyio.run(_list_workspace_tools_via_client)

    assert "gmail_send_email" in tools
    assert "calendar_get_events" in tools
    assert "drive_list_files" in tools
    assert "sheets_get_spreadsheet" in tools
    assert "docs_get_document" in tools


def test_workspace_tools_include_safety_annotations() -> None:
    tools = anyio.run(_list_server_tools, workspace_mcp)

    assert tools
    assert all(tool.annotations is not None for tool in tools.values())
    assert tools["gmail_read_email"].annotations.readOnlyHint is True
    assert tools["gmail_send_email"].annotations.readOnlyHint is False
    assert tools["gmail_send_email"].annotations.idempotentHint is False
    assert tools["drive_delete_file"].annotations.destructiveHint is True
    assert tools["calendar_get_current_date"].annotations.openWorldHint is False
    assert tools["sheets_get_spreadsheet"].annotations.readOnlyHint is True
    assert tools["tasks_delete_task"].annotations.destructiveHint is True
    assert tools["drive_list_files"].title == "Drive List Files"
    assert "drive" in tools["drive_list_files"].tags
    assert "browse" in tools["drive_list_files"].tags


def test_apps_tools_preserve_ui_metadata_and_local_hints() -> None:
    tools = anyio.run(_list_server_tools, apps_mcp)

    assert all(tool.annotations is not None for tool in tools.values())
    assert tools["get_dashboard"].annotations.readOnlyHint is True
    assert tools["set_state"].annotations.openWorldHint is False
    assert tools["set_state"].annotations.idempotentHint is True
    assert tools["cancel_meeting"].annotations.destructiveHint is True
    assert _meta(tools["get_dashboard"]) == {"ui": {"resourceUri": "ui://apps/dashboard-ui"}}
