"""The consolidated Gmail tools (2.0.0) and server-side label-name resolution.

Each merged tool is driven through the in-memory client with a recording fake
of the Gmail service, so every test sees exactly which Google calls ran. The
retired tools' capabilities are each reached through their replacement here:
mark read/unread, archive and file, spam and not spam, restore from Trash
(gmail_modify_messages); trash and permanent deletion of one message, many
messages or a thread (gmail_delete); thread listing (gmail_search_emails with
group_by_thread); attachment download IDs (gmail_read_emails); drafts, labels,
filters, forwarding and the vacation responder.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

import mcp_google_workspace.gmail.tools.drafts as gmail_drafts
import mcp_google_workspace.gmail.tools.filters as gmail_filters
import mcp_google_workspace.gmail.tools.labels as gmail_labels
import mcp_google_workspace.gmail.tools.message_state as gmail_state
import mcp_google_workspace.gmail.tools.messages as gmail_messages
import mcp_google_workspace.gmail.tools.search as gmail_search
import mcp_google_workspace.gmail.tools.settings as gmail_settings
from mcp_google_workspace.common import confirmation
from mcp_google_workspace.common.errors import RecoverableToolError
from mcp_google_workspace.gmail.label_resolver import LabelResolver
from mcp_google_workspace.gmail.server import gmail_mcp

_LABELS = [
    {"id": "INBOX", "name": "INBOX", "type": "system"},
    {"id": "STARRED", "name": "STARRED", "type": "system"},
    {"id": "Label_1", "name": "Clients/Acme", "type": "user"},
    {"id": "Label_2", "name": "Receipts", "type": "user"},
    {"id": "Label_3", "name": "Travel", "type": "user"},
    {"id": "Label_4", "name": "travel", "type": "user"},
]


class _Call:
    def __init__(self, path: str, kwargs: dict[str, Any]) -> None:
        self.path = path
        self.kwargs = kwargs


class _FakeService:
    def __init__(self, path: tuple[str, ...] = ()) -> None:
        self._path = path

    def __getattr__(self, name: str) -> Any:
        def method(**kwargs: Any) -> Any:
            if kwargs:
                return _Call(".".join((*self._path, name)), kwargs)
            return _FakeService((*self._path, name))

        return method


class _Gmail:
    """Records each executed call and answers from ``responses`` by path."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.calls: list[_Call] = []
        self.responses = {"users.labels.list": {"labels": _LABELS}, **(responses or {})}

    def paths(self) -> list[str]:
        return [call.path for call in self.calls]

    def calls_to(self, path: str) -> list[_Call]:
        return [call for call in self.calls if call.path == path]

    async def execute(self, call: _Call) -> Any:
        self.calls.append(call)
        response = self.responses.get(call.path, {})
        return response(call) if callable(response) else response


class _Confirmations:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.asked: list[tuple[str, str]] = []

    async def __call__(self, _ctx: Any, action_name: str, message: str, **_kwargs: Any) -> bool:
        self.asked.append((action_name, message))
        return self.answer


async def _utc() -> str:
    return "UTC"


def _call(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    gmail: _Gmail,
    name: str,
    arguments: dict[str, Any],
    *,
    confirm: _Confirmations | None = None,
) -> dict[str, Any]:
    monkeypatch.setattr(module, "gmail_service", _FakeService)
    monkeypatch.setattr(module, "execute_google_request", gmail.execute)
    if hasattr(module, "resolve_user_timezone"):
        monkeypatch.setattr(module, "resolve_user_timezone", _utc)
    if confirm is not None:
        # The consent gate around every tool asks through the confirmation adapter.
        monkeypatch.setattr(confirmation, "confirm_destructive_action", confirm)

    async def scenario() -> dict[str, Any]:
        async with Client(gmail_mcp) as client:
            result = await client.call_tool(name, arguments)
            return result.structured_content or result.data

    return asyncio.run(scenario())


