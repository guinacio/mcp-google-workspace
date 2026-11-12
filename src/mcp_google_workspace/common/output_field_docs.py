"""Documented result fields for tools whose inferred output schema is too thin.

``output_schemas.infer_tool_output_schema`` reads literal return dicts, so a
field built by a helper (``event_envelope``, ``file_envelope``...) comes out
untyped and undescribed. These entries document those fields from the code
that builds them, so an agent can interpret the result. They are merged into
the inferred (still open, ``additionalProperties: true``) schema.

Every type here must match what the implementation returns on success: a
client validates ``structuredContent`` against ``outputSchema``, so a wrong
claim fails real calls. Fields a builder may leave unset are nullable.
"""

from __future__ import annotations

import copy
from typing import Any, Final


def _s(kind: str | list[str], description: str, **extra: Any) -> dict[str, Any]:
    return {"type": kind, "description": description, **extra}


_STR_OR_NULL: Final = ["string", "null"]
_BOOL_OR_NULL: Final = ["boolean", "null"]

_RESOURCE_HANDLE: Final[dict[str, Any]] = _s(
    "object",
    "Stable resource handle: kind, id, uri (pass to resolve_workspace_resource), and name, mime_type, etag, "
    "modified_at and web_url when known.",
    properties={
        "kind": _s("string", "Resource kind, e.g. drive_file, gmail_message, contact."),
        "id": _s("string", "Resource ID for the matching service tool."),
        "uri": _s("string", "Resource URI, e.g. gdrive:///<file_id>."),
    },
)

_EVENT: Final[dict[str, Any]] = _s(
    "object",
    "Compact event.",
    properties={
        "id": _s(_STR_OR_NULL, "Event ID (an instance ID for an expanded recurring event)."),
        "title": _s("string", "Event title, '(untitled event)' when it has none."),
        "start": _s(_STR_OR_NULL, "Start in the account time zone (RFC3339), or a date for all-day events."),
        "end": _s(_STR_OR_NULL, "End in the account time zone (RFC3339), or a date for all-day events."),
        "event_timezone": _s(_STR_OR_NULL, "Zone the event was scheduled in, when it differs from the account's."),
        "all_day": _s("boolean", "Whether the event spans whole days."),
        "status": _s(_STR_OR_NULL, "confirmed, tentative or cancelled."),
        "organizer": _s("object", "Organizer name and email."),
        "attendee_count": _s("integer", "Number of guests."),
        "my_response": _s(_STR_OR_NULL, "The user's own response: needsAction, accepted, declined or tentative."),
        "requires_response": _s("boolean", "True when the user has not answered the invitation."),
        "meeting_url": _s(_STR_OR_NULL, "Video meeting link."),
        "location": _s(_STR_OR_NULL, "Location text."),
        "is_recurring": _s("boolean", "Whether the event belongs to a recurring series."),
        "updated_at": _s(_STR_OR_NULL, "Last modification time in the account time zone."),
    },
)

_FILE: Final[dict[str, Any]] = _s(
    "object",
    "Drive file metadata; a field Google did not return is null.",
    properties={
        "id": _s(_STR_OR_NULL, "File ID."),
        "name": _s(_STR_OR_NULL, "File name."),
        "kind": _s(_STR_OR_NULL, "folder, document, spreadsheet, presentation, form, image, video, audio or file."),
        "mime_type": _s(_STR_OR_NULL, "MIME type."),
        "size": _s(_STR_OR_NULL, "Size in bytes as Google sends it (a string); null for Google-native files."),
        "size_bytes": _s(["integer", "null"], "Size in bytes as a number."),
        "created_at": _s(_STR_OR_NULL, "Creation time in the account time zone."),
        "modified_at": _s(_STR_OR_NULL, "Last modification time in the account time zone."),
        "description": _s(_STR_OR_NULL, "File description."),
        "owners": _s(["array", "null"], "Owners (name, email); null for shared-drive files.", items={}),
        "last_modified_by": _s(["object", "null"], "Person who last modified the file."),
        "parent_ids": _s(["array", "null"], "Parent folder IDs.", items={"type": "string"}),
        "drive_id": _s(_STR_OR_NULL, "Shared drive ID, null in My Drive."),
        "web_url": _s(_STR_OR_NULL, "Link that opens the file in Drive."),
        "shared": _s(_BOOL_OR_NULL, "Whether the file is shared."),
        "starred": _s(_BOOL_OR_NULL, "Whether the user starred it."),
        "trashed": _s(_BOOL_OR_NULL, "Whether the file is in the trash."),
        "owned_by_me": _s(_BOOL_OR_NULL, "Whether the user owns it."),
        "can_edit": _s(_BOOL_OR_NULL, "Whether the user can edit it."),
        "can_download": _s(_BOOL_OR_NULL, "Whether the user can download it."),
    },
)

