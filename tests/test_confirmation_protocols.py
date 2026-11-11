"""Safety matrix: every consequential call, every client kind, every outcome.

For each trigger of the consequential-call table (``contracts/confirmation_sites.py``
generated from ``common/consent.py``) and each client kind:

* **MCP 2026-07-28 with elicitation** (multi round-trip): the first call returns
  ``input_required`` before any mutation; accept executes exactly once,
  decline/cancel/no answer execute nothing.
* **Handshake-era with elicitation**: imperative ``ctx.elicit``; same outcomes.
* **Either era without elicitation**: ``confirmation_required`` naming
  ``prepare_workspace_action``; prepare executes nothing and returns the same
  preview text; commit executes exactly once; a repeated commit replays; no
  commit, or an expired token, executes nothing.
* **Background tasks** (the raw batch-update tools): the task parks for input;
  accept executes once, decline nothing.

"Exactly once" is the Google mutations of the same call with consent disabled
(``MCP_CONSENT=off``), so multi-request tools (bulk trash, share + notify) are
compared with what the call really does.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import mcp_types
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.elicitation import ElicitResult
from fastmcp_tasks import call_tool_task

import mcp_google_workspace.auth.google_auth as google_auth
import mcp_google_workspace.auth.google_oauth as google_oauth
import mcp_google_workspace.server as server_module
from mcp_google_workspace.common.operations import OperationStore, memory_operation_store, set_operation_store

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from contracts.confirmation_sites import (  # noqa: E402
    CONFIRMATION_SITES,
    FOREGROUND_SITE_IDS,
    FOREGROUND_SITES,
    FULL_TABLE_ENV,
    OPTIONAL_FLAGS,
    SITE_IDS,
    TASK_SITE_IDS,
    TASK_SITES,
    GoogleRecorder,
    answer,
    answer_field,
    confirmation_operation,
    explicit_confirm,
    mutations,
)


@pytest.fixture(scope="module")
def workspace() -> Iterator[FastMCP]:
    """Root composition serving every table tool, restored afterwards."""
    tracked = (*OPTIONAL_FLAGS, "MCP_RATE_LIMIT_PER_MINUTE", "MCP_TOOL_TIER", "MCP_CONSENT")
    previous = {name: os.environ.get(name) for name in tracked}
    for name in (*OPTIONAL_FLAGS, "MCP_CONSENT"):
        os.environ.pop(name, None)
    os.environ.update(FULL_TABLE_ENV)
    # Hundreds of rounds from one local principal; admission limits are not
    # under test here.
    os.environ["MCP_RATE_LIMIT_PER_MINUTE"] = "100000"
    try:
        yield importlib.reload(server_module).workspace_mcp
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        importlib.reload(server_module)


class _Clock:
    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        import time

        return time.time() + self.offset


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture(autouse=True)
def operation_store(clock: _Clock) -> Iterator[OperationStore]:
    store = memory_operation_store(clock=clock)
    set_operation_store(store)
    try:
        yield store
    finally:
        set_operation_store(None)


@pytest.fixture
def google_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        google_auth, "_build_service_now", lambda *_args, **_kwargs: GoogleRecorder(calls)
    )

    def disconnect() -> dict[str, Any]:
        # Revocation and credential deletion are not Google API client calls.
        calls.append("oauth.credentials.delete")
        return {"status": "disconnected", "grant_revoked": True, "revocation_error": None}

    monkeypatch.setattr(google_oauth, "disconnect_google_account", disconnect)
    return calls


async def _plain_call(server: FastMCP, tool: str, arguments: dict[str, Any]) -> Any:
    async with Client(server) as client:
        return await client.call_tool(tool, arguments, raise_on_error=False)


def _baseline(
    server: FastMCP, tool: str, arguments: dict[str, Any], calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    """The Google mutations of one unconsented run (MCP_CONSENT=off)."""
    monkeypatch.setenv("MCP_CONSENT", "off")
    try:
        anyio.run(_plain_call, server, tool, arguments)
    finally:
        monkeypatch.delenv("MCP_CONSENT")
    done = mutations(calls)
    calls.clear()
    assert done, f"{tool} executed no mutation even without consent: the case does not exercise it"
    return done


async def _never_called(*_args: Any) -> Any:  # pragma: no cover - raw rounds drive MRTR by hand
    raise AssertionError("raw multi-round-trip tests answer explicitly")


async def _two_rounds(
    server: FastMCP,
    tool: str,
    arguments: dict[str, Any],
    action: str | None,
    calls: list[str],
) -> tuple[mcp_types.InputRequiredResult, list[str], Any]:
    """Ask round, then (optionally) the answering retry, on one modern client."""
    async with Client(server, elicitation_handler=_never_called) as client:
        assert client.protocol_version == "2026-07-28"
        first = await client.session.call_tool(tool, arguments, allow_input_required=True)
        assert isinstance(first, mcp_types.InputRequiredResult), first
        after_ask = mutations(calls)
        if action is None:
            return first, after_ask, None
        second = await client.session.call_tool(
            tool,
            arguments,
            allow_input_required=True,
            input_responses=answer(first, action),
            request_state=first.request_state,
        )
        return first, after_ask, second


def _message(result: mcp_types.InputRequiredResult) -> str:
    request = (result.input_requests or {})["confirm"]
    assert isinstance(request, mcp_types.ElicitRequest)
    return request.params.message


# ---------------------------------------------------------------------------
# MCP 2026-07-28, elicitation-capable client
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("tool", "arguments"), FOREGROUND_SITES, ids=FOREGROUND_SITE_IDS)
def test_modern_ask_round_returns_input_required_before_any_mutation(
    workspace: FastMCP, google_calls: list[str], tool: str, arguments: dict[str, Any]
) -> None:
    """Also the "no answer" outcome: the client never answers, nothing runs."""
    first, after_ask, _ = anyio.run(_two_rounds, workspace, tool, arguments, None, google_calls)

    assert after_ask == []
    assert first.result_type == "input_required"
    assert first.request_state  # sealed continuation, opaque to the client
    assert set(first.input_requests or {}) == {"confirm"}
    request = (first.input_requests or {})["confirm"]
    assert isinstance(request, mcp_types.ElicitRequest)
    assert request.params.mode == "form"
    assert request.params.message
    expected_field = "confirm" if explicit_confirm(tool, arguments) else "value"
    assert answer_field(first) == expected_field
    assert request.params.requested_schema["properties"][expected_field]["type"] == "boolean"


@pytest.mark.parametrize(("tool", "arguments"), FOREGROUND_SITES, ids=FOREGROUND_SITE_IDS)
def test_modern_accept_executes_exactly_once(
    workspace: FastMCP,
    google_calls: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    arguments: dict[str, Any],
) -> None:
    expected = _baseline(workspace, tool, arguments, google_calls, monkeypatch)
    _, after_ask, second = anyio.run(_two_rounds, workspace, tool, arguments, "accept", google_calls)

    assert after_ask == []
    assert isinstance(second, mcp_types.CallToolResult)
    assert second.is_error is False, second.content
    assert (second.structured_content or {}).get("status") != "cancelled"
    assert mutations(google_calls) == expected


@pytest.mark.parametrize("action", ["decline", "cancel"])
@pytest.mark.parametrize(("tool", "arguments"), FOREGROUND_SITES, ids=FOREGROUND_SITE_IDS)
def test_modern_decline_or_cancel_executes_nothing(
    workspace: FastMCP, google_calls: list[str], tool: str, arguments: dict[str, Any], action: str
) -> None:
    _, after_ask, second = anyio.run(_two_rounds, workspace, tool, arguments, action, google_calls)

    assert after_ask == []
    assert isinstance(second, mcp_types.CallToolResult)
    assert second.is_error is False
    assert second.structured_content == {
        "status": "cancelled",
        "action": confirmation_operation(tool, arguments),
        "reason": "The user declined; nothing was changed.",
    }
    assert mutations(google_calls) == []


async def _invoke(
    server: FastMCP,
    tool: str,
    arguments: dict[str, Any],
    *,
    mode: str,
    answer_with: str | None,
    prompts: list[str],
) -> Any:
    """Call through the real FastMCP client; ``answer_with=None`` declares no elicitation."""

    async def handler(message: str, _type: Any, params: Any, _context: Any) -> Any:
        prompts.append(message)
        if answer_with == "no_response":
            raise RuntimeError("the user closed the dialog without answering")
        if answer_with != "accept":
            return ElicitResult(action=answer_with)  # type: ignore[arg-type]
        return {name: True for name in params.requested_schema.get("properties", {})}

    kwargs: dict[str, Any] = {} if answer_with is None else {"elicitation_handler": handler}
    async with Client(server, mode=mode, **kwargs) as client:  # type: ignore[arg-type]
        expected = "2026-07-28" if mode == "auto" else "2025-11-25"
        assert client.protocol_version == expected
        return await client.call_tool(tool, arguments, raise_on_error=False)


@pytest.mark.parametrize(("tool", "arguments"), FOREGROUND_SITES, ids=FOREGROUND_SITE_IDS)
def test_modern_client_driver_answers_and_completes(
    workspace: FastMCP,
    google_calls: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    arguments: dict[str, Any],
) -> None:
    """The stock client resolves input_required through its elicitation handler."""
    expected = _baseline(workspace, tool, arguments, google_calls, monkeypatch)
    prompts: list[str] = []
    result = anyio.run(
        lambda: _invoke(workspace, tool, arguments, mode="auto", answer_with="accept", prompts=prompts)
    )

    assert len(prompts) == 1
    assert result.is_error is False
    assert mutations(google_calls) == expected


# ---------------------------------------------------------------------------
# Handshake-era (legacy) sessions with elicitation: ctx.elicit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["decline", "cancel", "no_response"])
@pytest.mark.parametrize(("tool", "arguments"), CONFIRMATION_SITES, ids=SITE_IDS)
def test_legacy_unconfirmed_call_executes_nothing(
    workspace: FastMCP, google_calls: list[str], tool: str, arguments: dict[str, Any], outcome: str
) -> None:
    prompts: list[str] = []
    if outcome == "no_response":
        # The elicitation request itself fails: the call fails, nothing runs.
        with pytest.raises(Exception, match="without answering"):
            anyio.run(
                lambda: _invoke(workspace, tool, arguments, mode="legacy", answer_with=outcome, prompts=prompts)
            )
    else:
        result = anyio.run(
            lambda: _invoke(workspace, tool, arguments, mode="legacy", answer_with=outcome, prompts=prompts)
        )
        assert result.is_error is False
        assert (result.structured_content or {}).get("status") == "cancelled"
    assert len(prompts) == 1
    assert mutations(google_calls) == []


@pytest.mark.parametrize(("tool", "arguments"), CONFIRMATION_SITES, ids=SITE_IDS)
def test_legacy_accepted_call_executes_exactly_once(
    workspace: FastMCP,
    google_calls: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    arguments: dict[str, Any],
) -> None:
    expected = _baseline(workspace, tool, arguments, google_calls, monkeypatch)
    prompts: list[str] = []
    result = anyio.run(
        lambda: _invoke(workspace, tool, arguments, mode="legacy", answer_with="accept", prompts=prompts)
    )

    assert len(prompts) == 1
    assert result.is_error is False, result.content
    assert mutations(google_calls) == expected


# ---------------------------------------------------------------------------
# Clients without elicitation: confirmation_required -> prepare -> commit
# ---------------------------------------------------------------------------


async def _fallback(
    server: FastMCP,
    tool: str,
    arguments: dict[str, Any],
    *,
    mode: str,
    calls: list[str],
    commits: int,
    clock: _Clock | None = None,
) -> dict[str, Any]:
    async with Client(server, mode=mode) as client:  # type: ignore[arg-type]
        direct = await client.call_tool(tool, arguments, raise_on_error=False)
        after_direct = mutations(calls)
        envelope = direct.structured_content or {}
        step = envelope.get("required_action") or {}
        prepared = await client.call_tool(step.get("tool", ""), step.get("arguments", {}), raise_on_error=False)
        after_prepare = mutations(calls)
        if clock is not None:
            clock.offset += 3600
        results = [
            await client.call_tool(
                "commit_workspace_action",
                {"commit_token": (prepared.structured_content or {}).get("commit_token", "")},
                raise_on_error=False,
            )
            for _ in range(commits)
        ]
    return {
        "direct": direct,
        "after_direct": after_direct,
        "prepared": prepared,
        "after_prepare": after_prepare,
        "commits": results,
    }


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize(("tool", "arguments"), CONFIRMATION_SITES, ids=SITE_IDS)
def test_client_without_elicitation_gets_the_prepare_step_and_commit_executes_once(
    workspace: FastMCP,
    google_calls: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    arguments: dict[str, Any],
    mode: str,
) -> None:
    expected = _baseline(workspace, tool, arguments, google_calls, monkeypatch)
    flow = anyio.run(lambda: _fallback(workspace, tool, arguments, mode=mode, calls=google_calls, commits=2))

    direct = flow["direct"]
    assert direct.is_error is True
    envelope = direct.structured_content
    assert envelope["code"] == "confirmation_required"
    assert envelope["retryable"] is False
    assert "No changes were made" in envelope["message"]
    assert envelope["required_action"]["tool"] == "prepare_workspace_action"
    assert envelope["required_action"]["arguments"]["tool_name"] == tool
    action = confirmation_operation(tool, arguments)
    assert envelope["details"]["action"] == action
    assert flow["after_direct"] == []

    prepared = flow["prepared"].structured_content
    assert flow["prepared"].is_error is False, flow["prepared"].content
    assert prepared["status"] == "prepared"
    assert prepared["action"] == action
    # The same preview text the elicitation would show.
    assert prepared["preview"] == envelope["details"]["preview"]
    assert flow["after_prepare"] == []

    first, again = flow["commits"]
    assert first.is_error is False, first.content
    assert first.structured_content["status"] == "committed"
    assert first.structured_content["tool"] == tool
    # A repeated commit returns the saved result; it never executes again.
    assert again.is_error is False
    assert mutations(google_calls) == expected


@pytest.mark.parametrize(("tool", "arguments"), CONFIRMATION_SITES, ids=SITE_IDS)
def test_uncommitted_or_expired_token_executes_nothing(
    workspace: FastMCP, google_calls: list[str], clock: _Clock, tool: str, arguments: dict[str, Any]
) -> None:
    """The "no answer" outcome of prepare/commit: the token expires unused."""
    flow = anyio.run(
        lambda: _fallback(workspace, tool, arguments, mode="auto", calls=google_calls, commits=1, clock=clock)
    )

    (late,) = flow["commits"]
    assert late.is_error is True
    assert "invalid, expired" in late.content[0].text
    assert mutations(google_calls) == []


@pytest.mark.parametrize(("tool", "arguments"), FOREGROUND_SITES, ids=FOREGROUND_SITE_IDS)
def test_prepare_and_elicitation_show_the_same_preview(
    workspace: FastMCP, google_calls: list[str], tool: str, arguments: dict[str, Any]
) -> None:
    async def run() -> tuple[str, str]:
        first, _, _ = await _two_rounds(workspace, tool, arguments, None, google_calls)
        async with Client(workspace) as client:
            prepared = await client.call_tool(
                "prepare_workspace_action", {"tool_name": tool, "arguments": arguments}, raise_on_error=False
            )
        return _message(first), (prepared.structured_content or {}).get("preview", "")

    elicited, previewed = anyio.run(run)
    assert elicited == previewed
    assert mutations(google_calls) == []


@pytest.mark.parametrize("answer_with", ["accept", "decline"])
@pytest.mark.parametrize(("tool", "arguments"), CONFIRMATION_SITES[:6], ids=SITE_IDS[:6])
def test_a_client_that_can_ask_is_still_asked_when_it_commits(
    workspace: FastMCP,
    google_calls: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    arguments: dict[str, Any],
    answer_with: str,
) -> None:
    """Prepare/commit never replaces the user's own answer when the client can elicit."""
    expected = _baseline(workspace, tool, arguments, google_calls, monkeypatch)
    prompts: list[str] = []

    async def handler(message: str, _type: Any, params: Any, _context: Any) -> Any:
        prompts.append(message)
        if answer_with != "accept":
            return ElicitResult(action="decline")
        return {name: True for name in params.requested_schema.get("properties", {})}

    async def run() -> tuple[Any, Any]:
        async with Client(workspace, elicitation_handler=handler) as client:
            prepared = await client.call_tool(
                "prepare_workspace_action", {"tool_name": tool, "arguments": arguments}
            )
            committed = await client.call_tool(
                "commit_workspace_action",
                {"commit_token": prepared.structured_content["commit_token"]},
                raise_on_error=False,
            )
        return prepared, committed

    prepared, committed = anyio.run(run)
    assert prompts == [prepared.structured_content["preview"]]
    assert committed.is_error is False
    if answer_with == "accept":
        assert mutations(google_calls) == expected
    else:
        assert mutations(google_calls) == []


