import anyio
import os

from mcp_google_workspace.server import workspace_mcp


async def _list_tool_names() -> list[str]:
    tools = await workspace_mcp.list_tools(run_middleware=False)
    return [t.name for t in tools]


def test_composition_mounts_namespaced_tools():
    tool_names = anyio.run(_list_tool_names)
    assert "gmail_send_email" in tool_names
    assert "gmail_list_filters" in tool_names
    assert "gmail_create_filter" in tool_names
    assert "gmail_delete_filter" in tool_names
    assert "gmail_list_drafts" in tool_names
    assert "gmail_create_draft" in tool_names
    assert "gmail_list_threads" in tool_names
    assert "gmail_get_thread" in tool_names
    assert "gmail_list_history" in tool_names
    assert "gmail_list_forwarding_addresses" in tool_names
    assert "gmail_get_vacation_settings" in tool_names
    assert "gmail_untrash_email" in tool_names
    assert "gmail_mark_as_spam" in tool_names
    assert "gmail_mark_as_not_spam" in tool_names
    assert "calendar_get_events" in tool_names
    assert "calendar_get_event" in tool_names
    assert "calendar_list_event_attachments" in tool_names
    assert "calendar_add_event_attachment" in tool_names
    assert "calendar_remove_event_attachment" in tool_names
    assert "calendar_download_event_attachment" in tool_names
    assert "drive_list_files" in tool_names
    assert "drive_get_file" in tool_names
    assert "drive_create_folder" in tool_names
    assert "drive_create_file_metadata" in tool_names
    assert "drive_upload_file" in tool_names
    assert "drive_update_file_metadata" in tool_names
    assert "drive_update_file_content" in tool_names
    assert "drive_move_file" in tool_names
    assert "drive_copy_file" in tool_names
    assert "drive_delete_file" in tool_names
    assert "drive_download_file" in tool_names
    assert "drive_export_google_file" in tool_names
    assert "drive_get_file_content_capabilities" in tool_names
    assert "drive_list_permissions" in tool_names
    assert "drive_get_permission" in tool_names
    assert "drive_create_permission" in tool_names
    assert "drive_update_permission" in tool_names
    assert "drive_delete_permission" in tool_names
    assert "drive_list_drives" in tool_names
    assert "drive_get_drive" in tool_names
    assert "drive_hide_drive" in tool_names
    assert "drive_unhide_drive" in tool_names
    if os.getenv("ENABLE_APPS_DASHBOARD", "").strip().lower() in {"1", "true", "yes", "on"}:
        assert "apps_get_dashboard" in tool_names
        assert "apps_get_weekly_calendar_view" in tool_names
        assert "apps_get_morning_briefing" in tool_names
        assert "apps_find_meeting_slots" in tool_names
    else:
        assert "apps_get_dashboard" not in tool_names
    if os.getenv("ENABLE_CHAT", "").strip().lower() in {"1", "true", "yes", "on"}:
        assert "chat_create_message" in tool_names
    else:
        assert "chat_create_message" not in tool_names
    if os.getenv("ENABLE_KEEP", "").strip().lower() in {"1", "true", "yes", "on"}:
        assert "keep_create_note" in tool_names
    else:
        assert "keep_create_note" not in tool_names