_MESSAGE_FIELDS: Final[dict[str, Any]] = {
    "id": _s(_STR_OR_NULL, "Message ID for gmail_read_emails."),
    "thread_id": _s(_STR_OR_NULL, "Conversation ID for gmail_get_thread."),
    "from": _s("object", "Sender name and email."),
    "subject": _s(_STR_OR_NULL, "Decoded subject."),
    "date": _s(_STR_OR_NULL, "Received time in the account time zone."),
    "snippet": _s(_STR_OR_NULL, "Short plain-text preview."),
    "unread": _s("boolean", "Whether the message is unread."),
    "has_attachments": _s("boolean", "Whether the message has attachments."),
}

_SEARCH_MESSAGE: Final[dict[str, Any]] = _s(
    "object",
    "Message summary.",
    properties={
        **_MESSAGE_FIELDS,
        "category": _s(_STR_OR_NULL, "First Gmail category label on the message (CATEGORY_*), or null."),
        "is_newsletter": _s("boolean", "Whether the message carries a List-Unsubscribe header."),
        "is_automated": _s("boolean", "Whether the sender looks automated (no-reply, bulk)."),
        "is_draft": _s("boolean", "Whether the message is a draft."),
        "is_sent": _s("boolean", "Whether the user sent it."),
    },
)

_FEED_MESSAGE: Final[dict[str, Any]] = _s(
    "object",
    "Message summary.",
    properties={**_MESSAGE_FIELDS, "direction": _s("string", "'received' or 'sent'.")},
)

_TASK: Final[dict[str, Any]] = _s(
    "object",
    "Task, shaped as in tasks_list_tasks.",
    properties={
        "id": _s(_STR_OR_NULL, "Task ID."),
        "title": _s("string", "Task title, '(untitled task)' when it has none."),
        "status": _s("string", "needsAction or completed."),
        "due_date": _s(_STR_OR_NULL, "Due date (YYYY-MM-DD); Google Tasks stores no due time."),
        "is_overdue": _s("boolean", "Open and due before today in the account time zone."),
        "completed_at": _s(_STR_OR_NULL, "Completion time in the account time zone."),
        "notes_snippet": _s("string", "Start of the notes."),
        "notes_truncated": _s("boolean", "Whether notes_snippet is cut short."),
        "parent_id": _s(_STR_OR_NULL, "Parent task ID for a subtask."),
        "updated_at": _s(_STR_OR_NULL, "Last modification time in the account time zone."),
        "deleted": _s("boolean", "Whether the task is deleted."),
        "hidden": _s("boolean", "Whether the task is hidden."),
        "url": _s(_STR_OR_NULL, "Link that opens the task."),
    },
)