def _modify(monkeypatch: pytest.MonkeyPatch, arguments: dict[str, Any], responses: dict[str, Any] | None = None):
    gmail = _Gmail(
        {
            "users.messages.modify": lambda call: {
                "id": call.kwargs["id"],
                "threadId": "t1",
                "labelIds": call.kwargs["body"]["addLabelIds"],
            },
            **(responses or {}),
        }
    )
    return gmail, _call(monkeypatch, gmail_state, gmail, "modify_messages", arguments)


# --- label resolution ---------------------------------------------------------


def _resolve(values: list[str]) -> tuple[list[str], int]:
    gmail = _Gmail()

    async def scenario() -> list[str]:
        return await LabelResolver(_FakeService(), gmail.execute).resolve_all(values)

    resolved = asyncio.run(scenario())
    return resolved, len(gmail.calls_to("users.labels.list"))


def test_label_names_ids_and_system_labels_resolve_with_one_list_call() -> None:
    resolved, list_calls = _resolve(["Clients/Acme", "Label_2", "starred", "receipts", "Inbox"])
    assert resolved == ["Label_1", "Label_2", "STARRED", "INBOX"]
    assert list_calls == 1


def test_system_labels_resolve_case_insensitively_without_listing_labels() -> None:
    resolved, list_calls = _resolve(["unread", "Spam", "TRASH", "category_promotions"])
    assert resolved == ["UNREAD", "SPAM", "TRASH", "CATEGORY_PROMOTIONS"]
    assert list_calls == 0


def test_an_exact_name_wins_over_a_case_insensitive_one() -> None:
    assert _resolve(["Travel"])[0] == ["Label_3"]
    assert _resolve(["travel"])[0] == ["Label_4"]


def test_an_unknown_label_is_not_found_with_candidates_and_is_never_created() -> None:
    gmail = _Gmail()

    async def scenario() -> None:
        await LabelResolver(_FakeService(), gmail.execute).resolve("Acme")

    with pytest.raises(RecoverableToolError) as raised:
        asyncio.run(scenario())
    assert raised.value.error_code == "not_found"
    assert raised.value.details["candidates"] == [{"id": "Label_1", "name": "Clients/Acme"}]
    assert raised.value.required_action == {"tool": "gmail_list_labels", "arguments": {}}
    assert gmail.paths() == ["users.labels.list"]


def test_an_ambiguous_label_name_is_not_found_with_every_match() -> None:
    gmail = _Gmail()

    async def scenario() -> None:
        await LabelResolver(_FakeService(), gmail.execute).resolve("TRAVEL")

    with pytest.raises(RecoverableToolError) as raised:
        asyncio.run(scenario())
    assert raised.value.error_code == "not_found"
    assert {item["id"] for item in raised.value.details["candidates"]} == {"Label_3", "Label_4"}


def test_modify_messages_resolves_names_once_per_call_and_fails_before_changing_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gmail, result = _modify(
        monkeypatch,
        {"message_ids": ["m1"], "add_labels": ["Clients/Acme", "Receipts"], "remove_labels": ["Label_3"]},
    )
    assert gmail.paths() == ["users.labels.list", "users.messages.modify"]
    assert gmail.calls[1].kwargs["body"] == {"addLabelIds": ["Label_1", "Label_2"], "removeLabelIds": ["Label_3"]}
    assert result["added_label_ids"] == ["Label_1", "Label_2"]

    unknown = _Gmail()
    with pytest.raises(ToolError, match="No Gmail label"):
        _call(monkeypatch, gmail_state, unknown, "modify_messages", {"message_ids": ["m1"], "add_labels": ["Nope"]})
    assert unknown.paths() == ["users.labels.list"]


# --- gmail_modify_messages -----------------------------------------------------


