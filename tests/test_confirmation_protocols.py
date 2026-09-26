"""Confirmation gates on both MCP protocol eras.

All 24 confirmation sites (the 14 former direct ``ctx.elicit`` calls, one of
which was the shared helper, plus the 10 other callers of
``confirm_destructive_action``) now go through one gate in
``common.async_ops``:

* MCP 2026-07-28 has no server-initiated requests, so ``ctx.elicit`` is
  unavailable. Until W4 adds the multi-round-trip ``InputRequiredResult``
  branch, a modern request fails closed: an ``isError`` tool result carrying
  the ``confirmation_required`` envelope and the exact prompt, and no Google
  mutation, even for a client that would have accepted.
* Handshake-era (legacy) requests keep imperative elicitation: a decline
  mutates nothing and an accept reaches the mutation.

Google is replaced at the single ``_build_service_now`` seam with a recorder,
so every executed request (read or write) is observed without network access.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from typing import Any

import anyio
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.elicitation import ElicitResult

import mcp_google_workspace.auth.google_auth as google_auth
import mcp_google_workspace.server as server_module

# Google API method-name prefixes that change provider state (deleteContact,
# batchDelete, modify, send, ...). Reads are get/list/getProfile.
_MUTATING_PREFIXES = ("batch", "create", "delete", "insert", "modify", "patch", "send", "trash", "update")

_CANNED_RESPONSES: dict[str, dict[str, Any]] = {
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
}


class _GoogleRecorder:
    """Stand-in Google API client that records each executed method chain."""

    def __init__(self, calls: list[str], path: tuple[str, ...] = ()) -> None:
        self._calls = calls
        self._path = path

    def __getattr__(self, name: str) -> "_GoogleRecorder":
        if name.startswith("__"):
            raise AttributeError(name)
        return _GoogleRecorder(self._calls, (*self._path, name))

    def __call__(self, *_args: Any, **_kwargs: Any) -> "_GoogleRecorder":
        return self

    def execute(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        method = ".".join(self._path)
        self._calls.append(method)
        return dict(_CANNED_RESPONSES.get(method, {}))


# (tool, arguments) for every confirmation site. Arguments select the branch
# that requires confirmation (e.g. permanent deletion, notify=True).
CONFIRMATION_SITES: list[tuple[str, dict[str, Any]]] = [
    # 13 direct ctx.elicit call sites (the 14th is the shared helper below).
    ("calendar_delete_event", {"event_id": "e1"}),
    ("chat_create_message", {"request": {"space_name": "spaces/A", "text": "hi", "notify": True}}),
    ("chat_delete_message", {"request": {"message_name": "spaces/A/messages/B"}}),
    ("chat_post_message_simple", {"request": {"space_name": "spaces/A", "text": "hi", "notify": True}}),
    (
        "chat_reply_to_message",
        {"request": {"message_name": "spaces/A/messages/B", "text": "hi", "notify": True}},
    ),
    ("drive_delete_file", {"file_id": "f1", "delete_mode": "permanent", "confirm_permanent": True}),
    ("gmail_batch_delete", {"message_ids": ["m1"], "permanent": True}),
    (
        "gmail_send_email",
        {"to": ["to@example.com"], "subject": "s", "text_body": "b", "confirm_send": True},
    ),
    ("gmail_reply_email", {"message_id": "m1", "text_body": "b", "confirm_send": True}),
    ("gmail_delete_email", {"message_id": "m1", "permanent": True}),
    ("gmail_delete_thread", {"thread_id": "t1"}),
    ("keep_create_note", {"request": {"title": "t", "text_body": "b", "confirm_create": True}}),
    ("keep_delete_note", {"request": {"note_name": "notes/n1", "confirm_delete": True}}),
    # 10 callers of common.async_ops.confirm_destructive_action (elicit site 14).
    ("calendar_remove_event_attachment", {"event_id": "e1", "file_id": "f1"}),
    (
        "drive_create_permission",
        {"file_id": "f1", "role": "reader", "type": "user", "email_address": "a@example.com"},
    ),
    ("drive_update_permission", {"file_id": "f1", "permission_id": "p1", "role": "reader"}),
    ("drive_delete_permission", {"file_id": "f1", "permission_id": "p1"}),
    ("gmail_delete_draft", {"draft_id": "d1"}),
    ("gmail_delete_filter", {"filter_id": "f1"}),
    ("gmail_delete_label", {"label_id": "Label_1"}),
    ("gmail_delete_forwarding_address", {"forwarding_email": "f@example.com"}),
    ("people_delete_contact", {"person_name": "people/c1"}),
    ("tasks_delete_task", {"tasklist_id": "l1", "task_id": "t1"}),
]

_OPTIONAL_FLAGS = ("ENABLE_APPS_DASHBOARD", "ENABLE_CHAT", "ENABLE_GEMINI", "ENABLE_KEEP", "ENABLE_MEET")


@pytest.fixture(scope="module")
def workspace() -> Iterator[FastMCP]:
    """Root composition with Chat and Keep mounted, restored afterwards."""
    previous = {name: os.environ.get(name) for name in _OPTIONAL_FLAGS}
    for name in _OPTIONAL_FLAGS:
        os.environ.pop(name, None)
    os.environ["ENABLE_CHAT"] = "true"
    os.environ["ENABLE_KEEP"] = "true"
    try:
        yield importlib.reload(server_module).workspace_mcp
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        importlib.reload(server_module)


@pytest.fixture
def google_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        google_auth, "_build_service_now", lambda *_args, **_kwargs: _GoogleRecorder(calls)
    )
    return calls


def _mutations(calls: list[str]) -> list[str]:
    return [
        call for call in calls if call.rsplit(".", 1)[-1].startswith(_MUTATING_PREFIXES)
    ]


async def _invoke(
    server: FastMCP,
    tool: str,
    arguments: dict[str, Any],
    *,
    mode: str,
    accept: bool,
    prompts: list[str],
) -> Any:
    async def handler(message: str, _type: Any, params: Any, _context: Any) -> Any:
        prompts.append(message)
        if not accept:
            return ElicitResult(action="decline")
        return {name: True for name in params.requested_schema.get("properties", {})}

    async with Client(server, mode=mode, elicitation_handler=handler) as client:  # type: ignore[arg-type]
        expected = "2026-07-28" if mode == "auto" else "2025-11-25"
        assert client.protocol_version == expected
        return await client.call_tool(tool, arguments, raise_on_error=False)


# W4: MRTR - replace with accept/decline/cancel/tamper rounds once the
# InputRequiredResult confirmation branch exists.
@pytest.mark.parametrize(("tool", "arguments"), CONFIRMATION_SITES, ids=[s[0] for s in CONFIRMATION_SITES])
def test_modern_confirmation_fails_closed_with_tool_result_and_no_mutation(
    workspace: FastMCP, google_calls: list[str], tool: str, arguments: dict[str, Any]
) -> None:
    prompts: list[str] = []
    result = anyio.run(
        lambda: _invoke(workspace, tool, arguments, mode="auto", accept=True, prompts=prompts)
    )

    # The accepting client is never asked: 2026-07-28 has no back-channel.
    assert prompts == []
    assert result.is_error is True
    envelope = result.structured_content
    assert envelope["code"] == "confirmation_required"
    assert envelope["retryable"] is False
    action = envelope["required_action"]
    assert action["action"] == "request_host_confirmation"
    assert action["operation"] == tool.split("_", 1)[1]
    assert action["prompt"]
    assert "No changes were made" in envelope["message"]
    assert "confirmation_required" in result.content[0].text
    assert _mutations(google_calls) == []


# W4: legacy (handshake-era) connections keep imperative elicitation through
# the same gate; the adapter will keep this as its thin legacy branch.
@pytest.mark.parametrize(("tool", "arguments"), CONFIRMATION_SITES, ids=[s[0] for s in CONFIRMATION_SITES])
def test_legacy_declined_confirmation_executes_no_mutation(
    workspace: FastMCP, google_calls: list[str], tool: str, arguments: dict[str, Any]
) -> None:
    prompts: list[str] = []
    result = anyio.run(
        lambda: _invoke(workspace, tool, arguments, mode="legacy", accept=False, prompts=prompts)
    )

    assert len(prompts) == 1
    assert result.is_error is False
    assert (result.structured_content or {}).get("status") == "cancelled"
    assert _mutations(google_calls) == []


# W4: see above.
@pytest.mark.parametrize(("tool", "arguments"), CONFIRMATION_SITES, ids=[s[0] for s in CONFIRMATION_SITES])
def test_legacy_accepted_confirmation_reaches_mutation(
    workspace: FastMCP, google_calls: list[str], tool: str, arguments: dict[str, Any]
) -> None:
    prompts: list[str] = []
    try:
        anyio.run(
            lambda: _invoke(workspace, tool, arguments, mode="legacy", accept=True, prompts=prompts)
        )
    except Exception:  # noqa: BLE001 - the recorder returns placeholder API payloads
        # Only whether the confirmed mutation was attempted matters here; the
        # placeholder provider response may not satisfy the tool's result schema.
        pass

    assert len(prompts) == 1
    assert _mutations(google_calls), google_calls


def test_only_the_shared_gate_uses_imperative_elicitation() -> None:
    """W4: keep ctx.elicit behind one seam so the adapter replaces it once."""
    from pathlib import Path

    import mcp_google_workspace

    root = Path(mcp_google_workspace.__file__).parent
    offenders = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if ".elicit(" in path.read_text(encoding="utf-8")
    )
    assert offenders == ["common/async_ops.py"]
