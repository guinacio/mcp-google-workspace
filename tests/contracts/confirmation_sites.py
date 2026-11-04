"""Shared fixtures for the confirmation-adapter tests.

``CONFIRMATION_SITES`` covers all 24 confirmation sites of the W0 inventory:
the 13 former direct ``ctx.elicit`` call sites, plus the former shared helper
(site 14) through each of its 10 callers, 23 tool invocations in total (since 2.0.0 several sites are
reached through one consolidated tool, selected by its arguments). Each
entry's arguments select the branch requiring confirmation. Google is
replaced at the single ``_build_service_now`` seam with a recorder, so every
executed request is observed without network access.
"""

from __future__ import annotations

from typing import Any

import mcp_types

# Google API method-name prefixes that change provider state (deleteContact,
# batchDelete, modify, send, ...). Reads are get/list/getProfile.
MUTATING_PREFIXES = ("batch", "create", "delete", "insert", "modify", "patch", "send", "trash", "update")

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


CONFIRMATION_SITES: list[tuple[str, dict[str, Any]]] = [
    # 13 former direct ctx.elicit call sites (the 14th was the shared helper).
    ("calendar_delete_event", {"event_id": "e1"}),
    ("chat_create_message", {"request": {"space_name": "spaces/A", "text": "hi", "notify": True}}),
    ("chat_delete_message", {"request": {"message_name": "spaces/A/messages/B"}}),
    ("chat_post_message_simple", {"request": {"space_name": "spaces/A", "text": "hi", "notify": True}}),
    (
        "chat_reply_to_message",
        {"request": {"message_name": "spaces/A/messages/B", "text": "hi", "notify": True}},
    ),
    ("drive_delete_file", {"file_id": "f1", "delete_mode": "permanent", "confirm_permanent": True}),
    ("gmail_delete", {"message_ids": ["m1", "m2"], "mode": "permanent"}),
    (
        "gmail_send_email",
        {"to": ["to@example.com"], "subject": "s", "text_body": "b", "confirm_send": True},
    ),
    ("gmail_reply", {"message_id": "m1", "text_body": "b", "confirm_send": True}),
    ("gmail_delete", {"message_ids": ["m1"], "mode": "permanent"}),
    ("gmail_delete", {"thread_id": "t1", "mode": "permanent"}),
    ("keep_create_note", {"request": {"title": "t", "text_body": "b", "confirm_create": True}}),
    ("keep_delete_note", {"request": {"note_name": "notes/n1", "confirm_delete": True}}),
    # 10 callers of the former shared helper.
    ("calendar_set_event_attachments", {"event_id": "e1", "remove": [{"file_id": "f1"}]}),
    (
        "drive_share_file",
        {"file_id": "f1", "role": "reader", "type": "user", "email_address": "a@example.com"},
    ),
    ("drive_share_file", {"file_id": "f1", "permission_id": "p1", "role": "reader"}),
    ("drive_unshare_file", {"file_id": "f1", "permission_id": "p1"}),
    ("gmail_delete_draft", {"draft_id": "d1"}),
    ("gmail_manage_filters", {"action": "delete", "filter_id": "f1"}),
    ("gmail_manage_labels", {"action": "delete", "label": "Work"}),
    ("gmail_manage_forwarding", {"action": "delete", "forwarding_email": "f@example.com"}),
    ("people_delete_contact", {"person_name": "people/c1"}),
    ("tasks_delete_task", {"tasklist_id": "l1", "task_id": "t1"}),
]

# Several sites share one tool since 2.0.0 (gmail_delete, drive_share_file);
# their ids name the site's predecessor so each stays distinct and readable.
_SHARED_TOOL_SITE_IDS = {
    ("gmail_delete", ("message_ids", "mode"), 2): "gmail_delete[batch_delete]",
    ("gmail_delete", ("message_ids", "mode"), 1): "gmail_delete[delete_email]",
    ("gmail_delete", ("mode", "thread_id"), 0): "gmail_delete[delete_thread]",
    ("drive_share_file", ("email_address", "file_id", "role", "type"), 0): "drive_share_file[create_permission]",
    ("drive_share_file", ("file_id", "permission_id", "role"), 0): "drive_share_file[update_permission]",
}


def _site_id(tool: str, arguments: dict[str, Any]) -> str:
    count = len(arguments.get("message_ids") or [])
    return _SHARED_TOOL_SITE_IDS.get((tool, tuple(sorted(arguments)), count), tool)


SITE_IDS = [_site_id(tool, arguments) for tool, arguments in CONFIRMATION_SITES]
assert len(set(SITE_IDS)) == len(SITE_IDS), "confirmation site ids must be unique"

# A consolidated tool keeps its predecessor's confirmation, operation name
# included (2.0.0 changed tool names, not confirmations). Sites not listed
# confirm under their tool's own name.
_PREDECESSOR_OPERATIONS = {
    "gmail_delete[batch_delete]": "batch_delete",
    "gmail_delete[delete_email]": "delete_email",
    "gmail_delete[delete_thread]": "delete_thread",
    "gmail_reply": "reply_email",
    "calendar_set_event_attachments": "remove_event_attachment",
    "drive_share_file[create_permission]": "create_permission",
    "drive_share_file[update_permission]": "update_permission",
    "drive_unshare_file": "delete_permission",
    "gmail_manage_filters": "delete_filter",
    "gmail_manage_labels": "delete_label",
    "gmail_manage_forwarding": "delete_forwarding_address",
}


def confirmation_operation(tool: str, arguments: dict[str, Any]) -> str:
    """The operation name the site's confirmation reports."""
    return _PREDECESSOR_OPERATIONS.get(_site_id(tool, arguments)) or tool.split("_", 1)[1]


# Sites whose confirmation uses the explicit ``confirm`` checkbox schema.
EXPLICIT_CONFIRM_SITES = {"gmail_send_email", "gmail_reply"}

OPTIONAL_FLAGS = ("ENABLE_APPS_DASHBOARD", "ENABLE_CHAT", "ENABLE_GEMINI", "ENABLE_KEEP", "ENABLE_MEET")


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