@pytest.mark.parametrize(
    ("arguments", "body"),
    [
        # mark_as_read / mark_as_unread
        ({"mark_read": True}, {"addLabelIds": [], "removeLabelIds": ["UNREAD"]}),
        ({"mark_read": False}, {"addLabelIds": ["UNREAD"], "removeLabelIds": []}),
        # move_email: archive, back to the inbox, file under a label
        ({"archive": True}, {"addLabelIds": [], "removeLabelIds": ["INBOX"]}),
        ({"archive": False}, {"addLabelIds": ["INBOX"], "removeLabelIds": []}),
        ({"add_labels": ["Receipts"], "archive": True}, {"addLabelIds": ["Label_2"], "removeLabelIds": ["INBOX"]}),
        # apply_labels: star
        ({"add_labels": ["STARRED"]}, {"addLabelIds": ["STARRED"], "removeLabelIds": []}),
        # mark_as_spam / mark_as_not_spam (add_to_inbox true and false)
        ({"spam": True}, {"addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX"]}),
        ({"spam": False}, {"addLabelIds": ["INBOX"], "removeLabelIds": ["SPAM"]}),
        ({"spam": False, "archive": True}, {"addLabelIds": [], "removeLabelIds": ["INBOX", "SPAM"]}),
    ],
)
def test_modify_messages_covers_each_retired_single_message_tool(
    monkeypatch: pytest.MonkeyPatch, arguments: dict[str, Any], body: dict[str, Any]
) -> None:
    gmail, result = _modify(monkeypatch, {"message_ids": ["m1"], **arguments})
    modify = gmail.calls_to("users.messages.modify")
    assert len(modify) == 1 and modify[0].kwargs["id"] == "m1"
    assert modify[0].kwargs["body"] == body
    assert result["status"] == "ok"
    assert result["messages"] == [{"id": "m1", "thread_id": "t1", "label_ids": body["addLabelIds"]}]


def test_modify_messages_on_many_messages_is_one_batch_modify(monkeypatch: pytest.MonkeyPatch) -> None:
    gmail, result = _modify(monkeypatch, {"message_ids": ["m1", "m2", "m2"], "mark_read": True})
    assert gmail.paths() == ["users.messages.batchModify"]
    assert gmail.calls[0].kwargs["body"] == {"ids": ["m1", "m2"], "addLabelIds": [], "removeLabelIds": ["UNREAD"]}
    assert result["processed"] == 2 and result["messages"] == []


def test_modify_messages_on_a_thread_changes_every_message(monkeypatch: pytest.MonkeyPatch) -> None:
    thread = {"id": "t1", "messages": [{"id": "a", "threadId": "t1", "labelIds": []}, {"id": "b", "threadId": "t1"}]}
    gmail, result = _modify(monkeypatch, {"thread_id": "t1", "archive": True}, {"users.threads.modify": thread})
    assert gmail.paths() == ["users.threads.modify"]
    assert gmail.calls[0].kwargs["body"] == {"addLabelIds": [], "removeLabelIds": ["INBOX"]}
    assert result["thread_id"] == "t1" and result["processed"] == 2
    assert [item["id"] for item in result["messages"]] == ["a", "b"]


def test_modify_messages_restores_messages_and_threads_from_trash(monkeypatch: pytest.MonkeyPatch) -> None:
    restored = {"users.messages.untrash": {"id": "m1", "threadId": "t1", "labelIds": ["INBOX"]}}
    gmail, result = _modify(monkeypatch, {"message_ids": ["m1"], "untrash": True}, restored)
    assert gmail.paths() == ["users.messages.untrash"]
    assert result["untrashed"] is True and result["messages"][0]["label_ids"] == ["INBOX"]

    gmail, _ = _modify(monkeypatch, {"message_ids": ["m1", "m2"], "untrash": True, "mark_read": True}, restored)
    assert gmail.paths() == ["users.messages.untrash", "users.messages.untrash", "users.messages.batchModify"]

    gmail, _ = _modify(monkeypatch, {"thread_id": "t1", "untrash": True}, {"users.threads.untrash": {"id": "t1"}})
    assert gmail.paths() == ["users.threads.untrash"]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"message_ids": ["m1"], "thread_id": "t1", "mark_read": True}, "exactly one of message_ids"),
        ({"mark_read": True}, "exactly one of message_ids"),
        ({"message_ids": ["m1"], "add_labels": ["INBOX"], "archive": True}, "added and removed"),
    ],
)
def test_modify_messages_rejects_invalid_combinations(
    monkeypatch: pytest.MonkeyPatch, arguments: dict[str, Any], message: str
) -> None:
    gmail = _Gmail()
    with pytest.raises(ToolError, match=message):
        _call(monkeypatch, gmail_state, gmail, "modify_messages", arguments)
    assert [path for path in gmail.paths() if path != "users.labels.list"] == []