# ---------------------------------------------------------------------------
# Background tasks (the task-capable table tools)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("answer_with", ["accept", "decline"])
@pytest.mark.parametrize(("tool", "arguments"), TASK_SITES, ids=TASK_SITE_IDS)
def test_task_mode_call_parks_for_consent(
    workspace: FastMCP,
    google_calls: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    arguments: dict[str, Any],
    answer_with: str,
) -> None:
    expected = _baseline(workspace, tool, arguments, google_calls, monkeypatch)
    prompts: list[str] = []

    async def handler(message: str, _type: Any, params: Any, _context: Any) -> Any:
        assert mutations(google_calls) == []  # the ask happens before any mutation
        prompts.append(message)
        if answer_with != "accept":
            return ElicitResult(action="decline")
        return {name: True for name in params.requested_schema.get("properties", {})}

    async def run() -> Any:
        async with Client(workspace, elicitation_handler=handler) as client:
            task = await call_tool_task(client, tool, arguments)
            return await task.result()

    result = anyio.run(run)
    assert len(prompts) == 1
    assert result.is_error is False
    if answer_with == "accept":
        assert mutations(google_calls) == expected
    else:
        assert result.structured_content["status"] == "cancelled"
        assert mutations(google_calls) == []


@pytest.mark.parametrize("consent", ["required", "preview_only"])
@pytest.mark.parametrize(("tool", "arguments"), TASK_SITES, ids=TASK_SITE_IDS)
def test_task_that_could_never_ask_is_refused_before_it_is_created(
    workspace: FastMCP,
    google_calls: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    arguments: dict[str, Any],
    consent: str,
) -> None:
    """A task cannot read the client's capabilities, so the submission is checked.

    Without elicitation (or with MCP_CONSENT=preview_only) the call gets the
    prepare step at once instead of a task parked on a question nobody can answer.
    """
    monkeypatch.setenv("MCP_CONSENT", consent)

    async def run() -> Any:
        handler = {"elicitation_handler": _never_called} if consent == "preview_only" else {}
        async with Client(workspace, **handler) as client:  # type: ignore[arg-type]
            return await client.call_tool(tool, arguments, raise_on_error=False)

    result = anyio.run(run)
    assert result.is_error is True
    assert result.structured_content["code"] == "confirmation_required"
    assert result.structured_content["required_action"]["tool"] == "prepare_workspace_action"
    assert result.structured_content["details"]["preview"]
    assert mutations(google_calls) == []


