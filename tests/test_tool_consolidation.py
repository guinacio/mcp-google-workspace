"""2.0.0 tool consolidation: retired names, tool tiers, registries and job cost.

* Every retired tool name is gone from every tier and answers as an unknown
  tool; its replacement exists; no description still points at a retired name;
  the CHANGELOG lists each pair.
* ``MCP_TOOL_TIER`` selects core (default), extended or complete.
* Every per-tool-name registry (prepare/commit, remote-hidden, always
  available, tiers, annotations, the Apps operation manifest) names tools that
  exist, so a rename can never leave a gate keyed by a dead name.
* The common assistant jobs complete in the number of tool calls the
  consolidation promises, end to end over a fake Google.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastmcp import Client, FastMCP
from mcp.shared.exceptions import MCPError

import mcp_google_workspace.auth.google_auth as google_auth
import mcp_google_workspace.server as server_module
from mcp_google_workspace import tool_tiers
from mcp_google_workspace.apps.operations import DASHBOARD_OPERATIONS
from mcp_google_workspace.auth.grants import ALWAYS_AVAILABLE_TOOLS
from mcp_google_workspace.common import component_annotations
from mcp_google_workspace.common.approvals import CONSEQUENTIAL_TOOLS
from mcp_google_workspace.common.operations import memory_operation_store, set_operation_store
from mcp_google_workspace.common.production import _REMOTE_HIDDEN

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from contracts.retired_tools import RETIRED_TOOLS  # noqa: E402

_TIER_ENV = ("MCP_TOOL_TIER", "ENABLE_APPS_DASHBOARD", "ENABLE_CHAT", "ENABLE_GEMINI", "ENABLE_KEEP", "ENABLE_MEET", "GEMINI_API_KEY")


def _compose(monkeypatch: pytest.MonkeyPatch, **env: str) -> FastMCP:
    for name in _TIER_ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return importlib.reload(server_module).workspace_mcp


@pytest.fixture
def restore_server() -> Iterator[None]:
    yield
    importlib.reload(server_module)


async def _tools(server: FastMCP) -> dict[str, Any]:
    async with Client(server) as client:
        return {tool.name: tool for tool in await client.list_tools()}


@pytest.fixture(scope="module")
def tiers() -> dict[str, dict[str, Any]]:
    mp = pytest.MonkeyPatch()
    catalogs: dict[str, dict[str, Any]] = {}
    try:
        for tier in tool_tiers.TOOL_TIERS:
            extra = {"GEMINI_API_KEY": "test-key"} if tier == "complete" else {}
            catalogs[tier] = anyio.run(_tools, _compose(mp, MCP_TOOL_TIER=tier, **extra))
    finally:
        mp.undo()
        importlib.reload(server_module)
    return catalogs


# --- tiers ---------------------------------------------------------------------


def test_tiers_are_nested_and_the_extended_set_is_exactly_the_difference(tiers) -> None:
    core, extended, complete = (set(tiers[tier]) for tier in tool_tiers.TOOL_TIERS)
    assert core < extended < complete
    assert extended - core == set(tool_tiers.EXTENDED_TOOLS)
    optional = ("apps_", "chat_", "gemini_", "keep_", "meet_")
    assert {name for name in complete - extended if not name.startswith(optional)} == set()
    assert {name.split("_", 1)[0] for name in complete - extended} == {"apps", "chat", "gemini", "keep", "meet"}


def test_tool_counts_per_tier(tiers) -> None:
    # Core: 80 model-visible tools plus the two app-only upload callbacks.
    assert len(tiers["core"]) == 82
    assert len(tiers["extended"]) == 91
    assert len(tiers["complete"]) == 143
    app_only = {
        name for name, tool in tiers["core"].items()
        if ((tool.meta or {}).get("ui") or {}).get("visibility") == ["app"]
    }
    assert app_only == {"uploads_store_files", "uploads_delete_file"}


def test_core_tier_per_namespace_counts(tiers) -> None:
    counts: dict[str, int] = {}
    for name in tiers["core"]:
        namespace = name.split("_", 1)[0]
        counts[namespace] = counts.get(namespace, 0) + 1
    assert {key: counts.get(key, 0) for key in ("gmail", "drive", "calendar", "tasks", "people", "sheets", "slides", "forms", "docs", "uploads")} == {
        "gmail": 16, "drive": 14, "calendar": 10, "tasks": 7, "people": 6,
        "sheets": 5, "slides": 4, "forms": 4, "docs": 4, "uploads": 5,
    }


def test_an_unknown_tier_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TOOL_TIER", "everything")
    with pytest.raises(ValueError, match="MCP_TOOL_TIER must be one of core, extended, complete"):
        tool_tiers.tool_tier()


def test_complete_turns_on_optional_integrations_unless_explicitly_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TOOL_TIER", "complete")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    for name in ("ENABLE_CHAT", "ENABLE_GEMINI"):
        monkeypatch.delenv(name, raising=False)
    assert google_auth.is_chat_enabled() is True
    assert google_auth.is_gemini_enabled() is False  # no API key
    monkeypatch.setenv("ENABLE_CHAT", "false")
    assert google_auth.is_chat_enabled() is False
    monkeypatch.setenv("MCP_TOOL_TIER", "extended")
    monkeypatch.delenv("ENABLE_CHAT")
    assert google_auth.is_chat_enabled() is False


def test_prepare_offers_only_tools_of_the_active_tier(tiers, monkeypatch: pytest.MonkeyPatch, restore_server) -> None:
    def offered(tier: str) -> list[str]:
        return tiers[tier]["prepare_workspace_action"].input_schema["properties"]["tool_name"]["enum"]

    assert "sheets_batch_update_spreadsheet" not in offered("core")
    assert "sheets_batch_update_spreadsheet" in offered("extended")
    assert set(offered("extended")) == CONSEQUENTIAL_TOOLS

    server = _compose(monkeypatch)

    async def prepare() -> Any:
        async with Client(server) as client:
            return await client.call_tool(
                "prepare_workspace_action",
                {"tool_name": "sheets_batch_update_spreadsheet", "arguments": {"requests": []}},
                raise_on_error=False,
            )

    result = anyio.run(prepare)
    assert result.is_error is True and "not available in the core tool tier" in result.content[0].text


# --- retired names ------------------------------------------------------------


def test_retired_names_are_gone_from_every_tier_and_their_replacements_exist(tiers) -> None:
    for tier, tools in tiers.items():
        assert not set(RETIRED_TOOLS) & set(tools), tier
    assert set(RETIRED_TOOLS.values()) <= set(tiers["complete"])


def test_calling_a_retired_name_is_an_unknown_tool(tiers, monkeypatch: pytest.MonkeyPatch, restore_server) -> None:
    server = _compose(monkeypatch, MCP_TOOL_TIER="complete", GEMINI_API_KEY="test-key")

    async def call(name: str) -> str:
        async with Client(server) as client:
            try:
                await client.call_tool(name, {}, raise_on_error=False)
            except MCPError as error:
                return str(error)
        return "called"

    for name in ("gmail_mark_as_read", "drive_create_permission", "files_list_files", "get_workspace_capabilities"):
        assert "Unknown tool" in anyio.run(call, name), name


def test_no_description_points_at_a_retired_tool(tiers) -> None:
    pattern = re.compile(r"\b(" + "|".join(sorted(RETIRED_TOOLS, key=len, reverse=True)) + r")\b")
    hits: list[str] = []
    for name, tool in tiers["complete"].items():
        text = json.dumps({"description": tool.description, "input": tool.input_schema, "output": tool.output_schema})
        hits.extend(f"{name}: {match}" for match in pattern.findall(text))
    assert hits == []


def test_the_changelog_lists_every_retired_name_with_its_replacement() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog.split("## 2.0.0", 1)[1].split("\n## ", 1)[0]
    rows = dict(re.findall(r"^\|\s*`([a-z_]+)`\s*\|\s*`([a-z_]+)`", unreleased, re.MULTILINE))
    assert rows == RETIRED_TOOLS


# --- registries keyed by tool name ---------------------------------------------


def test_every_registry_names_tools_that_exist(tiers) -> None:
    complete = set(tiers["complete"])
    assert CONSEQUENTIAL_TOOLS <= set(tiers["extended"])
    assert _REMOTE_HIDDEN <= complete
    assert ALWAYS_AVAILABLE_TOOLS - {"search_tools", "call_tool"} <= complete
    assert tool_tiers.EXTENDED_TOOLS <= complete
    for spec in DASHBOARD_OPERATIONS.values():
        assert f"{spec.namespace}_{spec.tool}" in complete, spec


def test_annotation_tables_name_tools_that_exist(tiers) -> None:
    bases = {name.split("_", 1)[1] for name in tiers["complete"] if "_" in name} | set(tiers["complete"])
    bases.add("digest")  # tasks_tasks_digest: its local name starts with its namespace
    tables = {
        "_LOCAL_ONLY_TOOLS": component_annotations._LOCAL_ONLY_TOOLS,
        "_MUTATING_TOOLS": component_annotations._MUTATING_TOOLS,
        "_DESTRUCTIVE_TOOLS": component_annotations._DESTRUCTIVE_TOOLS,
        "_IDEMPOTENT_TOOLS": component_annotations._IDEMPOTENT_TOOLS,
        "_READ_ONLY_TOOLS": component_annotations._READ_ONLY_TOOLS,
    }
    stale = {table: sorted(set(names) - bases) for table, names in tables.items() if set(names) - bases}
    assert stale == {}


# (readOnly, destructive, idempotent) for every tool 2.0.0 added or reshaped.
HINTS = {
    "gmail_modify_messages": (False, False, True),
    "gmail_delete": (False, True, False),
    "gmail_reply": (False, False, False),
    "gmail_save_draft": (False, False, False),
    "gmail_list_drafts": (True, False, True),
    "gmail_search_emails": (True, False, True),
    "gmail_read_emails": (True, False, True),
    "gmail_manage_labels": (False, True, False),
    "gmail_manage_filters": (False, True, False),
    "gmail_manage_forwarding": (False, True, False),
    "gmail_manage_vacation": (False, False, True),
    "drive_share_file": (False, False, False),
    "drive_unshare_file": (False, True, False),
    "drive_list_permissions": (True, False, True),
    "drive_list_drives": (True, False, True),
    "drive_update_drive": (False, False, True),
    "drive_get_file": (True, False, True),
    "drive_upload_file": (False, False, False),
    "drive_download_file": (False, False, False),
    "calendar_get_calendar_context": (True, False, True),
    "calendar_find_free_time": (True, False, True),
    "calendar_set_event_attachments": (False, True, True),
    "tasks_list_tasklists": (True, False, True),
    "tasks_list_tasks": (True, False, True),
    "tasks_update_task": (False, False, True),
    "people_search_contacts": (True, False, True),
    "people_manage_contact_groups": (False, False, False),
    "docs_read_document": (True, False, True),
    "sheets_read_sheet_values": (True, False, True),
    "slides_read_presentation": (True, False, True),
    "forms_list_form_responses": (True, False, True),
    "uploads_list_files": (True, False, True),
    "uploads_delete_file": (False, False, True),
    "get_google_connection_status": (True, False, True),
    "resolve_workspace_resource": (True, False, True),
}


@pytest.mark.parametrize("name", sorted(HINTS))
def test_new_and_reshaped_tools_carry_correct_annotations(tiers, name: str) -> None:
    hints = tiers["complete"][name].annotations
    assert (hints.read_only_hint, hints.destructive_hint, hints.idempotent_hint) == HINTS[name]
    assert hints.open_world_hint is (not name.startswith("uploads_"))


# --- gating kept by the renamed consequential tools ----------------------------


class _FakeGoogle:
    """Answers every executed Google call from ``responses`` and records it."""

    def __init__(self, calls: list[tuple[str, dict[str, Any]]], responses: dict[str, Any], path: tuple[str, ...] = (), kwargs: dict[str, Any] | None = None) -> None:
        self._calls = calls
        self._responses = responses
        self._path = path
        self._kwargs = kwargs or {}

    def __getattr__(self, name: str) -> "_FakeGoogle":
        if name.startswith("__"):
            raise AttributeError(name)
        return _FakeGoogle(self._calls, self._responses, (*self._path, name))

    def __call__(self, *_args: Any, **kwargs: Any) -> "_FakeGoogle":
        return _FakeGoogle(self._calls, self._responses, self._path, kwargs)

    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        method = ".".join(self._path)
        self._calls.append((method, self._kwargs))
        response = self._responses.get(method, {})
        return json.loads(json.dumps(response(self._kwargs) if callable(response) else response))


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    responses: dict[str, Any] = {"settings.get": {"id": "timezone", "value": "UTC"}}
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _FakeGoogle(calls, responses))
    set_operation_store(memory_operation_store())
    yield calls, responses
    set_operation_store(None)


def _mutating(calls: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [method for method, _ in calls if method.rsplit(".", 1)[-1] in {"create", "insert", "modify", "batchModify", "send", "update", "patch", "batchUpdate", "delete"}]


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("drive_share_file", {"file_id": "f1", "type": "anyone", "role": "reader"}),
        ("gmail_modify_messages", {"message_ids": [f"m{index}" for index in range(10)], "mark_read": True}),
    ],
)
def test_renamed_consequential_tools_still_require_prepare(google, tool: str, arguments: dict[str, Any]) -> None:
    calls, _ = google

    async def call() -> Any:
        async with Client(server_module.workspace_mcp) as client:
            return await client.call_tool(tool, arguments, raise_on_error=False)

    result = anyio.run(call)
    assert result.is_error is True
    assert result.structured_content["code"] == "prepare_required"
    assert _mutating(calls) == []


# --- the common jobs, end to end ---------------------------------------------------


_MESSAGE = {
    "id": "m1",
    "threadId": "t1",
    "labelIds": ["INBOX", "UNREAD"],
    "snippet": "Can we meet on Friday?",
    "internalDate": "1790000000000",
    "payload": {
        "mimeType": "text/plain",
        "headers": [
            {"name": "From", "value": "Ana <ana@example.com>"},
            {"name": "To", "value": "me@example.com"},
            {"name": "Subject", "value": "Friday"},
            {"name": "Message-ID", "value": "<m1@example.com>"},
        ],
        "body": {"data": "Q2FuIHdlIG1lZXQgb24gRnJpZGF5Pw=="},
    },
}
_DOCUMENT = json.loads((ROOT / "tests" / "fixtures" / "google_payloads" / "docs_get_document.json").read_text(encoding="utf-8"))

_RESPONSES: dict[str, Any] = {
    "users.messages.list": {"messages": [{"id": "m1"}], "resultSizeEstimate": 1},
    "users.messages.get": _MESSAGE,
    "users.threads.get": {"id": "t1", "messages": [_MESSAGE]},
    "users.getProfile": {"emailAddress": "me@example.com"},
    "users.settings.sendAs.list": {"sendAs": []},
    "users.messages.send": {"id": "m2", "threadId": "t1", "labelIds": ["SENT"]},
    "users.labels.list": {"labels": [{"id": "Label_7", "name": "Receipts", "type": "user"}]},
    "users.messages.modify": lambda kwargs: {"id": kwargs["id"], "threadId": "t1", "labelIds": ["Label_7"]},
    "freebusy.query": {"calendars": {"ana@example.com": {"busy": []}, "primary": {"busy": []}}},
    "events.list": {"items": []},
    "events.insert": lambda kwargs: {
        "id": "e1", "status": "confirmed", "summary": kwargs["body"].get("summary"),
        "start": kwargs["body"]["start"], "end": kwargs["body"]["end"], "htmlLink": "https://calendar.google.com/e1",
    },
    "files.list": {"files": [{"id": "doc-1", "name": "Plan", "mimeType": "application/vnd.google-apps.document"}]},
    "documents.get": _DOCUMENT,
    "documents.batchUpdate": {"documentId": "doc-1", "replies": [{"replaceAllText": {"occurrencesChanged": 1}}]},
    "spreadsheets.values.batchGet": {"spreadsheetId": "s1", "valueRanges": [{"range": "Budget!A1:B2", "majorDimension": "ROWS", "values": [["Item", "Cost"], ["Rent", "100"]]}]},
    "spreadsheets.values.update": {"spreadsheetId": "s1", "updatedRange": "Budget!B2", "updatedCells": 1, "updatedRows": 1, "updatedColumns": 1},
    "tasks.list": {"items": [{"id": "task-1", "title": "Send invoice", "status": "needsAction"}]},
    "tasks.patch": {"id": "task-1", "title": "Send invoice", "status": "completed", "completed": "2026-11-04T10:00:00Z"},
    "permissions.create": {"id": "p1", "type": "user", "role": "reader", "emailAddress": "bo@example.com"},
}


async def _accept(_message: str, _type: Any, params: Any, _context: Any) -> Any:
    return {name: True for name in params.requested_schema.get("properties", {})}


class _Job:
    """A client session that counts the tool calls a job needs."""

    def __init__(self, client: Client) -> None:
        self.client = client
        self.calls: list[str] = []

    async def __call__(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(tool)
        result = await self.client.call_tool(tool, arguments, raise_on_error=False)
        assert result.is_error is False, (tool, result.content)
        return result.structured_content or {}


async def _triage(job: _Job) -> None:
    digest = await job("gmail_get_mail_digest", {})
    assert digest["messages"][0]["snippet"] == "Can we meet on Friday?"


async def _read_thread(job: _Job) -> None:
    thread = await job("gmail_get_thread", {"thread_id": "t1"})
    assert thread["latest"]["body"]


async def _reply(job: _Job) -> None:
    found = await job("gmail_search_emails", {"from_email": "ana@example.com"})
    thread = await job("gmail_get_thread", {"thread_id": found["messages"][0]["thread_id"]})
    sent = await job("gmail_reply", {"message_id": thread["latest"]["id"], "text_body": "Friday works."})
    assert sent["status"] == "sent"


async def _schedule(job: _Job) -> None:
    free = await job(
        "calendar_find_free_time",
        {"participants": ["primary", "ana@example.com"], "time_min": "2026-11-06T09:00:00", "time_max": "2026-11-06T12:00:00", "time_zone": "UTC"},
    )
    slot = free["suggested_slots"][0]
    created = await job(
        "calendar_create_event",
        {"summary": "Friday sync", "start_datetime": slot["start"], "end_datetime": slot["end"], "attendees": [{"email": "ana@example.com"}]},
    )
    assert created["status"] == "ok"


async def _find_document(job: _Job) -> None:
    found = await job("search_workspace", {"query": "plan", "services": ["drive"]})
    assert found["matches"][0]["id"] == "doc-1"


async def _summarize_document(job: _Job) -> None:
    found = await job("drive_list_files", {"full_text": "quarterly plan", "kind": "document"})
    text = await job("docs_read_document", {"document_id": found["files"][0]["id"], "format": "text"})
    assert text["body"]


async def _edit_document(job: _Job) -> None:
    await job("docs_read_document", {"document_id": "doc-1", "format": "text"})
    replaced = await job("docs_replace_document_text", {"document_id": "doc-1", "contains_text": "draft", "replace_text": "final"})
    assert replaced["status"] == "ok"


async def _update_sheet(job: _Job) -> None:
    values = await job("sheets_read_sheet_values", {"spreadsheet_id": "s1", "ranges": ["Budget!A1:B2"]})
    assert values["value_ranges"][0]["values"][1] == ["Rent", "100"]
    await job("sheets_update_sheet_values", {"spreadsheet_id": "s1", "range_a1": "Budget!B2", "values": [["120"]]})


async def _manage_tasks(job: _Job) -> None:
    listed = await job("tasks_list_tasks", {"show_completed": False})
    done = await job("tasks_update_task", {"task_id": listed["tasks"][0]["id"], "status": "completed"})
    assert done["task"]["status"] == "completed"


async def _share_file(job: _Job) -> None:
    found = await job("drive_list_files", {"query": "name = 'Plan'"})
    shared = await job(
        "drive_share_file", {"file_id": found["files"][0]["id"], "type": "user", "role": "reader", "email_address": "bo@example.com"}
    )
    assert shared["status"] == "ok"


async def _label_email(job: _Job) -> None:
    labelled = await job("gmail_modify_messages", {"message_ids": ["m1"], "add_labels": ["Receipts"]})
    assert labelled["added_label_ids"] == ["Label_7"]


# (job, tool calls before 2.0.0, tool calls now). "Update a sheet" counts the
# case where the tab and range are known; a sheet whose tabs are unknown adds
# one sheets_get_spreadsheet call, as before.
JOBS = [
    ("triage inbox", _triage, 1, 1),
    ("read a thread", _read_thread, 1, 1),
    ("reply to an email", _reply, 3, 3),
    ("schedule a meeting", _schedule, 2, 2),
    ("find a document", _find_document, 1, 1),
    ("summarize a document", _summarize_document, 2, 2),
    ("edit a document", _edit_document, 2, 2),
    ("update a sheet", _update_sheet, 3, 2),
    ("manage tasks", _manage_tasks, 3, 2),
    ("share a file", _share_file, 2, 2),
    ("label an email", _label_email, 2, 1),
]


@pytest.mark.parametrize(("label", "job", "before", "after"), JOBS, ids=[job[0] for job in JOBS])
def test_common_jobs_complete_in_the_promised_number_of_calls(google, label: str, job: Any, before: int, after: int) -> None:
    calls, responses = google
    responses.update(_RESPONSES)

    async def run() -> list[str]:
        async with Client(server_module.workspace_mcp, elicitation_handler=_accept) as client:
            session = _Job(client)
            await job(session)
            return session.calls

    tool_calls = anyio.run(run)
    assert len(tool_calls) == after, (label, tool_calls)
    assert after <= before