# --- gmail_delete --------------------------------------------------------------


def test_delete_moves_one_or_many_messages_and_a_thread_to_trash_without_asking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    confirm = _Confirmations(True)
    gmail = _Gmail()
    one = _call(monkeypatch, gmail_state, gmail, "delete", {"message_ids": ["m1"]}, confirm=confirm)
    assert gmail.paths() == ["users.messages.trash"] and one["mode"] == "trash"

    gmail = _Gmail()
    many = _call(monkeypatch, gmail_state, gmail, "delete", {"message_ids": ["m1", "m2"]}, confirm=confirm)
    assert gmail.paths() == ["users.messages.trash", "users.messages.trash"] and many["processed"] == 2

    gmail = _Gmail({"users.threads.trash": {"id": "t1", "messages": [{"id": "a"}, {"id": "b"}]}})
    thread = _call(monkeypatch, gmail_state, gmail, "delete", {"thread_id": "t1"}, confirm=confirm)
    assert gmail.paths() == ["users.threads.trash"] and thread["processed"] == 2
    assert confirm.asked == []


@pytest.mark.parametrize(("count", "asks"), [(9, False), (10, True), (11, True)])
def test_trashing_ten_or_more_messages_asks_first(monkeypatch: pytest.MonkeyPatch, count: int, asks: bool) -> None:
    declined = _Confirmations(False)
    gmail = _Gmail()
    ids = [f"m{index}" for index in range(count)]
    result = _call(monkeypatch, gmail_state, gmail, "delete", {"message_ids": ids}, confirm=declined)
    if asks:
        assert [action for action, _ in declined.asked] == ["batch_trash"]
        assert f"Move {count} Gmail messages" in declined.asked[0][1]
        assert result["status"] == "cancelled" and gmail.paths() == []
    else:
        assert declined.asked == []
        assert gmail.paths() == ["users.messages.trash"] * count


@pytest.mark.parametrize(
    ("arguments", "action_name", "prompt", "google_call"),
    [
        (
            {"message_ids": ["m1"]},
            "delete_email",
            "Permanently delete 1 Gmail message (m1)? This cannot be undone.",
            "users.messages.delete",
        ),
        (
            {"message_ids": ["m1", "m2"]},
            "batch_delete",
            "Permanently delete 2 Gmail messages (m1, m2)? This cannot be undone.",
            "users.messages.batchDelete",
        ),
        (
            {"thread_id": "t1"},
            "delete_thread",
            "Permanently delete every message in Gmail thread t1? This cannot be undone.",
            "users.threads.delete",
        ),
    ],
)
def test_permanent_delete_asks_with_its_exact_target(
    monkeypatch: pytest.MonkeyPatch,
    arguments: dict[str, Any],
    action_name: str,
    prompt: str,
    google_call: str,
) -> None:
    declined = _Confirmations(False)
    gmail = _Gmail()
    result = _call(monkeypatch, gmail_state, gmail, "delete", {**arguments, "mode": "permanent"}, confirm=declined)
    assert declined.asked == [(action_name, prompt)]
    assert result["status"] == "cancelled" and gmail.paths() == []

    accepted = _Confirmations(True)
    gmail = _Gmail()
    result = _call(monkeypatch, gmail_state, gmail, "delete", {**arguments, "mode": "permanent"}, confirm=accepted)
    assert accepted.asked == [(action_name, prompt)]
    assert result["status"] == "ok" and gmail.paths() == [google_call]