_SHARED_DRIVE: Final[dict[str, Any]] = _s(
    "object",
    "Shared drive; a flag Google did not return is null.",
    properties={
        "id": _s(_STR_OR_NULL, "Shared drive ID, the drive_id drive_list_files takes."),
        "name": _s(_STR_OR_NULL, "Shared drive name."),
        "hidden": _s(_BOOL_OR_NULL, "Whether the drive is hidden from the user's default view."),
        "created_at": _s(_STR_OR_NULL, "Creation time in the account time zone."),
        "org_unit_id": _s(_STR_OR_NULL, "Organizational unit the drive belongs to."),
        "can_add_children": _s(_BOOL_OR_NULL, "Whether the user can add files and folders."),
        "can_edit": _s(_BOOL_OR_NULL, "Whether the user can edit its files."),
        "can_share": _s(_BOOL_OR_NULL, "Whether the user can share its files."),
        "can_manage_members": _s(_BOOL_OR_NULL, "Whether the user can add and remove members."),
        "can_download": _s(_BOOL_OR_NULL, "Whether the user can download its files."),
        "can_delete_drive": _s(_BOOL_OR_NULL, "Whether the user can delete the drive."),
        "admin_managed_restrictions": _s(_BOOL_OR_NULL, "Whether only administrators can change the restrictions."),
        "domain_users_only": _s(_BOOL_OR_NULL, "Whether access is limited to the drive's domain."),
        "drive_members_only": _s(_BOOL_OR_NULL, "Whether access is limited to drive members."),
        "copy_requires_writer_permission": _s(
            _BOOL_OR_NULL, "Whether copying, printing and downloading need writer access."
        ),
    },
)

_DRIVE_SUMMARY: Final[dict[str, Any]] = _s(
    "object",
    "The shared drive after the change.",
    properties={
        "id": _s(_STR_OR_NULL, "Shared drive ID."),
        "name": _s(_STR_OR_NULL, "Shared drive name."),
        "hidden": _s(_BOOL_OR_NULL, "Whether the drive is now hidden."),
    },
)

_PERMISSION: Final[dict[str, Any]] = _s(
    "object",
    "Sharing grant; a field Google did not return is null.",
    properties={
        "id": _s(_STR_OR_NULL, "Permission ID, the permission_id drive_share_file and drive_unshare_file take."),
        "type": _s(_STR_OR_NULL, "user, group, domain or anyone."),
        "role": _s(_STR_OR_NULL, "owner, organizer, fileOrganizer, writer, commenter or reader."),
        "email_address": _s(_STR_OR_NULL, "Grantee email for a user or group."),
        "domain": _s(_STR_OR_NULL, "Grantee domain for a domain grant."),
        "display_name": _s(_STR_OR_NULL, "Grantee display name."),
        "allow_file_discovery": _s(_BOOL_OR_NULL, "Whether a domain or anyone grant makes the file findable in search."),
        "pending_owner": _s(_BOOL_OR_NULL, "Whether the grantee is the pending owner of an ownership transfer."),
        "expires_at": _s(_STR_OR_NULL, "When the grant ends, in the account time zone."),
        "deleted": _s(_BOOL_OR_NULL, "Whether the grantee's account was deleted."),
    },
)

_LABEL: Final[dict[str, Any]] = _s(
    "object",
    "Gmail label.",
    properties={
        "id": _s(_STR_OR_NULL, "Label ID (system name or Label_...)."),
        "name": _s(_STR_OR_NULL, "Label name; '/' separates nested labels."),
        "type": _s(_STR_OR_NULL, "'system' or 'user'."),
        "message_list_visibility": _s(_STR_OR_NULL, "show or hide."),
        "label_list_visibility": _s(_STR_OR_NULL, "labelShow, labelShowIfUnread or labelHide."),
        "background_color": _s(_STR_OR_NULL, "Background color hex, null when unset."),
        "text_color": _s(_STR_OR_NULL, "Text color hex, null when unset."),
    },
)

_DRAFT_IDS: Final[dict[str, Any]] = {
    "id": _s(_STR_OR_NULL, "Draft ID, the draft_id the other draft tools take."),
    "message_id": _s(_STR_OR_NULL, "ID of the draft's current message."),
    "thread_id": _s(_STR_OR_NULL, "Thread the draft belongs to."),
}

