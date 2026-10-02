"""An asking round must survive every wrapper intact (W4a).

A multi-round-trip ask is FastMCP's ``InputRequiredToolResult``. It must never
be wrapped as a successful business payload, decorated with resource handles,
converted to an error, counted as a completed operation, or treated as a
completed commit. This module checks each wrapper and proxy on the path, plus
the prepare/commit token lifecycle and in-task confirmations.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import mcp.types as mt
import mcp_types
import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.client.elicitation import ElicitResult
from fastmcp.exceptions import McpError
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools import InputRequiredToolResult, ToolResult
from fastmcp_tasks import call_tool_task
from mcp.shared.exceptions import MCPError

import mcp_google_workspace.auth.google_auth as google_auth
import mcp_google_workspace.server as server_module
from mcp_google_workspace import tool_discovery
from mcp_google_workspace.common.component_annotations import (
    _paginated_result,
    _wrap_pagination,
    apply_default_tool_annotations,
)
from mcp_google_workspace.auth.identity import current_principal
from mcp_google_workspace.common.approvals import prepare_action
from mcp_google_workspace.common.confirmation import (
    confirm_destructive_action,
    install_confirmation_guard,
    reset_confirmation_keys,
)
from mcp_google_workspace.common.operations import (
    OPERATION_META_KEY,
    OperationStore,
    memory_operation_store,
    operation_key,
    set_operation_store,
)
from mcp_google_workspace.common.repeat_safety import track_provider_call
from mcp_google_workspace.common.errors import StructuredToolErrorMiddleware
from mcp_google_workspace.common.production import METRICS, RUNTIME_STATE, ProductionControlMiddleware
from mcp_google_workspace.common.resources import ResourceHandleMiddleware
from mcp_google_workspace.common.task_backend import install_tasks_extension

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from contracts.confirmation_sites import OPTIONAL_FLAGS, GoogleRecorder, answer, mutations  # noqa: E402


def _ask_result() -> InputRequiredToolResult:
    return InputRequiredToolResult(
        mcp_types.InputRequiredResult(
            input_requests={
                "confirm": mcp_types.ElicitRequest(
                    params=mcp_types.ElicitRequestFormParams(
                        message="Delete?",
                        requested_schema={"type": "object", "properties": {"value": {"type": "boolean"}}},
                    )
                )
            },
            request_state="opaque",
        )
    )


def _context(name: str) -> MiddlewareContext[mt.CallToolRequestParams]:
    return MiddlewareContext(
        message=mt.CallToolRequestParams(name=name, arguments={"person_name": "people/c1"}),
        method="tools/call",
    )


@pytest.fixture(autouse=True)
def fresh_confirmation_state() -> Iterator[OperationStore]:
    reset_confirmation_keys()
    store = memory_operation_store()
    set_operation_store(store)
    try:
        yield store
    finally:
        set_operation_store(None)
        reset_confirmation_keys()


# ---------------------------------------------------------------------------
# Middleware, one by one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "middleware",
    [StructuredToolErrorMiddleware(), ResourceHandleMiddleware()],
    ids=["structured-errors", "resource-handles"],
)
def test_middleware_passes_the_ask_through_untouched(middleware: Any) -> None:
    ask = _ask_result()

    async def call_next(_context: Any) -> ToolResult:
        return ask

    result = anyio.run(lambda: middleware.on_call_tool(_context("people_delete_contact"), call_next))
    assert result is ask
    assert result.is_error is False
    assert result.structured_content is None
    assert result.content == []
    assert result.input_required.request_state == "opaque"


def test_admission_telemetry_counts_an_ask_as_a_round_not_a_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_RATE_LIMIT_PER_MINUTE", "100000")
    # Earlier in-process clients leave the shared runtime state draining.
    monkeypatch.setattr(RUNTIME_STATE, "draining", False)
    middleware = ProductionControlMiddleware()
    ask = _ask_result()
    tool = "people_delete_contact_telemetry_probe"

    async def call_next(_context: Any) -> ToolResult:
        return ask

    before_ok = METRICS.calls[(tool, "ok")]
    before_round = METRICS.calls[(tool, "input_required")]
    result = anyio.run(lambda: middleware.on_call_tool(_context(tool), call_next))
    assert result is ask
    assert METRICS.calls[(tool, "input_required")] == before_round + 1
    assert METRICS.calls[(tool, "ok")] == before_ok


def test_pagination_wrapper_and_guard_leave_an_ask_unchanged() -> None:
    raw = mcp_types.InputRequiredResult(request_state="opaque")
    assert _paginated_result(raw) is raw

    async def list_things(ctx: Context | None = None) -> dict[str, Any]:
        if not await confirm_destructive_action(ctx, "list_things", "List?"):
            return {"status": "cancelled"}
        return {"items": []}

    ctx = SimpleNamespace(
        is_background_task=False,
        request_context=SimpleNamespace(protocol_version="2026-07-28"),
        session=SimpleNamespace(client_capabilities=SimpleNamespace(elicitation=object())),
        request_state=None,
        input_responses=None,
    )
    component = SimpleNamespace(fn=list_things)
    _wrap_pagination(component, "list_things")
    install_confirmation_guard(component, "list_things")
    result = anyio.run(lambda: component.fn(ctx=ctx))
    assert isinstance(result, mcp_types.InputRequiredResult)
    assert set(result.input_requests or {}) == {"confirm"}


def test_guard_preserves_signature_and_published_schemas() -> None:
    from mcp_google_workspace.people import people_mcp

    async def exercise() -> Any:
        return await people_mcp.get_tool("delete_contact")

    tool = anyio.run(exercise)
    assert getattr(tool.fn, "_workspace_confirmation_guard", False) is True
    assert "person_name" in tool.parameters["properties"]
    assert "ctx" not in tool.parameters["properties"]
    assert tool.output_schema is not None  # the ask round is exempt from output validation


# ---------------------------------------------------------------------------
# Proxies and the prepare/commit flow over the full composition
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    for name in OPTIONAL_FLAGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MCP_RATE_LIMIT_PER_MINUTE", "100000")
    try:
        yield monkeypatch
    finally:
        monkeypatch.undo()
        tool_discovery._CONFIGURED_SERVERS.clear()
        importlib.reload(server_module)


@pytest.fixture
def google_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: GoogleRecorder(calls))
    return calls


async def _never(*_args: Any) -> Any:  # pragma: no cover - rounds are driven by hand
    raise AssertionError("unexpected elicitation callback")


async def _rounds(server: FastMCP, tool: str, arguments: dict[str, Any], plan: list[str | None]) -> list[Any]:
    """Run an ask round, then one retry per ``plan`` entry (an answer action).

    ``None`` retries with the previous state and no answer at all.
    """
    results: list[Any] = []
    async with Client(server, elicitation_handler=_never) as client:
        first = await client.session.call_tool(tool, arguments, allow_input_required=True)
        results.append(first)
        for action in plan:
            assert isinstance(first, mcp_types.InputRequiredResult)
            try:
                results.append(
                    await client.session.call_tool(
                        tool,
                        arguments,
                        allow_input_required=True,
                        input_responses=None if action is None else answer(first, action),
                        request_state=first.request_state,
                    )
                )
            except MCPError as exc:
                results.append(exc)
    return results


async def _answer(
    server: FastMCP, tool: str, arguments: dict[str, Any], first: mcp_types.InputRequiredResult, action: str
) -> Any:
    """Answer a previous ask from a fresh connection (state is request-bound)."""
    async with Client(server, elicitation_handler=_never) as client:
        return await client.session.call_tool(
            tool,
            arguments,
            allow_input_required=True,
            input_responses=answer(first, action),
            request_state=first.request_state,
        )


async def _rounds_from(
    server: FastMCP,
    tool: str,
    arguments: dict[str, Any],
    first: mcp_types.InputRequiredResult,
    plan: list[str | None],
) -> list[Any]:
    """Retries answering an ask obtained earlier (see ``_rounds``)."""
    results: list[Any] = [first]
    async with Client(server, elicitation_handler=_never) as client:
        for action in plan:
            try:
                results.append(
                    await client.session.call_tool(
                        tool,
                        arguments,
                        allow_input_required=True,
                        input_responses=None if action is None else answer(first, action),
                        request_state=first.request_state,
                    )
                )
            except MCPError as exc:
                results.append(exc)
    return results


def test_bm25_call_tool_proxy_passes_the_ask_through_intact(workspace_env, google_calls) -> None:
    workspace_env.setenv("MCP_TOOL_SEARCH", "on")
    server = importlib.reload(server_module).workspace_mcp
    tool_discovery._CONFIGURED_SERVERS.clear()
    assert tool_discovery.configure_tool_search(server)
    proxied = {"name": "people_delete_contact", "arguments": {"person_name": "people/c1"}}

    first, done = anyio.run(_rounds, server, "call_tool", proxied, ["accept"])

    assert isinstance(first, mcp_types.InputRequiredResult)
    assert set(first.input_requests or {}) == {"confirm"}
    assert isinstance(done, mcp_types.CallToolResult)
    assert done.is_error is False
    assert (done.structured_content or {}).get("status") == "deleted"
    assert mutations(google_calls) == ["people.deleteContact"]


_BULK_SEND = {
    "subject": "Announcement",
    "to": [f"person-{index}@example.com" for index in range(10)],
    "text_body": "Hello",
    "confirm_send": True,
}


def _prepare(server: FastMCP) -> str:
    async def exercise() -> str:
        async with Client(server) as client:
            prepared = await client.call_tool(
                "prepare_workspace_action", {"tool_name": "gmail_send_email", "arguments": _BULK_SEND}
            )
        return str(prepared.structured_content["commit_token"])

    return anyio.run(exercise)


def _state(store: OperationStore, token: str) -> str | None:
    """State of the commit's operation record (``None`` once gone)."""
    record = anyio.run(lambda: store.get(operation_key(current_principal().storage_key, token)))
    return None if record is None else str(record["state"])