# --- search, read, attachments ------------------------------------------------


def _thread(thread_id: str, count: int) -> dict[str, Any]:
    return {
        "id": thread_id,
        "messages": [
            {
                "id": f"{thread_id}-{index}",
                "threadId": thread_id,
                "labelIds": ["INBOX"],
                "snippet": f"message {index}",
                "payload": {"headers": [{"name": "From", "value": "Ana <ana@example.com>"}, {"name": "Subject", "value": thread_id}]},
            }
            for index in range(count)
        ],
    }


def test_search_group_by_thread_lists_conversations_with_label_names(monkeypatch: pytest.MonkeyPatch) -> None:
    gmail = _Gmail(
        {
            "users.threads.list": {"threads": [{"id": "t1"}, {"id": "t2"}], "resultSizeEstimate": 2, "nextPageToken": "n"},
            "users.threads.get": lambda call: _thread(call.kwargs["id"], 2 if call.kwargs["id"] == "t1" else 1),
        }
    )
    result = _call(
        monkeypatch,
        gmail_search,
        gmail,
        "search_emails",
        {"query": "invoice", "labels": ["Receipts", "inbox"], "group_by_thread": True},
    )
    listed = gmail.calls_to("users.threads.list")[0]
    assert listed.kwargs["labelIds"] == ["Label_2", "INBOX"] and listed.kwargs["q"] == "(invoice)"
    assert all("body(attachmentId,size)" in call.kwargs["fields"] for call in gmail.calls_to("users.threads.get"))
    assert [(item["id"], item["message_count"]) for item in result["threads"]] == [("t1-1", 2), ("t2-0", 1)]
    assert result["count"] == 2 and result["next_page_token"] == "n" and result["has_more"] is True
    assert "users.messages.list" not in gmail.paths()


def test_read_emails_attachments_carry_the_download_id(monkeypatch: pytest.MonkeyPatch) -> None:
    message = {
        "id": "m1",
        "threadId": "t1",
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [{"name": "From", "value": "Ana <ana@example.com>"}],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": "SGk="}},
                {"mimeType": "application/pdf", "filename": "invoice.pdf", "body": {"attachmentId": "att-1", "size": 42}},
            ],
        },
    }
    gmail = _Gmail({"users.messages.get": message})
    result = _call(monkeypatch, gmail_messages, gmail, "read_emails", {"message_ids": ["m1"], "format": "metadata"})
    assert result["messages"][0]["attachments"] == [
        {"filename": "invoice.pdf", "mime_type": "application/pdf", "size": 42, "download_id": "att-1"}
    ]


# --- drafts ---------------------------------------------------------------------


def test_save_draft_creates_or_rewrites_and_list_drafts_reads_one(monkeypatch: pytest.MonkeyPatch) -> None:
    gmail = _Gmail(
        {
            "users.drafts.create": {"id": "d1", "message": {"id": "m1", "threadId": "t1"}},
            "users.drafts.update": {"id": "d1", "message": {"id": "m2", "threadId": "t1"}},
            "users.drafts.list": {"drafts": [{"id": "d1", "message": {"id": "m2", "threadId": "t1"}}], "resultSizeEstimate": 1},
            "users.drafts.get": {"id": "d1", "message": {"id": "m2", "threadId": "t1", "labelIds": ["DRAFT"], "snippet": "Hi"}},
        }
    )
    created = _call(monkeypatch, gmail_drafts, gmail, "save_draft", {"subject": "Hello", "to": ["a@example.com"], "text_body": "Hi"})
    assert created["mode"] == "created" and created["draft"]["id"] == "d1"
    updated = _call(monkeypatch, gmail_drafts, gmail, "save_draft", {"draft_id": "d1", "subject": "Hello again"})
    assert updated["mode"] == "updated"
    assert gmail.calls_to("users.drafts.update")[0].kwargs["id"] == "d1"

    listed = _call(monkeypatch, gmail_drafts, gmail, "list_drafts", {})
    assert listed["drafts"] == [{"id": "d1", "message_id": "m2", "thread_id": "t1"}]
    one = _call(monkeypatch, gmail_drafts, gmail, "list_drafts", {"draft_id": "d1", "format": "metadata"})
    assert one["count"] == 1 and one["drafts"][0]["label_ids"] == ["DRAFT"] and one["has_more"] is False
    assert gmail.calls_to("users.drafts.get")[0].kwargs["format"] == "metadata"