_DRAFT: Final[dict[str, Any]] = _s(
    "object",
    "Draft: IDs only in a list; read by draft_id it adds the fields below, null when the format did not read them.",
    properties={
        **_DRAFT_IDS,
        "label_ids": _s(["array", "null"], "Label IDs on the draft message.", items={"type": "string"}),
        "date": _s(_STR_OR_NULL, "Last save time in the account time zone."),
        "from": _s(["object", "null"], "Sender name and email."),
        "to": _s(_STR_OR_NULL, "Decoded To header."),
        "cc": _s(_STR_OR_NULL, "Decoded Cc header."),
        "bcc": _s(_STR_OR_NULL, "Decoded Bcc header."),
        "subject": _s(_STR_OR_NULL, "Decoded subject."),
        "snippet": _s(_STR_OR_NULL, "Short plain-text preview."),
        "attachments": _s(
            ["array", "null"], "Attachments (filename, mime_type, size, download_id); format 'full' only.", items={}
        ),
        "raw": _s(_STR_OR_NULL, "Format 'raw' only: the RFC 2822 message, base64url-encoded."),
    },
)

_FILTER: Final[dict[str, Any]] = _s(
    "object",
    "Gmail filter.",
    properties={
        "id": _s(_STR_OR_NULL, "Filter ID, the filter_id delete takes."),
        "criteria": _s(
            "object",
            "Set criteria only, in the create names: from, to, subject, query, negated_query, has_attachment, "
            "exclude_chats, size, size_comparison.",
        ),
        "action": _s("object", "Set actions only: add_label_ids, remove_label_ids (label IDs) and forward."),
    },
)

_FORWARDING_ADDRESS: Final[dict[str, Any]] = _s(
    "object",
    "Forwarding address.",
    properties={
        "forwarding_email": _s(_STR_OR_NULL, "The address."),
        "verification_status": _s(_STR_OR_NULL, "'accepted' or 'pending'."),
    },
)

_VACATION: Final[dict[str, Any]] = _s(
    "object",
    "Vacation auto-reply, in the update parameter names; a field Google did not return is null.",
    properties={
        "enable_auto_reply": _s(_BOOL_OR_NULL, "Whether the auto-reply is on."),
        "response_subject": _s(_STR_OR_NULL, "Auto-reply subject."),
        "response_body_plain_text": _s(_STR_OR_NULL, "Plain-text body."),
        "response_body_html": _s(_STR_OR_NULL, "HTML body."),
        "restrict_to_contacts": _s(_BOOL_OR_NULL, "Whether only contacts get the reply."),
        "restrict_to_domain": _s(_BOOL_OR_NULL, "Whether only the account's domain gets the reply."),
        "start_time": _s(["integer", "null"], "Start as epoch milliseconds."),
        "end_time": _s(["integer", "null"], "End as epoch milliseconds."),
    },
)


def _array(item: dict[str, Any], description: str) -> dict[str, Any]:
    return _s("array", description, items=copy.deepcopy(item))


_TIMEZONE = _s("string", "IANA time zone of every time in this result.")
_STATE = _s(
    "string",
    "Connection state: connected, refresh_required, missing_scopes, reauth_required, not_connected or error.",
)

