import anyio

from mcp_google_workspace.drive.server import drive_mcp
from mcp_google_workspace.drive.presentation import file_envelope


async def _list_drive_tools() -> list[str]:
    tools = await drive_mcp.list_tools(run_middleware=False)
    return [t.name for t in tools]


def test_drive_server_registers_expected_tools():
    tool_names = anyio.run(_list_drive_tools)
    assert "list_files" in tool_names
    assert "get_file" in tool_names
    assert "create_folder" in tool_names
    assert "create_file_metadata" in tool_names
    assert "upload_file" in tool_names
    assert "update_file_metadata" in tool_names
    assert "update_file_content" in tool_names
    assert "move_file" in tool_names
    assert "copy_file" in tool_names
    assert "delete_file" in tool_names
    assert "download_file" in tool_names
    assert "export_google_file" in tool_names
    assert "get_file_content_capabilities" in tool_names
    assert "list_permissions" in tool_names
    assert "get_permission" in tool_names
    assert "create_permission" in tool_names
    assert "update_permission" in tool_names
    assert "delete_permission" in tool_names
    assert "list_drives" in tool_names
    assert "get_drive" in tool_names
    assert "hide_drive" in tool_names
    assert "unhide_drive" in tool_names


def test_file_envelope_surfaces_owner_state_and_capabilities():
    result = file_envelope(
        {
            "id": "file-1", "name": "Plan", "mimeType": "application/vnd.google-apps.document",
            "owners": [{"displayName": "Ada", "emailAddress": "ada@example.com"}],
            "capabilities": {"canEdit": True, "canDownload": True}, "shared": True,
        }
    )
    assert result["kind"] == "document"
    assert result["owners"][0]["email"] == "ada@example.com"
    assert result["can_edit"] is True
