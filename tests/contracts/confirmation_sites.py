"""Shared fixtures for the consent tests.

``CONSENT_CASES`` holds one call per trigger of the consequential-call table
(``common/consent.py``): every way a call can need consent. Google is
replaced at the single ``_build_service_now`` seam with a recorder, so every
executed request is observed without network access.
"""

from __future__ import annotations

from typing import Any

import mcp_types

# Google API method-name prefixes that change provider state (deleteContact,
# batchDelete, modify, send, ...). Reads are get/list/getProfile.
MUTATING_PREFIXES = ("batch", "create", "delete", "end", "insert", "modify", "patch", "send", "trash", "update")

CANNED_RESPONSES: dict[str, dict[str, Any]] = {
    "users.messages.get": {
        "id": "m1",
        "threadId": "t1",
        "payload": {
            "headers": [
                {"name": "From", "value": "Sender <sender@example.com>"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": "Hello"},
                {"name": "Message-ID", "value": "<m1@example.com>"},
            ]
        },
    },
    "users.getProfile": {"emailAddress": "me@example.com"},
    "users.settings.sendAs.list": {"sendAs": []},
    # Calendar's account time-zone setting, read by tools that return local times.
    "settings.get": {"kind": "calendar#setting", "id": "timezone", "value": "UTC"},
    # Gmail label names and IDs resolve against the mailbox's label list.
    "users.labels.list": {"labels": [{"id": "Label_1", "name": "Work", "type": "user"}]},
    # A saved draft, previewed before it is sent.
    "users.drafts.get": {
        "id": "d1",
        "message": {
            "payload": {
                "headers": [
                    {"name": "To", "value": "Ana <ana@example.com>"},
                    {"name": "Bcc", "value": "hidden@example.com"},
                    {"name": "Subject", "value": "Draft subject"},
                ]
            }
        },
    },
}


class GoogleRecorder:
    """Stand-in Google API client that records each executed method chain."""

    def __init__(self, calls: list[str], path: tuple[str, ...] = ()) -> None:
        self._calls = calls
        self._path = path

    def __getattr__(self, name: str) -> "GoogleRecorder":
        if name.startswith("__"):
            raise AttributeError(name)
        return GoogleRecorder(self._calls, (*self._path, name))

    def __call__(self, *_args: Any, **_kwargs: Any) -> "GoogleRecorder":
        return self

    def execute(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        method = ".".join(self._path)
        self._calls.append(method)
        return dict(CANNED_RESPONSES.get(method, {}))


def mutations(calls: list[str]) -> list[str]:
    return [call for call in calls if call.rsplit(".", 1)[-1].startswith(MUTATING_PREFIXES)]


# One call per trigger of the consequential-call table (common/consent.py),
# keyed by (tool, action): the arguments select exactly that trigger.
# ``test_consent_table.py`` fails when a trigger has no case here or a case no
# longer selects its trigger, so this list always covers the whole table.
_TEN_IDS = [f"m{index}" for index in range(10)]
CONSENT_CASES: dict[tuple[str, str], dict[str, Any]] = {
    ("disconnect_google_workspace", "disconnect"): {},
    ("gmail_delete", "delete_thread"): {"thread_id": "t1", "mode": "permanent"},
    ("gmail_delete", "delete_email"): {"message_ids": ["m1"], "mode": "permanent"},
    ("gmail_delete", "batch_delete"): {"message_ids": ["m1", "m2"], "mode": "permanent"},
    ("gmail_delete", "batch_trash"): {"message_ids": _TEN_IDS},
    ("gmail_delete_draft", "delete_draft"): {"draft_id": "d1"},
    ("gmail_manage_filters", "delete_filter"): {"action": "delete", "filter_id": "f1"},
    ("gmail_manage_filters", "create_filter"): {
        "action": "create",
        "criteria": {"from": "boss@example.com"},
        "filter_action": {"forward": "elsewhere@example.com"},
    },
    ("gmail_manage_forwarding", "create_forwarding_address"): {
        "action": "create",
        "forwarding_email": "f@example.com",
    },
    ("gmail_manage_forwarding", "delete_forwarding_address"): {
        "action": "delete",
        "forwarding_email": "f@example.com",
    },
    ("gmail_manage_labels", "delete_label"): {"action": "delete", "label": "Work"},
    ("gmail_manage_vacation", "update_vacation"): {
        "action": "update",
        "enable_auto_reply": True,
        "response_subject": "Away",
        "response_body_plain_text": "Back on Monday.",
    },
    ("gmail_modify_messages", "modify_messages"): {"message_ids": _TEN_IDS, "mark_read": True},
    ("gmail_reply", "reply_all_email"): {"message_id": "m1", "text_body": "b", "reply_all": True},
    ("gmail_reply", "reply_email"): {"message_id": "m1", "text_body": "b"},
    ("gmail_send_draft", "send_draft"): {"draft_id": "d1"},
    ("gmail_send_email", "send_email"): {"to": ["to@example.com"], "subject": "s", "text_body": "b"},
    ("calendar_create_event", "create_event"): {
        "summary": "Sync",
        "start_datetime": "2030-01-07T10:00:00Z",
        "end_datetime": "2030-01-07T10:30:00Z",
        "attendees": [{"email": "guest@example.com"}],
    },
    ("calendar_delete_event", "delete_event"): {"event_id": "e1"},
    ("calendar_set_event_attachments", "remove_event_attachment"): {"event_id": "e1", "remove": [{"file_id": "f1"}]},
    ("calendar_set_event_attachments", "update_event_attachments"): {
        "event_id": "e1",
        "add": [{"file_url": "https://drive.google.com/file/d/f2/view", "title": "Agenda"}],
        "send_updates": "all",
    },
    ("calendar_update_event", "update_event"): {"event_id": "e1", "attendees": [{"email": "guest@example.com"}]},
    ("drive_delete_file", "delete_file"): {"file_id": "f1", "delete_mode": "permanent"},
    ("drive_share_file", "update_permission"): {"file_id": "f1", "permission_id": "p1", "role": "reader"},
    ("drive_share_file", "create_permission"): {
        "file_id": "f1",
        "role": "reader",
        "type": "user",
        "email_address": "a@example.com",
    },
    ("drive_unshare_file", "delete_permission"): {"file_id": "f1", "permission_id": "p1"},
    ("docs_batch_update_document", "batch_update_document"): {
        "document_id": "doc1",
        "requests": [{"deleteContentRange": {"range": {"startIndex": 1, "endIndex": 2}}}],
    },
    ("forms_batch_update_form", "batch_update_form"): {
        "form_id": "form1",
        "requests": [{"deleteItem": {"location": {"index": 0}}}],
    },
    ("sheets_batch_update_spreadsheet", "batch_update_spreadsheet"): {
        "spreadsheet_id": "s1",
        "requests": [{"deleteSheet": {"sheetId": 1}}],
    },
    ("slides_batch_update_presentation", "batch_update_presentation"): {
        "presentation_id": "pres1",
        "requests": [{"deleteObject": {"objectId": "o1"}}],
    },
    ("tasks_delete_task", "delete_task"): {"tasklist_id": "l1", "task_id": "t1"},
    ("tasks_update_task", "delete_task"): {"tasklist_id": "l1", "task_id": "t1", "deleted": True},
    ("people_delete_contact", "delete_contact"): {"person_name": "people/c1"},
    ("chat_create_message", "create_message"): {"request": {"space_name": "spaces/A", "text": "hi"}},
    ("chat_delete_message", "delete_message"): {"request": {"message_name": "spaces/A/messages/B"}},
    ("chat_post_message_simple", "post_message_simple"): {"request": {"space_name": "spaces/A", "text": "hi"}},
    ("chat_reply_to_message", "reply_to_message"): {
        "request": {"message_name": "spaces/A/messages/B", "text": "hi"}
    },
    ("keep_append_note_content", "delete_note"): {
        "request": {
            "note_name": "notes/n1",
            "text_append": "more",
            "apply_via_replacement": True,
            "delete_original_on_apply": True,
        }
    },
    ("keep_create_note", "create_note"): {
        "request": {"title": "t", "text_body": "b", "collaborator_emails": ["c@example.com"]}
    },
    ("keep_delete_note", "delete_note"): {"request": {"note_name": "notes/n1"}},
    ("keep_patch_note_checklist", "delete_note"): {
        "request": {
            "note_name": "notes/n1",
            "operation": "add",
            "text": "item",
            "apply_via_replacement": True,
            "delete_original_on_apply": True,
        }
    },
    ("keep_share_note", "share_note"): {"request": {"note_name": "notes/n1", "collaborator_emails": ["c@example.com"]}},
    ("keep_unshare_note", "unshare_note"): {
        "request": {"note_name": "notes/n1", "permission_names": ["notes/n1/permissions/p1"]}
    },
    ("meet_end_active_conference", "end_active_conference"): {"space_name": "spaces/abc"},
    ("meet_update_space", "update_space"): {"space_name": "spaces/abc", "config": {"accessType": "OPEN"}},
}

CONFIRMATION_SITES: list[tuple[str, dict[str, Any]]] = [
    (tool, arguments) for (tool, _action), arguments in CONSENT_CASES.items()
]
SITE_IDS = [f"{tool}[{action}]" for tool, action in CONSENT_CASES]
assert len(set(SITE_IDS)) == len(SITE_IDS), "confirmation site ids must be unique"

#: Tools that run as background tasks (``task=True``) among the table's tools.
TASK_SITES = [
    (tool, arguments)
    for (tool, _action), arguments in CONSENT_CASES.items()
    if tool.endswith(("_batch_update_document", "_batch_update_form", "_batch_update_spreadsheet", "_batch_update_presentation"))
]
TASK_SITE_IDS = [tool for tool, _arguments in TASK_SITES]
#: The other sites: a 2026-07-28 client runs task-capable tools as tasks.
FOREGROUND_SITES = [site for site in CONFIRMATION_SITES if site not in TASK_SITES]
FOREGROUND_SITE_IDS = [site_id for site, site_id in zip(CONFIRMATION_SITES, SITE_IDS) if site not in TASK_SITES]


def confirmation_operation(tool: str, arguments: dict[str, Any]) -> str:
    """The action name the call's confirmation reports, from the table."""
    from mcp_google_workspace.common.consent import classify

    trigger = classify(tool, arguments).trigger
    assert trigger is not None, (tool, arguments)
    return trigger.action


def explicit_confirm(tool: str, arguments: dict[str, Any]) -> bool:
    """Whether the call asks with the explicit ``confirm`` checkbox (sends)."""
    from mcp_google_workspace.common.consent import classify

    trigger = classify(tool, arguments).trigger
    return trigger is not None and trigger.explicit_confirm


# Sites whose confirmation uses the explicit ``confirm`` checkbox schema.
EXPLICIT_CONFIRM_SITES = {"gmail_send_email", "gmail_reply", "gmail_send_draft"}

OPTIONAL_FLAGS = ("ENABLE_APPS_DASHBOARD", "ENABLE_CHAT", "ENABLE_GEMINI", "ENABLE_KEEP", "ENABLE_MEET")
#: Environment for a composition that serves every table tool.
FULL_TABLE_ENV = {"ENABLE_CHAT": "true", "ENABLE_KEEP": "true", "ENABLE_MEET": "true", "MCP_TOOL_TIER": "extended"}


def answer_field(result: mcp_types.InputRequiredResult) -> str:
    """The single boolean property the asked elicitation schema declares."""
    assert result.input_requests is not None
    request = result.input_requests["confirm"]
    assert isinstance(request, mcp_types.ElicitRequest)
    properties = request.params.requested_schema["properties"]
    assert len(properties) == 1
    return next(iter(properties))


def answer(result: mcp_types.InputRequiredResult, action: str, value: Any = True) -> dict[str, Any]:
    """``inputResponses`` answering the adapter's single confirmation request."""
    if action != "accept":
        return {"confirm": mcp_types.ElicitResult(action=action)}  # type: ignore[arg-type]
    return {"confirm": mcp_types.ElicitResult(action="accept", content={answer_field(result): value})}