# --- labels, filters, forwarding, vacation --------------------------------------


def test_manage_labels_creates_updates_by_name_and_deletes_after_confirming(monkeypatch: pytest.MonkeyPatch) -> None:
    gmail = _Gmail({"users.labels.create": {"id": "Label_9", "name": "New"}, "users.labels.patch": {"id": "Label_2"}})
    created = _call(monkeypatch, gmail_labels, gmail, "manage_labels", {"action": "create", "name": "New", "text_color": "#ffffff"})
    assert created["label"]["id"] == "Label_9"
    assert gmail.calls_to("users.labels.create")[0].kwargs["body"] == {"name": "New", "color": {"textColor": "#ffffff"}}

    _call(monkeypatch, gmail_labels, gmail, "manage_labels", {"action": "update", "label": "Receipts", "name": "Bills"})
    assert gmail.calls_to("users.labels.patch")[0].kwargs["id"] == "Label_2"

    declined = _Confirmations(False)
    gmail = _Gmail()
    result = _call(monkeypatch, gmail_labels, gmail, "manage_labels", {"action": "delete", "label": "Receipts"}, confirm=declined)
    assert declined.asked == [("delete_label", "Delete Gmail label Receipts? Messages keep their other labels.")]
    assert result["status"] == "cancelled" and gmail.calls_to("users.labels.delete") == []

    accepted = _Confirmations(True)
    result = _call(monkeypatch, gmail_labels, gmail, "manage_labels", {"action": "delete", "label": "Label_2"}, confirm=accepted)
    assert result == {"status": "ok", "action": "delete", "label_id": "Label_2"}
    assert gmail.calls_to("users.labels.delete")[0].kwargs["id"] == "Label_2"


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"action": "create"}, "requires name"),
        ({"action": "update", "name": "x"}, "requires label"),
        ({"action": "delete"}, "requires label"),
        ({"action": "update", "label": "Receipts"}, "at least one"),
    ],
)
def test_manage_labels_names_the_missing_parameter(
    monkeypatch: pytest.MonkeyPatch, arguments: dict[str, Any], message: str
) -> None:
    # A delete asks first (the gate runs before the tool body); nothing changes either way.
    gmail = _Gmail()
    with pytest.raises(ToolError, match=message):
        _call(monkeypatch, gmail_labels, gmail, "manage_labels", arguments, confirm=_Confirmations(True))
    assert gmail.calls_to("users.labels.delete") == []


def test_manage_filters_lists_creates_with_label_names_and_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    gmail = _Gmail({"users.settings.filters.list": {"filter": [{"id": "f1"}]}, "users.settings.filters.create": {"id": "f2"}})
    listed = _call(monkeypatch, gmail_filters, gmail, "manage_filters", {"action": "list"})
    assert listed["filters"] == [{"id": "f1"}] and listed["count"] == 1

    created = _call(
        monkeypatch,
        gmail_filters,
        gmail,
        "manage_filters",
        {
            "action": "create",
            "criteria": {"from": "billing@example.com"},
            "filter_action": {"add_labels": ["Receipts"], "remove_labels": ["inbox"]},
        },
    )
    assert created["filter"] == {"id": "f2"}
    assert gmail.calls_to("users.settings.filters.create")[0].kwargs["body"] == {
        "criteria": {"from": "billing@example.com"},
        "action": {"addLabelIds": ["Label_2"], "removeLabelIds": ["INBOX"]},
    }

    confirm = _Confirmations(True)
    deleted = _call(monkeypatch, gmail_filters, gmail, "manage_filters", {"action": "delete", "filter_id": "f1"}, confirm=confirm)
    assert confirm.asked == [("delete_filter", "Delete Gmail filter f1?")]
    assert deleted["status"] == "ok" and gmail.calls_to("users.settings.filters.delete")[0].kwargs["id"] == "f1"


