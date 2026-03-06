import importlib

import anyio

import mcp_google_workspace.server as server_module


async def _list_tool_names(server):
    tools = await server.list_tools(run_middleware=False)
    return [tool.name for tool in tools]


def _reload_workspace(monkeypatch, **flags):
    for name in ["ENABLE_APPS_DASHBOARD", "ENABLE_CHAT", "ENABLE_KEEP", "ENABLE_MEET"]:
        monkeypatch.delenv(name, raising=False)
    for name, value in flags.items():
        monkeypatch.setenv(name, value)
    return importlib.reload(server_module).workspace_mcp


def test_composition_mounts_default_namespaces(monkeypatch):
    workspace = _reload_workspace(monkeypatch)
    tool_names = anyio.run(_list_tool_names, workspace)

    assert "gmail_send_email" in tool_names
    assert "calendar_get_events" in tool_names
    assert "drive_list_files" in tool_names
    assert "sheets_get_spreadsheet" in tool_names
    assert "docs_get_document" in tool_names
    assert "tasks_list_tasklists" in tool_names
    assert "people_list_contacts" in tool_names
    assert "forms_get_form" in tool_names
    assert "slides_get_presentation" in tool_names
    assert "apps_get_dashboard" not in tool_names
    assert "chat_create_message" not in tool_names
    assert "keep_create_note" not in tool_names
    assert "meet_get_space" not in tool_names


def test_composition_mounts_optional_namespaces_when_enabled(monkeypatch):
    workspace = _reload_workspace(
        monkeypatch,
        ENABLE_APPS_DASHBOARD="true",
        ENABLE_CHAT="true",
        ENABLE_KEEP="true",
        ENABLE_MEET="true",
    )
    tool_names = anyio.run(_list_tool_names, workspace)

    assert "apps_get_dashboard" in tool_names
    assert "apps_get_weekly_calendar_view" in tool_names
    assert "chat_create_message" in tool_names
    assert "keep_create_note" in tool_names
    assert "meet_get_space" in tool_names