# ---------------------------------------------------------------------------
# The adapter is the only way to ask
# ---------------------------------------------------------------------------


def test_only_the_adapter_uses_imperative_elicitation() -> None:
    """ctx.elicit stays behind the adapter's legacy branch, in one module."""
    import mcp_google_workspace

    root = Path(mcp_google_workspace.__file__).parent
    offenders = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if ".elicit(" in path.read_text(encoding="utf-8")
    )
    assert offenders == ["common/confirmation.py"]


def test_no_tool_asks_for_consent_by_itself() -> None:
    """Every consent decision comes from the table: no tool calls the adapter."""
    import mcp_google_workspace

    root = Path(mcp_google_workspace.__file__).parent
    offenders = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "confirm_destructive_action(" in path.read_text(encoding="utf-8")
    )
    assert offenders == ["common/confirmation.py", "common/consent.py"]


class _NoElicitContext:
    """Context stand-in whose request negotiated no usable elicitation."""

    is_background_task = False

    def __init__(self, protocol_version: str | None) -> None:
        self.request_context = (
            None if protocol_version is None else type("RC", (), {"protocol_version": protocol_version})()
        )

    async def elicit(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("elicit must not be attempted without a usable elicitation channel")


@pytest.mark.parametrize("protocol_version", [None, "", "1999-01-01", "2026-07-28", "2025-11-25"])
def test_confirmation_adapter_fails_closed_without_a_usable_channel(protocol_version: str | None) -> None:
    """Unknown versions, and known versions without a session/capability, never ask."""
    from mcp_google_workspace.common.confirmation import confirm_destructive_action
    from mcp_google_workspace.common.errors import ConfirmationRequiredError

    async def run() -> None:
        with pytest.raises(ConfirmationRequiredError):
            await confirm_destructive_action(
                _NoElicitContext(protocol_version),  # type: ignore[arg-type]
                "delete_thing",
                "Delete the thing?",
            )

    anyio.run(run)