def test_commit_asking_round_keeps_the_approval_token(workspace_env, fresh_confirmation_state, google_calls) -> None:
    store = fresh_confirmation_state
    server = importlib.reload(server_module).workspace_mcp
    token = _prepare(server)
    assert _state(store, token) == "prepared"
    commit = {"commit_token": token}

    (first,) = anyio.run(_rounds, server, "commit_workspace_action", commit, [])
    # Ask round: a question, not a commit; the operation awaits the answer.
    assert isinstance(first, mcp_types.InputRequiredResult)
    assert _state(store, token) == "awaiting_input"
    assert mutations(google_calls) == []

    _, retry_without_answer, done, replay = anyio.run(
        _rounds_from, server, "commit_workspace_action", commit, first, [None, "accept", "accept"]
    )
    assert first.input_requests is not None
    confirm = first.input_requests["confirm"]
    assert isinstance(confirm, mcp_types.ElicitRequest)
    assert "confirm" in confirm.params.requested_schema["properties"]
    # A rejected answer round releases the claim as well.
    assert isinstance(retry_without_answer, mcp_types.CallToolResult)
    assert retry_without_answer.is_error is True
    assert (retry_without_answer.structured_content or {})["code"] == "confirmation_invalid"
    # Accepted: exactly one send, and the operation is finished.
    assert isinstance(done, mcp_types.CallToolResult), done
    assert done.is_error is False
    assert (done.structured_content or {})["status"] == "committed"
    assert mutations(google_calls) == ["users.messages.send"]
    assert _state(store, token) == "succeeded"
    # W4b: repeating the commit returns the saved result and cannot send again
    # (W4a rejected the repeat with an error, so a lost response left no result).
    assert isinstance(replay, mcp_types.CallToolResult), replay
    assert replay.is_error is False
    assert (replay.structured_content or {})["status"] == "committed"
    assert replay.meta[OPERATION_META_KEY]["replayed"] is True
    assert mutations(google_calls) == ["users.messages.send"]