OUTPUT_FIELD_DOCS: Final[dict[str, dict[str, dict[str, Any]]]] = {
    "calendar_get_calendar_digest": {
        "window_day_count": _s("integer", "Days covered, starting now."),
        "events": _array(_EVENT, "Events in the window, earliest first."),
        "requires_response": _s(
            "array",
            "The events whose invitation the user has not answered, shaped like events.",
            items={"type": "object"},
        ),
        "account_timezone": _TIMEZONE,
    },
    "calendar_search_events": {
        "events": _array(_EVENT, "Events on this page."),
        "range_preset": _s(_STR_OR_NULL, "The range_preset that was applied, or null."),
        "effective_time_min": _s(_STR_OR_NULL, "Window start actually sent to Google; reuse it when paging."),
        "effective_time_max": _s(_STR_OR_NULL, "Window end actually sent to Google; reuse it when paging."),
        "account_timezone": _TIMEZONE,
    },
    "drive_list_files": {
        "files": _array(_FILE, "Files on this page."),
        "account_timezone": _TIMEZONE,
    },
    "drive_list_drives": {"drives": _array(_SHARED_DRIVE, "Shared drives on this page.")},
    "drive_update_drive": {"drive": copy.deepcopy(_DRIVE_SUMMARY)},
    "drive_list_permissions": {"permissions": _array(_PERMISSION, "Grants on this page.")},
    "drive_share_file": {"permission": copy.deepcopy(_PERMISSION)},
    "gmail_list_labels": {"labels": _array(_LABEL, "Every label in the mailbox.")},
    "gmail_manage_labels": {"label": copy.deepcopy(_LABEL)},
    "gmail_list_drafts": {"drafts": _array(_DRAFT, "Drafts on this page, or the one draft read.")},
    "gmail_save_draft": {
        "draft": _s(
            "object",
            "The saved draft.",
            properties={
                **copy.deepcopy(_DRAFT_IDS),
                "label_ids": _s(["array", "null"], "Label IDs on the draft message.", items={"type": "string"}),
            },
        )
    },
    "gmail_manage_filters": {
        "filters": _array(_FILTER, "Every filter (action 'list')."),
        "filter": copy.deepcopy(_FILTER),
    },
    "gmail_manage_forwarding": {
        "forwarding_addresses": _array(_FORWARDING_ADDRESS, "Every forwarding address (action 'list')."),
        "forwarding_address": copy.deepcopy(_FORWARDING_ADDRESS),
    },
    "gmail_manage_vacation": {"vacation": copy.deepcopy(_VACATION)},
    "gmail_get_mail_digest": {
        "messages": _array(_FEED_MESSAGE, "Received and sent messages in the window; drafts excluded."),
        "window": _s("string", "The lookback window that was applied, e.g. '3d'."),
        "account_timezone": _TIMEZONE,
    },
    "gmail_search_emails": {
        "messages": _array(_SEARCH_MESSAGE, "Matching messages (absent with group_by_thread)."),
        "threads": _s(
            "array",
            "Matching conversations (only with group_by_thread): each the newest message's fields, as in "
            "messages, plus message_count.",
            items={"type": "object", "properties": {"message_count": _s("integer", "Messages in the thread.")}},
        ),
        "result_size_estimate": _s("integer", "Google's estimate of the total number of matches."),
        "effective_query": _s(_STR_OR_NULL, "The Gmail query actually run, helper filters included."),
        "account_timezone": _TIMEZONE,
    },
    "tasks_tasks_digest": {
        "tasklist_id": _s("string", "ID of the list that was read."),
        "overdue": _array(_TASK, "Open tasks due before today."),
        "upcoming": _array(_TASK, "Open tasks due from today through the window, soonest first."),
        "unscheduled": _array(_TASK, "Open tasks with no due date."),
        "count": _s("integer", "Tasks in the three groups together."),
        "window_days": _s("integer", "Days ahead of today counted as upcoming."),
        "account_timezone": _TIMEZONE,
    },
    "search_workspace": {
        "query": _s("string", "The query that was searched."),
        "services": _s("array", "Services that were searched.", items={"type": "string"}),
        "matches": _array(
            _s(
                "object",
                "One hit: service, id, kind and resource, plus Drive name, mime_type, modified_at, web_url; "
                "contact name, email; or Gmail thread_id, subject, from, date, snippet, unread.",
                properties={
                    "service": _s("string", "drive, people or gmail."),
                    "id": _s(_STR_OR_NULL, "ID for that service's tools."),
                    "kind": _s(_STR_OR_NULL, "Drive file kind, 'contact' or 'message'."),
                    "resource": copy.deepcopy(_RESOURCE_HANDLE),
                },
            ),
            "Hits in the order of services, each service's hits in its own order.",
        ),
        "account_timezone": _s(_STR_OR_NULL, "IANA time zone of the times in this result."),
        "errors": _s("object", "Error message per service that failed; empty when all answered."),
    },
    "connect_google_workspace": {
        "state": _STATE,
        "connected": _s("boolean", "Whether a usable grant is stored (local mode)."),
        "mode": _s("string", "'local_stdio' when consent ran on the server machine."),
        "granted_capabilities": _s("array", "Services the stored grant covers.", items={"type": "string"}),
        "granted_scopes": _s("array", "OAuth scopes the stored grant covers.", items={"type": "string"}),
        "requested_capabilities": _s("array", "Services that were asked for.", items={"type": "string"}),
        "action": _s(_STR_OR_NULL, "What the user must do next, or null."),
        "authorization_url": _s("string", "Remote mode: Google consent URL for the user to open."),
        "expires_at": _s("integer", "Remote mode: Unix time (seconds) when authorization_url expires."),
        "after_consent": _s("object", "Remote mode: the tool to call once consent is done."),
        "capabilities": _s("array", "Remote mode: services requested in this consent.", items={"type": "string"}),
        "requested_scopes": _s("array", "Remote mode: every scope the consent asks for.", items={"type": "string"}),
        "newly_requested_scopes": _s(
            "array", "Remote mode: scopes not granted before this consent.", items={"type": "string"}
        ),
        "if_callback_blocked": _s(
            "string", "Behind an authenticating gateway: how to finish consent with authorization_response."
        ),
        "message": _s("string", "Outcome of a pasted authorization_response, in words."),
    },
    "get_google_connection_status": {
        "state": _STATE,
        "connected": _s("boolean", "Whether a usable grant is stored for the caller."),
        "granted_capabilities": _s("array", "Services the grant covers.", items={"type": "string"}),
        "granted_scopes": _s("array", "OAuth scopes the grant covers.", items={"type": "string"}),
        "checked_capability": _s(_STR_OR_NULL, "The capability argument, or null."),
        "required_scopes": _s("array", "Scopes checked_capability needs.", items={"type": "string"}),
        "expires_at": _s(_STR_OR_NULL, "Access-token expiry, or null."),
        "action": _s(_STR_OR_NULL, "What the user must do to reconnect, or null."),
        "server": _s("object", "This deployment's tool tier, enabled namespaces, OAuth capabilities and file inputs."),
    },
    "disconnect_google_workspace": {
        "status": _s("string", "'disconnected', or 'cancelled' when the user declined."),
        "grant_revoked": _s("boolean", "Whether Google confirmed the grant was revoked."),
        "revocation_error": _s(_STR_OR_NULL, "Why revocation failed, or null."),
    },
    "prepare_workspace_action": {
        "status": _s("string", "'prepared'."),
        "action": _s("string", "Consequential action being confirmed, as named by the tool's consent policy, e.g. 'send_email'."),
        "preview": _s("string", "Exact text to show the user before committing."),
        "commit_token": _s("string", "One-time token for commit_workspace_action."),
        "expires_at": _s("integer", "Unix time (seconds) when commit_token expires."),
        "next_action": _s("object", "The commit_workspace_action call to make after the user agrees."),
    },
    "commit_workspace_action": {
        "status": _s("string", "'committed' when the bound call ran."),
        "tool": _s("string", "Published name of the tool that ran."),
        "result": _s("object", "That tool's structured result, as its own outputSchema describes."),
    },
    "resolve_workspace_resource": {
        "resource": copy.deepcopy(_RESOURCE_HANDLE),
        "verified": _s("boolean", "True when Google confirmed the resource still exists; false when only parsed."),
    },
}


def apply_output_field_docs(tool_name: str, schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """Merge the documented fields for ``tool_name`` into its (open) output schema."""
    docs = OUTPUT_FIELD_DOCS.get(tool_name)
    if docs is None or schema is None:
        return schema
    properties = schema.setdefault("properties", {})
    for field, documented in docs.items():
        properties[field] = {**properties.get(field, {}), **copy.deepcopy(documented)}
    return schema
