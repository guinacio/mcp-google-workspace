import anyio
from fastmcp import Client

from mcp_google_workspace.apps.server import apps_mcp
from mcp_google_workspace.server import workspace_mcp


async def _client_list_tool_names(server):
    async with Client(server) as client:
        tools = await client.list_tools()
        return [tool.name for tool in tools]


async def _get_tool(server, name):
    tools = await server.list_tools(run_middleware=False)
    return next(tool for tool in tools if tool.name == name)


def test_workspace_list_tools_does_not_require_credentials(monkeypatch):
    def _raise():
        raise AssertionError("credentials should not be loaded during discovery")

    monkeypatch.setattr("mcp_google_workspace.auth.google_auth.get_credentials", _raise)

    tool_names = anyio.run(_client_list_tool_names, workspace_mcp)

    assert "gmail_send_email" in tool_names
    assert "sheets_get_spreadsheet" in tool_names
    assert "docs_get_document" in tool_names


def test_apps_dashboard_tool_keeps_ui_annotations():
    dashboard_tool = anyio.run(_get_tool, apps_mcp, "get_dashboard")
    state_tool = anyio.run(_get_tool, apps_mcp, "set_state")

    assert dashboard_tool.annotations.readOnlyHint is True
    assert dashboard_tool.annotations._meta == {"ui": {"resourceUri": "ui://apps/dashboard-ui"}}
    assert state_tool.annotations.openWorldHint is False
    assert state_tool.annotations.idempotentHint is True