def test_commit_decline_consumes_the_token_without_sending(workspace_env, fresh_confirmation_state, google_calls) -> None:
    store = fresh_confirmation_state
    server = importlib.reload(server_module).workspace_mcp
    token = _prepare(server)

    commit = {"commit_token": token}
    (first,) = anyio.run(_rounds, server, "commit_workspace_action", commit, [])
    assert isinstance(first, mcp_types.InputRequiredResult)
    assert _state(store, token) == "awaiting_input"  # asked, token kept and unclaimed
    assert mutations(google_calls) == []

    declined = anyio.run(_answer, server, "commit_workspace_action", commit, first, "decline")
    assert isinstance(declined, mcp_types.CallToolResult)
    assert (declined.structured_content or {})["result"]["status"] == "cancelled"
    assert _state(store, token) == "succeeded"  # finished (declined); never executable again
    assert mutations(google_calls) == []


class _Rejected(Exception):
    resp = SimpleNamespace(status=400)


@pytest.mark.parametrize(
    ("code", "attempted", "settlement"),
    [
        ("rate_limited", None, "prepared"),
        ("confirmation_invalid", None, "prepared"),
        ("timeout", None, "prepared"),
        ("internal_error", "applied", "outcome_unknown"),
        ("timeout", "uncertain", "outcome_unknown"),
        ("invalid_input", "rejected", "failed"),
    ],
)
def test_commit_failure_is_settled_from_what_reached_google(
    monkeypatch: pytest.MonkeyPatch,
    fresh_confirmation_state: OperationStore,
    code: str,
    attempted: str | None,
    settlement: str,
) -> None:
    """W4a released only for pre-execution codes and otherwise consumed the token.

    W4b settles from the tracked Google calls: nothing non-repeatable reached
    Google -> back to ``prepared`` (retryable); a send may have happened ->
    ``outcome_unknown``; Google definitively rejected it -> ``failed``.
    """
    store = fresh_confirmation_state

    async def failing_call(_name: str, _arguments: dict[str, Any]) -> Any:
        if attempted is not None:
            try:
                with track_provider_call("gmail.users.messages.send", {"userId": "me"}):
                    if attempted == "rejected":
                        raise _Rejected("bad request")
                    if attempted == "uncertain":
                        raise TimeoutError("socket timeout")
            except Exception:  # noqa: BLE001 - the nested error middleware's job
                pass
        raise McpError(code=-32000, message="failed", data={"code": code})

    async def exercise() -> tuple[str, str | None]:
        prepared = await prepare_action("gmail_batch_modify", {"message_ids": ["m"] * 10})
        token = str(prepared["commit_token"])
        tool = await server_module.workspace_mcp.get_tool("commit_workspace_action")
        assert getattr(tool.fn, "_workspace_confirmation_guard", False) is True
        monkeypatch.setattr(server_module, "workspace_mcp", SimpleNamespace(call_tool=failing_call))
        try:
            returned = await tool.fn(token)
        except Exception as exc:  # noqa: BLE001 - the nested protocol error (McpError)
            raised = getattr(exc, "error_code", None) or "mcp_error"
        else:
            # W5: the execution guard returns tool execution errors (here
            # outcome_unknown) as isError results instead of raising them.
            assert isinstance(returned, ToolResult) and returned.is_error is True, returned
            raised = (returned.structured_content or {}).get("code")
        record = await store.get(operation_key(current_principal().storage_key, token))
        return raised, None if record is None else record["state"]

    raised, state = anyio.run(exercise)
    assert state == settlement
    assert raised == ("outcome_unknown" if settlement == "outcome_unknown" else "mcp_error")


