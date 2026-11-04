"""Tool names retired in 2.0.0 and the tool that replaces each.

2.0.0 removes these names outright: they are absent from ``tools/list`` in
every tier and a call to one is an unknown tool. The CHANGELOG's "Retired
tools" table lists the same pairs (``tests/test_tool_consolidation.py``
checks both).
"""

from __future__ import annotations

RETIRED_TOOLS: dict[str, str] = {
    # Gmail
    "gmail_mark_as_read": "gmail_modify_messages",
    "gmail_mark_as_unread": "gmail_modify_messages",
    "gmail_mark_as_spam": "gmail_modify_messages",
    "gmail_mark_as_not_spam": "gmail_modify_messages",
    "gmail_move_email": "gmail_modify_messages",
    "gmail_apply_labels": "gmail_modify_messages",
    "gmail_batch_modify": "gmail_modify_messages",
    "gmail_modify_thread": "gmail_modify_messages",
    "gmail_untrash_email": "gmail_modify_messages",
    "gmail_untrash_thread": "gmail_modify_messages",
    "gmail_delete_email": "gmail_delete",
    "gmail_batch_delete": "gmail_delete",
    "gmail_delete_thread": "gmail_delete",
    "gmail_trash_thread": "gmail_delete",
    "gmail_reply_email": "gmail_reply",
    "gmail_reply_all_email": "gmail_reply",
    "gmail_list_threads": "gmail_search_emails",
    "gmail_list_attachments": "gmail_read_emails",
    "gmail_create_draft": "gmail_save_draft",
    "gmail_update_draft": "gmail_save_draft",
    "gmail_get_draft": "gmail_list_drafts",
    "gmail_create_label": "gmail_manage_labels",
    "gmail_update_label": "gmail_manage_labels",
    "gmail_delete_label": "gmail_manage_labels",
    "gmail_list_filters": "gmail_manage_filters",
    "gmail_create_filter": "gmail_manage_filters",
    "gmail_delete_filter": "gmail_manage_filters",
    "gmail_list_forwarding_addresses": "gmail_manage_forwarding",
    "gmail_get_forwarding_address": "gmail_manage_forwarding",
    "gmail_create_forwarding_address": "gmail_manage_forwarding",
    "gmail_delete_forwarding_address": "gmail_manage_forwarding",
    "gmail_get_vacation_settings": "gmail_manage_vacation",
    "gmail_update_vacation_settings": "gmail_manage_vacation",
    # Drive
    "drive_create_permission": "drive_share_file",
    "drive_update_permission": "drive_share_file",
    "drive_delete_permission": "drive_unshare_file",
    "drive_get_permission": "drive_list_permissions",
    "drive_get_drive": "drive_list_drives",
    "drive_hide_drive": "drive_update_drive",
    "drive_unhide_drive": "drive_update_drive",
    "drive_get_file_content_capabilities": "drive_get_file",
    "drive_update_file_content": "drive_upload_file",
    "drive_create_file_metadata": "drive_upload_file",
    "drive_export_google_file": "drive_download_file",
    # Calendar
    "calendar_list_calendars": "calendar_get_calendar_context",
    "calendar_check_time_availability": "calendar_find_free_time",
    "calendar_find_common_free_slots": "calendar_find_free_time",
    "calendar_add_event_attachment": "calendar_set_event_attachments",
    "calendar_remove_event_attachment": "calendar_set_event_attachments",
    "calendar_download_event_attachment": "drive_download_file",
    # Tasks
    "tasks_get_tasklist": "tasks_list_tasklists",
    "tasks_get_task": "tasks_list_tasks",
    "tasks_complete_task": "tasks_update_task",
    "tasks_move_task": "tasks_update_task",
    # People
    "people_list_contacts": "people_search_contacts",
    "people_list_contact_groups": "people_manage_contact_groups",
    "people_create_contact_group": "people_manage_contact_groups",
    "people_modify_contact_group_members": "people_manage_contact_groups",
    # Docs, Sheets, Slides, Forms
    "docs_get_document": "docs_read_document",
    "sheets_get_sheet_values": "sheets_read_sheet_values",
    "sheets_batch_get_sheet_values": "sheets_read_sheet_values",
    "slides_get_presentation": "slides_read_presentation",
    "slides_get_slide_page": "slides_read_presentation",
    "forms_get_form_response": "forms_list_form_responses",
    # Workspace Files uploads (renamed namespace)
    "files_file_manager": "uploads_file_manager",
    "files_list_files": "uploads_list_files",
    "files_list_files_page": "uploads_list_files",
    "files_read_file": "uploads_read_file",
    "files_store_files": "uploads_store_files",
    "files_delete_file": "uploads_delete_file",
    # Root
    "get_workspace_capabilities": "get_google_connection_status",
    "refresh_workspace_catalog": "get_google_connection_status",
}