def test_manage_forwarding_covers_list_get_create_and_confirmed_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    address = {"forwardingEmail": "archive@example.com", "verificationStatus": "accepted"}
    gmail = _Gmail(
        {
            "users.settings.forwardingAddresses.list": {"forwardingAddresses": [address]},
            "users.settings.forwardingAddresses.get": address,
            "users.settings.forwardingAddresses.create": {**address, "verificationStatus": "pending"},
        }
    )
    assert _call(monkeypatch, gmail_settings, gmail, "manage_forwarding", {"action": "list"})["count"] == 1
    got = _call(monkeypatch, gmail_settings, gmail, "manage_forwarding", {"action": "get", "forwarding_email": "archive@example.com"})
    assert got["forwarding_address"] == address
    accepted = _Confirmations(True)
    made = _call(
        monkeypatch,
        gmail_settings,
        gmail,
        "manage_forwarding",
        {"action": "create", "forwarding_email": "archive@example.com"},
        confirm=accepted,
    )
    assert accepted.asked == [
        (
            "create_forwarding_address",
            "Add archive@example.com as a Gmail forwarding address? Gmail may email it a verification link.",
        )
    ]
    assert made["forwarding_address"]["verificationStatus"] == "pending"
    declined = _Confirmations(False)
    result = _call(
        monkeypatch,
        gmail_settings,
        gmail,
        "manage_forwarding",
        {"action": "delete", "forwarding_email": "archive@example.com"},
        confirm=declined,
    )
    assert declined.asked == [("delete_forwarding_address", "Delete Gmail forwarding address archive@example.com?")]
    assert result["status"] == "cancelled"
    assert gmail.calls_to("users.settings.forwardingAddresses.delete") == []
    with pytest.raises(ToolError, match="requires forwarding_email"):
        _call(monkeypatch, gmail_settings, gmail, "manage_forwarding", {"action": "get"})


def test_manage_vacation_reads_and_updates_the_auto_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    gmail = _Gmail({"users.settings.getVacation": {"enableAutoReply": False}, "users.settings.updateVacation": {"enableAutoReply": True}})
    assert _call(monkeypatch, gmail_settings, gmail, "manage_vacation", {"action": "get"})["vacation"] == {"enableAutoReply": False}
    accepted = _Confirmations(True)
    updated = _call(
        monkeypatch,
        gmail_settings,
        gmail,
        "manage_vacation",
        {"action": "update", "enable_auto_reply": True, "response_subject": "Away", "end_time": 1},
        confirm=accepted,
    )
    # Turning the auto-reply on asks first; turning it off does not.
    assert [action for action, _ in accepted.asked] == ["update_vacation"]
    assert accepted.asked[0][1].startswith("Turn on the Gmail auto-reply?")
    assert "Subject: Away" in accepted.asked[0][1]
    assert updated["status"] == "ok"
    off = _call(monkeypatch, gmail_settings, gmail, "manage_vacation", {"action": "update"}, confirm=accepted)
    assert off["status"] == "ok" and len(accepted.asked) == 1
    assert gmail.calls_to("users.settings.updateVacation")[0].kwargs["body"] == {
        "enableAutoReply": True,
        "restrictToContacts": False,
        "restrictToDomain": False,
        "responseSubject": "Away",
        "endTime": 1,
    }