def test_operation_claims_are_exclusive_releasable_and_never_consumed(fresh_confirmation_state) -> None:
    """Replaces the W4a SQLite/Redis approval-token claim tests (both stores are removed)."""
    store = fresh_confirmation_state

    async def exercise() -> list[str]:
        token = str((await prepare_action("gmail_batch_modify", {"message_ids": ["m"] * 10}))["commit_token"])
        key = operation_key(current_principal().storage_key, token)
        first = await store.claim(key, kind="commit", ref="r")
        second = await store.claim(key, kind="commit", ref="r")
        assert first.handle is not None and first.record is not None
        assert first.record["arguments"] == {"message_ids": ["m"] * 10}
        await store.release(first.handle)
        third = await store.claim(key, kind="commit", ref="r")
        assert third.handle is not None
        await store.succeed(third.handle, {"status": "committed"})
        fourth = await store.claim(key, kind="commit", ref="r")
        assert fourth.record is not None and fourth.record["arguments"] is None  # dropped when terminal
        other = await store.claim(operation_key("someone-else", token), kind="commit", ref="r")
        return [first.status, second.status, third.status, fourth.status, other.status]

    assert anyio.run(exercise) == ["claimed", "in_progress", "claimed", "saved", "missing"]


# ---------------------------------------------------------------------------
# Tasks extension: a tasked tool that needs confirmation
# ---------------------------------------------------------------------------


def _tasked_server(mutated: list[str]) -> FastMCP:
    server = FastMCP(name="tasked-confirmation")
    install_tasks_extension(server)

    @server.tool(name="purge_thing", task=True)
    async def purge_thing(thing_id: str, ctx: Context) -> dict[str, Any]:
        if not await confirm_destructive_action(ctx, "purge_thing", f"Purge {thing_id}?"):
            return {"status": "cancelled"}
        mutated.append(thing_id)
        return {"status": "purged", "thing_id": thing_id}

    apply_default_tool_annotations(server)
    return server


@pytest.mark.parametrize(("accept", "expected"), [(True, "purged"), (False, "cancelled")])
def test_tasked_tool_parks_for_input_and_resumes_via_tasks_update(accept: bool, expected: str) -> None:
    """FastMCP 4.0.10 supports the guard pattern in tasks: input_required -> tasks/update."""
    mutated: list[str] = []
    prompts: list[str] = []
    server = _tasked_server(mutated)

    async def handler(message: str, _type: Any, params: Any, _context: Any) -> Any:
        prompts.append(message)
        assert mutated == []  # the ask happens before any mutation
        if not accept:
            return ElicitResult(action="decline")
        return {name: True for name in params.requested_schema.get("properties", {})}

    async def exercise() -> Any:
        async with Client(server, elicitation_handler=handler) as client:
            task = await call_tool_task(client, "purge_thing", {"thing_id": "t1"})
            return await task.result()

    result = anyio.run(exercise)
    assert prompts == ["Purge t1?"]
    assert result.is_error is False
    assert result.structured_content["status"] == expected
    assert mutated == (["t1"] if accept else [])
