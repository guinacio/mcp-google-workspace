"""The consequential-call table: coverage, annotations, adversarial calls, modes.

``test_confirmation_protocols.py`` runs every table trigger through every
client kind. This module checks the table itself and tries to get a
consequential call executed without consent through its arguments.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastmcp import Client, Context, FastMCP

import mcp_google_workspace.auth.google_auth as google_auth
import mcp_google_workspace.auth.google_oauth as google_oauth
import mcp_google_workspace.server as server_module
from mcp_google_workspace.common import consent
from mcp_google_workspace.common.component_annotations import consent_hints
from mcp_google_workspace.common.consent import (
    BULK_THRESHOLD,
    CONSENT_TABLE,
    classify,
    committing,
    consent_mode,
)
from mcp_google_workspace.common.fastmcp_compat import local_tools
from mcp_google_workspace.common.operations import (
    memory_operation_store,
    operation_key,
    set_operation_store,
)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from contracts.confirmation_sites import (  # noqa: E402
    CONSENT_CASES,
    FULL_TABLE_ENV,
    OPTIONAL_FLAGS,
    GoogleRecorder,
    mutations,
)

_TRACKED = (*OPTIONAL_FLAGS, "MCP_RATE_LIMIT_PER_MINUTE", "MCP_TOOL_TIER", "MCP_CONSENT", "MCP_TOOL_SEARCH")


def _compose(env: dict[str, str]) -> FastMCP:
    for name in _TRACKED:
        os.environ.pop(name, None)
    os.environ.update({"MCP_RATE_LIMIT_PER_MINUTE": "100000", **env})
    return importlib.reload(server_module).workspace_mcp


@pytest.fixture(scope="module")
def workspace() -> Iterator[FastMCP]:
    previous = {name: os.environ.get(name) for name in _TRACKED}
    try:
        yield _compose(FULL_TABLE_ENV)
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        importlib.reload(server_module)


@pytest.fixture(autouse=True)
def operation_store() -> Iterator[Any]:
    store = memory_operation_store()
    set_operation_store(store)
    try:
        yield store
    finally:
        set_operation_store(None)


@pytest.fixture
def google_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: GoogleRecorder(calls))
    monkeypatch.setattr(
        google_oauth,
        "disconnect_google_account",
        lambda: calls.append("oauth.credentials.delete") or {"status": "disconnected"},
    )
    return calls


def _call(server: FastMCP, tool: str, arguments: dict[str, Any], **client: Any) -> Any:
    async def run() -> Any:
        async with Client(server, **client) as session:
            return await session.call_tool(tool, arguments, raise_on_error=False)

    return anyio.run(run)


def _complete_catalog() -> dict[str, Any]:
    previous = {name: os.environ.get(name) for name in _TRACKED}
    try:
        server = _compose(
            {**FULL_TABLE_ENV, "MCP_TOOL_TIER": "complete", "ENABLE_APPS_DASHBOARD": "true",
             "ENABLE_GEMINI": "true", "GEMINI_API_KEY": "test-key"}
        )
        tools = anyio.run(lambda: server.list_tools(run_middleware=False))
        return {tool.name: tool for tool in tools}
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        importlib.reload(server_module)


@pytest.fixture(scope="module")
def complete_catalog(workspace: FastMCP) -> dict[str, Any]:
    catalog = _complete_catalog()
    _compose(FULL_TABLE_ENV)  # the module's composition again
    return catalog


# ---------------------------------------------------------------------------
# The table covers every tool, and the annotations come from it
# ---------------------------------------------------------------------------


def test_every_tool_of_every_tier_is_classified_and_the_table_has_no_stale_rows(
    complete_catalog: dict[str, Any],
) -> None:
    assert sorted(set(complete_catalog) - set(CONSENT_TABLE)) == []
    assert sorted(set(CONSENT_TABLE) - set(complete_catalog)) == []


def test_annotations_are_derived_from_the_table(complete_catalog: dict[str, Any]) -> None:
    mismatched = {}
    for name, tool in complete_catalog.items():
        hints = tool.annotations
        published = (hints.read_only_hint, hints.destructive_hint, hints.idempotent_hint)
        expected = consent_hints(name)
        if published != (expected["read_only_hint"], expected["destructive_hint"], expected["idempotent_hint"]):
            mismatched[name] = published
    assert mismatched == {}


def test_every_tool_that_can_be_consequential_is_destructive_and_never_read_only(
    complete_catalog: dict[str, Any],
) -> None:
    for name, policy in CONSENT_TABLE.items():
        hints = complete_catalog[name].annotations
        if policy.may_be_consequential:
            assert hints.destructive_hint is True, name
            assert hints.read_only_hint is False, name
        if hints.read_only_hint:
            assert not policy.may_be_consequential and policy.base == "read", name
    # commit_workspace_action executes consequential calls: destructive, not gated itself.
    assert complete_catalog["commit_workspace_action"].annotations.destructive_hint is True


def test_every_trigger_has_a_case_that_selects_it() -> None:
    triggers = {
        (tool, trigger.action)
        for tool, policy in CONSENT_TABLE.items()
        for trigger in policy.triggers
    }
    assert triggers == set(CONSENT_CASES)
    for (tool, action), arguments in CONSENT_CASES.items():
        classification = classify(tool, arguments)
        assert classification.risk == "consequential", (tool, action)
        assert classification.trigger is not None and classification.trigger.action == action


def test_every_gated_tool_is_async_and_sees_its_request_context(workspace: FastMCP) -> None:
    """The guard gates async bodies only, and needs the Context to tell a task from a request."""
    from fastmcp.utilities.types import find_kwarg_by_type

    servers = [workspace, *(provider for provider in _mounted(workspace))]
    seen: set[str] = set()
    for server in servers:
        namespace = _namespace(server)
        for tool in local_tools(server):
            name = f"{namespace}_{tool.name}" if namespace else tool.name
            policy = CONSENT_TABLE.get(name)
            if policy is None or not (policy.may_be_consequential or policy.dry_run):
                continue
            original = inspect.unwrap(tool.fn)
            assert inspect.iscoroutinefunction(original), name
            assert find_kwarg_by_type(original, Context), name
            assert getattr(tool.fn, "_workspace_confirmation_guard", False) is True, name
            seen.add(name)
    assert seen == {name for name, policy in CONSENT_TABLE.items() if policy.may_be_consequential}


def _mounted(server: FastMCP) -> list[FastMCP]:
    from mcp_google_workspace import calendar, chat, docs, drive, forms, gmail, keep, meet, people, sheets, slides, tasks

    return [
        calendar.calendar_mcp, chat.chat_mcp, docs.docs_mcp, drive.drive_mcp, forms.forms_mcp, gmail.gmail_mcp,
        keep.keep_mcp, meet.meet_mcp, people.people_mcp, sheets.sheets_mcp, slides.slides_mcp, tasks.tasks_mcp,
    ]


def _namespace(server: FastMCP) -> str | None:
    prefix = server.name.partition("-")[0]
    return None if prefix == "google" else prefix


# ---------------------------------------------------------------------------
# No argument skips consent (property over the table)
# ---------------------------------------------------------------------------

#: Arguments whose value legitimately decides what happens (the class they
#: select is the class of what the call really does): trash vs permanent,
#: who is emailed, how many items, which action.
SEMANTIC = {
    "action", "mode", "delete_mode", "message_ids", "thread_id", "reply_all", "permission_id",
    "attendees", "send_updates", "recurrence", "remove", "add", "deleted", "enable_auto_reply",
    "filter_action", "requests", "config", "collaborator_emails", "apply_via_replacement",
    "delete_original_on_apply", "dry_run",
}


def _candidates(schema: dict[str, Any]) -> list[Any]:
    if "enum" in schema:
        return list(schema["enum"])
    kind = schema.get("type")
    if kind == "boolean":
        return [True, False]
    if kind == "integer":
        return [0, 1, 10_000]
    if kind == "string":
        return ["", "x", "none", "false"]
    if kind == "array":
        return [[]]
    return [None]


@pytest.mark.parametrize(("tool", "action"), sorted(CONSENT_CASES))
def test_no_other_argument_makes_a_consequential_call_free(
    complete_catalog: dict[str, Any], tool: str, action: str
) -> None:
    base = CONSENT_CASES[(tool, action)]
    schema = complete_catalog[tool].parameters
    properties = dict(schema.get("properties", {}))
    nested = base.get("request")
    if isinstance(nested, dict):
        request_schema = properties["request"]
        ref = request_schema.get("$ref", "").rsplit("/", 1)[-1]
        request_props = schema.get("$defs", {}).get(ref, request_schema).get("properties", {})
    for name, prop in properties.items():
        if name in SEMANTIC or name in base:
            continue
        for value in _candidates(prop):
            assert classify(tool, {**base, name: value}).risk == "consequential", (name, value)
    if isinstance(nested, dict):
        for name, prop in request_props.items():
            if name in SEMANTIC or name in nested:
                continue
            for value in _candidates(prop):
                varied = {**base, "request": {**nested, name: value}}
                assert classify(tool, varied).risk == "consequential", (name, value)


REMOVED_FLAGS = [
    ("gmail_send_email", {"to": ["a@example.com"], "subject": "s", "text_body": "b", "confirm_send": False}),
    ("gmail_reply", {"message_id": "m1", "text_body": "b", "confirm_send": False}),
    ("calendar_delete_event", {"event_id": "e1", "force": True}),
    ("drive_delete_file", {"file_id": "f1", "delete_mode": "permanent", "confirm_permanent": True}),
    ("disconnect_google_workspace", {"confirm": True}),
    ("chat_create_message", {"request": {"space_name": "spaces/A", "text": "hi", "notify": False}}),
    ("chat_post_message_simple", {"request": {"space_name": "spaces/A", "text": "hi", "notify": False}}),
    ("chat_reply_to_message", {"request": {"message_name": "spaces/A/messages/B", "text": "hi", "notify": False}}),
    ("chat_delete_message", {"request": {"message_name": "spaces/A/messages/B", "force": True}}),
    ("keep_create_note", {"request": {"title": "t", "collaborator_emails": ["c@example.com"], "confirm_create": False}}),
    ("keep_delete_note", {"request": {"note_name": "notes/n1", "confirm_delete": False}}),
    ("gmail_delete", {"message_ids": ["m1"], "mode": "permanent", "force": True}),
    ("drive_share_file", {"file_id": "f1", "type": "anyone", "role": "reader", "confirm": True}),
]


@pytest.mark.parametrize(("tool", "arguments"), REMOVED_FLAGS, ids=[tool for tool, _ in REMOVED_FLAGS])
def test_removed_safety_flags_are_rejected_by_the_closed_input_schema(
    workspace: FastMCP, google_calls: list[str], tool: str, arguments: dict[str, Any]
) -> None:
    result = _call(workspace, tool, arguments)
    assert result.is_error is True
    text = result.content[0].text
    assert "confirmation_required" not in text  # rejected as input, before any gate
    assert mutations(google_calls) == []


# ---------------------------------------------------------------------------
# Adversarial calls end to end (client without elicitation: nothing may run)
# ---------------------------------------------------------------------------


def _refused(result: Any) -> bool:
    return result.is_error is True and (result.structured_content or {}).get("code") == "confirmation_required"


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        # Mode defaults: no mode is trash (reversible), never permanent.
        ("gmail_delete", {"message_ids": ["m1"]}),
        ("drive_delete_file", {"file_id": "f1"}),
        # send_updates omitted means Google emails no one; a plain edit is free.
        ("calendar_update_event", {"event_id": "e1", "summary": "Renamed"}),
        # Below the bulk threshold.
        ("gmail_modify_messages", {"message_ids": [f"m{i}" for i in range(BULK_THRESHOLD - 1)], "mark_read": True}),
        ("gmail_delete", {"message_ids": [f"m{i}" for i in range(BULK_THRESHOLD - 1)]}),
        ("sheets_batch_update_spreadsheet", {"spreadsheet_id": "s1", "requests": [{"addSheet": {}}] * (BULK_THRESHOLD - 1)}),
        # Duplicated IDs are one message each: ten copies of one ID are one change.
        ("gmail_modify_messages", {"message_ids": ["m1"] * BULK_THRESHOLD, "mark_read": True}),
    ],
    ids=["delete-default-trash", "drive-default-trash", "update-no-notification", "modify-9", "trash-9",
         "batch-9-additive", "duplicate-ids"],
)
def test_reversible_variants_run_without_consent_and_do_only_the_reversible_thing(
    workspace: FastMCP, google_calls: list[str], tool: str, arguments: dict[str, Any]
) -> None:
    result = _call(workspace, tool, arguments)
    assert not _refused(result), result.content
    done = mutations(google_calls)
    assert not any(call.endswith((".delete", ".batchDelete")) for call in done), done


@pytest.mark.parametrize("count", [BULK_THRESHOLD, BULK_THRESHOLD + 1])
@pytest.mark.parametrize(
    ("tool", "build"),
    [
        ("gmail_modify_messages", lambda n: {"message_ids": [f"m{i}" for i in range(n)], "mark_read": True}),
        ("gmail_modify_messages", lambda n: {"message_ids": [f"m{i}" for i in range(n)], "untrash": True}),
        ("gmail_delete", lambda n: {"message_ids": [f"m{i}" for i in range(n)]}),
        ("sheets_batch_update_spreadsheet", lambda n: {"spreadsheet_id": "s1", "requests": [{"addSheet": {}}] * n}),
    ],
    ids=["modify", "restore", "trash", "batch-additive"],
)
def test_threshold_boundary_needs_consent(
    workspace: FastMCP, google_calls: list[str], tool: str, build: Any, count: int
) -> None:
    result = _call(workspace, tool, build(count))
    assert _refused(result), result.content
    assert mutations(google_calls) == []


@pytest.mark.parametrize(
    ("arguments", "fragment"),
    [
        ({"to": [], "subject": "s", "text_body": "b"}, "Recipients: 0 in total"),
        ({"to": [], "bcc": ["hidden@example.com"], "subject": "s"}, "Bcc: hidden@example.com"),
        ({"to": ["a@example.com"] * 20, "subject": "s"}, "Recipients: 1 in total"),
        ({"to": ["A@example.com", "a@example.com"], "subject": "s"}, "Recipients: 1 in total"),
        ({"to": [f"p{i}@example.com" for i in range(10)], "subject": "s"}, "This is a large send (10 recipients)."),
    ],
    ids=["empty", "bcc-only", "duplicates", "case-duplicates", "large"],
)
def test_every_send_needs_consent_and_previews_exact_recipients(
    workspace: FastMCP, google_calls: list[str], arguments: dict[str, Any], fragment: str
) -> None:
    result = _call(workspace, "gmail_send_email", arguments)
    assert _refused(result), result.content
    assert fragment in result.structured_content["details"]["preview"]
    assert mutations(google_calls) == []


@pytest.mark.parametrize("value", ["PERMANENT", "Permanent", "permanently", "hard"])
def test_mode_values_outside_the_enum_are_rejected(workspace: FastMCP, google_calls: list[str], value: str) -> None:
    result = _call(workspace, "gmail_delete", {"message_ids": ["m1"], "mode": value})
    assert result.is_error is True and not _refused(result)
    assert mutations(google_calls) == []


@pytest.mark.parametrize("send_updates", ["all", "externalOnly"])
def test_send_updates_that_email_guests_raise_the_class(
    workspace: FastMCP, google_calls: list[str], send_updates: str
) -> None:
    result = _call(workspace, "calendar_update_event", {"event_id": "e1", "summary": "x", "send_updates": send_updates})
    assert _refused(result)
    assert mutations(google_calls) == []


def test_the_call_tool_search_proxy_is_gated_like_a_direct_call(
    google_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_google_workspace.tool_discovery import _CONFIGURED_SERVERS, configure_tool_search

    previous = {name: os.environ.get(name) for name in _TRACKED}
    try:
        server = _compose({"MCP_TOOL_SEARCH": "on"})
        configure_tool_search(server)
        args = {"name": "gmail_send_email", "arguments": {"to": ["a@example.com"], "subject": "s"}}
        assert _refused(_call(server, "call_tool", args))
        assert mutations(google_calls) == []
    finally:
        _CONFIGURED_SERVERS.clear()
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        _compose(FULL_TABLE_ENV)


def test_splitting_a_bulk_change_into_smaller_calls_is_not_detected(
    workspace: FastMCP, google_calls: list[str]
) -> None:
    """Known limit: classification is per call. Two calls of nine run without consent."""
    for start in (0, BULK_THRESHOLD - 1):
        ids = [f"m{i}" for i in range(start, start + BULK_THRESHOLD - 1)]
        assert not _refused(_call(workspace, "gmail_modify_messages", {"message_ids": ids, "mark_read": True}))
    assert mutations(google_calls) == ["users.messages.batchModify", "users.messages.batchModify"]


# ---------------------------------------------------------------------------
# Prepare and commit reuse the same table
# ---------------------------------------------------------------------------

_SEND = {"to": ["a@example.com"], "subject": "s", "text_body": "b"}


def _prepared(server: FastMCP, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = _call(server, "prepare_workspace_action", {"tool_name": tool, "arguments": arguments})
    assert result.is_error is False, result.content
    return dict(result.structured_content)


def test_prepare_refuses_a_call_that_needs_no_consent(workspace: FastMCP, google_calls: list[str]) -> None:
    result = _call(
        workspace, "prepare_workspace_action", {"tool_name": "gmail_delete", "arguments": {"message_ids": ["m1"]}}
    )
    assert result.is_error is True
    assert result.structured_content["code"] == "invalid_input"
    assert result.structured_content["required_action"] == {"tool": "gmail_delete", "arguments": {"message_ids": ["m1"]}}
    never = _call(workspace, "prepare_workspace_action", {"tool_name": "gmail_list_labels", "arguments": {}})
    assert never.is_error is True and "never needs consent" in never.content[0].text
    assert mutations(google_calls) == []


def test_prepare_validates_the_arguments_like_a_direct_call(workspace: FastMCP, google_calls: list[str]) -> None:
    result = _call(
        workspace, "prepare_workspace_action", {"tool_name": "gmail_send_email", "arguments": {**_SEND, "confirm_send": True}}
    )
    assert result.is_error is True
    assert mutations(google_calls) == []


def test_a_prepared_token_cannot_be_committed_for_different_arguments(
    workspace: FastMCP, google_calls: list[str], operation_store: Any
) -> None:
    from mcp_google_workspace.auth.identity import current_principal

    token = _prepared(workspace, "gmail_send_email", _SEND)["commit_token"]
    key = operation_key(current_principal().storage_key, token)

    async def tamper() -> None:
        # Simulates a store whose stored arguments no longer match the digest
        # prepare bound (the token itself carries no arguments).
        def swap(record: dict[str, Any]) -> tuple[dict[str, Any], float, None]:
            return {**record, "arguments": {**_SEND, "to": ["attacker@example.com"]}}, 600, None

        await operation_store._update(key, swap)

    anyio.run(tamper)
    result = _call(workspace, "commit_workspace_action", {"commit_token": token})
    assert result.is_error is True
    assert result.structured_content["code"] == "confirmation_invalid"
    assert mutations(google_calls) == []


def test_a_commit_grant_covers_one_call_of_one_tool(workspace: FastMCP, google_calls: list[str]) -> None:
    """Consent never leaks from the committed call to any other call."""

    async def run() -> list[Any]:
        async with Client(workspace) as client:
            with committing("gmail_send_email", "not-the-digest-of-anything"):
                other = await client.call_tool(
                    "drive_unshare_file", {"file_id": "f1", "permission_id": "p1"}, raise_on_error=False
                )
                same = await client.call_tool("gmail_send_email", _SEND, raise_on_error=False)
                again = await client.call_tool("gmail_send_email", _SEND, raise_on_error=False)
        return [other, same, again]

    other, same, again = anyio.run(run)
    assert _refused(other)
    assert same.structured_content["code"] == "confirmation_invalid"  # digest mismatch
    assert _refused(again)  # the grant was spent
    assert mutations(google_calls) == []


def test_a_foreign_principal_cannot_commit_a_token(workspace: FastMCP, google_calls: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_google_workspace.auth import identity

    token = _prepared(workspace, "gmail_send_email", _SEND)["commit_token"]
    original = identity.current_principal

    def someone_else(*args: Any, **kwargs: Any) -> Any:
        principal = original(*args, **kwargs)
        return type(principal)(issuer=principal.issuer, subject="someone-else")

    monkeypatch.setattr("mcp_google_workspace.server.current_principal", someone_else)
    result = _call(workspace, "commit_workspace_action", {"commit_token": token})
    assert result.is_error is True and "invalid, expired" in result.content[0].text
    assert mutations(google_calls) == []


# ---------------------------------------------------------------------------
# dry_run
# ---------------------------------------------------------------------------

DRY_RUNS = [
    ("gmail_modify_messages", {"message_ids": [f"m{i}" for i in range(12)], "add_labels": ["Work"]}, "Change 12 Gmail messages"),
    ("gmail_modify_messages", {"message_ids": ["m1"], "mark_read": True}, "Change 1 Gmail message (m1): mark read?"),
    ("gmail_delete", {"thread_id": "t1", "mode": "permanent"}, "Permanently delete every message in Gmail thread t1"),
    ("gmail_delete", {"message_ids": ["m1", "m2"]}, "Move 2 Gmail messages (m1, m2) to Trash?"),
    ("docs_batch_update_document", {"document_id": "d1", "requests": [{"insertText": {}}]}, "1 insertText"),
    ("sheets_batch_update_spreadsheet", {"spreadsheet_id": "s1", "requests": [{"deleteSheet": {}}, {"addSheet": {}}]}, "1 addSheet, 1 deleteSheet"),
    ("slides_batch_update_presentation", {"presentation_id": "p1", "requests": [{"deleteObject": {}}]}, "1 deleteObject"),
    ("forms_batch_update_form", {"form_id": "f1", "requests": [{"createItem": {}}]}, "1 createItem"),
]


@pytest.mark.parametrize("consent_setting", ["required", "off"])
@pytest.mark.parametrize(("tool", "arguments", "fragment"), DRY_RUNS)
def test_dry_run_previews_without_any_google_call(
    workspace: FastMCP,
    google_calls: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    arguments: dict[str, Any],
    fragment: str,
    consent_setting: str,
) -> None:
    monkeypatch.setenv("MCP_CONSENT", consent_setting)
    result = _call(workspace, tool, {**arguments, "dry_run": True})
    assert result.is_error is False, result.content
    content = result.structured_content
    assert content["status"] == "dry_run"
    assert fragment in content["preview"]
    expected = classify(tool, arguments).risk == "consequential" and consent_setting != "off"
    assert content["consent_required"] is expected
    assert google_calls == []


@pytest.mark.parametrize("tool", ["gmail_modify_messages", "gmail_delete"])
def test_gmail_dry_run_counts_every_message_not_only_the_listed_ones(
    workspace: FastMCP, google_calls: list[str], tool: str
) -> None:
    ids = [f"m{i}" for i in range(12)] + ["m0", "m1"]
    result = _call(workspace, tool, {"message_ids": ids, "dry_run": True})
    content = result.structured_content
    # The preview text names only the first IDs; the counts cover all of them.
    assert (content["count"], content["requested_count"]) == (12, 14)
    assert content["consent_required"] is True
    thread = _call(workspace, tool, {"thread_id": "t1", "dry_run": True}).structured_content
    assert (thread["count"], thread["requested_count"]) == (None, 0)
    assert google_calls == []


def test_gmail_dry_run_description_says_nothing_is_asked_and_what_consent_required_means(
    workspace: FastMCP,
) -> None:
    tool = anyio.run(workspace.get_tool, "gmail_modify_messages")
    text = tool.parameters["properties"]["dry_run"]["description"]
    assert "no consent is asked" not in text
    assert "consent_required" in text and "real call" in text


# ---------------------------------------------------------------------------
# MCP_CONSENT
# ---------------------------------------------------------------------------


def test_consent_mode_defaults_to_required_and_rejects_unknown_values() -> None:
    assert consent_mode({}) == "required"
    assert consent_mode({"MCP_CONSENT": " Preview_Only "}) == "preview_only"
    with pytest.raises(ValueError, match="MCP_CONSENT must be one of required, preview_only, off"):
        consent_mode({"MCP_CONSENT": "never"})


def test_unknown_consent_mode_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_CONSENT", "skip")
    try:
        with pytest.raises(ValueError, match="MCP_CONSENT"):
            importlib.reload(server_module)
    finally:
        monkeypatch.delenv("MCP_CONSENT")
        _compose(FULL_TABLE_ENV)


def test_consent_off_runs_consequential_calls_directly(
    workspace: FastMCP, google_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_CONSENT", "off")
    result = _call(workspace, "gmail_send_email", _SEND)
    assert result.is_error is False
    assert mutations(google_calls) == ["users.messages.send"]


def test_preview_only_never_elicits_even_when_the_client_can(
    workspace: FastMCP, google_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_CONSENT", "preview_only")

    async def never(*_args: Any) -> Any:  # pragma: no cover - must not be asked
        raise AssertionError("preview_only must not elicit")

    result = _call(workspace, "gmail_send_email", _SEND, elicitation_handler=never)
    assert _refused(result)
    assert "MCP_CONSENT=preview_only" in result.structured_content["message"]
    token = _prepared(workspace, "gmail_send_email", _SEND)["commit_token"]
    committed = _call(workspace, "commit_workspace_action", {"commit_token": token}, elicitation_handler=never)
    assert committed.is_error is False
    assert mutations(google_calls) == ["users.messages.send"]


def test_consent_off_is_a_visible_warning_at_startup(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    from mcp_google_workspace.common.production import production_lifespan

    async def start() -> None:
        async with production_lifespan(None):
            pass

    monkeypatch.setenv("MCP_RUNTIME_MODE", "bundle")
    for setting, expected in (("off", True), ("required", False)):
        monkeypatch.setenv("MCP_CONSENT", setting)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="mcp_google_workspace.consent"):
            anyio.run(start)
        warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
        assert bool(warnings) is expected, setting
        if expected:
            assert "MCP_CONSENT=off" in warnings[0].getMessage()
            assert "DISABLED" in warnings[0].getMessage()


def test_the_table_module_is_the_only_place_tools_are_classified() -> None:
    """Annotations, prepare and the gate read one table."""
    from mcp_google_workspace.common import component_annotations

    source = inspect.getsource(component_annotations)
    for retired in ("_DESTRUCTIVE_PREFIXES", "_READ_ONLY_PREFIXES", "_IDEMPOTENT_TOOLS", "CONSEQUENTIAL_TOOLS"):
        assert retired not in source
    assert consent.consent_capable_tools() == {
        name for name, policy in CONSENT_TABLE.items() if policy.may_be_consequential
    }
