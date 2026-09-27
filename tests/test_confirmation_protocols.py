"""Confirmation sites on both MCP protocol eras, through the one adapter.

All 24 confirmation sites (see ``contracts/confirmation_sites.py``) call
``common.confirmation.confirm_destructive_action``:

* MCP 2026-07-28 (multi round-trip): the first call returns
  ``resultType: input_required`` with one form elicitation and a sealed
  continuation, before any mutation. Retrying with an ``accept`` answer
  executes exactly one mutation; ``decline``/``cancel`` execute none.
* A modern host that declared no elicitation capability gets the fail-closed
  ``confirmation_required`` tool result and no mutation.
* Handshake-era (legacy) requests keep imperative elicitation through the
  adapter's legacy branch.

Continuation-security cases (tampering, expiry, replay, ...) live in
``test_confirmation_adapter.py``.
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

import mcp_google_workspace.auth.google_auth as google_auth
import mcp_google_workspace.server as server_module
from mcp_google_workspace.common.operations import OperationStore, memory_operation_store, set_operation_store

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from contracts.confirmation_sites import (  # noqa: E402
    CONFIRMATION_SITES,
    EXPLICIT_CONFIRM_SITES,
    OPTIONAL_FLAGS,
    SITE_IDS,
    GoogleRecorder,
    answer,
    answer_field,
    mutations,
)


@pytest.fixture(scope="module")
def workspace() -> Iterator[FastMCP]:
    """Root composition with Chat and Keep mounted, restored afterwards."""
    tracked = (*OPTIONAL_FLAGS, "MCP_RATE_LIMIT_PER_MINUTE")
    previous = {name: os.environ.get(name) for name in tracked}
    for name in OPTIONAL_FLAGS:
        os.environ.pop(name, None)
    os.environ["ENABLE_CHAT"] = "true"
    os.environ["ENABLE_KEEP"] = "true"
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


@pytest.fixture(autouse=True)
def operation_store() -> Iterator[OperationStore]:
    store = memory_operation_store()
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
    return calls


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


@pytest.mark.parametrize(("tool", "arguments"), CONFIRMATION_SITES, ids=SITE_IDS)
def test_modern_ask_round_returns_input_required_before_any_mutation(
    workspace: FastMCP, google_calls: list[str], tool: str, arguments: dict[str, Any]
) -> None:
    first, after_ask, _ = anyio.run(_two_rounds, workspace, tool, arguments, None, google_calls)

    assert after_ask == []
    assert first.result_type == "input_required"
    assert first.request_state  # sealed continuation, opaque to the client
    assert set(first.input_requests or {}) == {"confirm"}
    request = (first.input_requests or {})["confirm"]
    assert isinstance(request, mcp_types.ElicitRequest)
    assert request.params.mode == "form"
    assert request.params.message
    expected_field = "confirm" if tool in EXPLICIT_CONFIRM_SITES else "value"
    assert answer_field(first) == expected_field
    assert request.params.requested_schema["properties"][expected_field]["type"] == "boolean"


@pytest.mark.parametrize(("tool", "arguments"), CONFIRMATION_SITES, ids=SITE_IDS)
def test_modern_accept_executes_exactly_one_mutation(
    workspace: FastMCP, google_calls: list[str], tool: str, arguments: dict[str, Any]
) -> None:
    _, after_ask, second = anyio.run(_two_rounds, workspace, tool, arguments, "accept", google_calls)

    assert after_ask == []
    assert isinstance(second, mcp_types.CallToolResult)
    assert second.is_error is False, second.content
    assert (second.structured_content or {}).get("status") != "cancelled"
    assert len(mutations(google_calls)) == 1, google_calls


@pytest.mark.parametrize("action", ["decline", "cancel"])
@pytest.mark.parametrize(("tool", "arguments"), CONFIRMATION_SITES, ids=SITE_IDS)
def test_modern_decline_or_cancel_executes_no_mutation(
    workspace: FastMCP, google_calls: list[str], tool: str, arguments: dict[str, Any], action: str
) -> None:
    _, after_ask, second = anyio.run(_two_rounds, workspace, tool, arguments, action, google_calls)

    assert after_ask == []
    assert isinstance(second, mcp_types.CallToolResult)
    assert second.is_error is False
    assert (second.structured_content or {}).get("status") == "cancelled"
    assert mutations(google_calls) == []


async def _invoke(
    server: FastMCP,
    tool: str,
    arguments: dict[str, Any],
    *,
    mode: str,
    accept: bool | None,
    prompts: list[str],
) -> Any:
    """Call through the real FastMCP client; ``accept=None`` declares no elicitation."""

    async def handler(message: str, _type: Any, params: Any, _context: Any) -> Any:
        prompts.append(message)
        if not accept:
            return ElicitResult(action="decline")
        return {name: True for name in params.requested_schema.get("properties", {})}

    kwargs: dict[str, Any] = {} if accept is None else {"elicitation_handler": handler}
    async with Client(server, mode=mode, **kwargs) as client:  # type: ignore[arg-type]
        expected = "2026-07-28" if mode == "auto" else "2025-11-25"
        assert client.protocol_version == expected
        return await client.call_tool(tool, arguments, raise_on_error=False)


@pytest.mark.parametrize(("tool", "arguments"), CONFIRMATION_SITES, ids=SITE_IDS)
def test_modern_client_driver_answers_and_completes(
    workspace: FastMCP, google_calls: list[str], tool: str, arguments: dict[str, Any]
) -> None:
    """The stock client resolves input_required through its elicitation handler."""
    prompts: list[str] = []
    result = anyio.run(
        lambda: _invoke(workspace, tool, arguments, mode="auto", accept=True, prompts=prompts)
    )

    assert len(prompts) == 1
    assert result.is_error is False
    assert len(mutations(google_calls)) == 1, google_calls


@pytest.mark.parametrize(("tool", "arguments"), CONFIRMATION_SITES, ids=SITE_IDS)
@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_host_without_elicitation_fails_closed_with_tool_result_and_no_mutation(
    workspace: FastMCP, google_calls: list[str], tool: str, arguments: dict[str, Any], mode: str
) -> None:
    prompts: list[str] = []
    result = anyio.run(
        lambda: _invoke(workspace, tool, arguments, mode=mode, accept=None, prompts=prompts)
    )

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
    assert mutations(google_calls) == []


# Legacy (handshake-era) connections: the adapter's thin ctx.elicit branch.
@pytest.mark.parametrize(("tool", "arguments"), CONFIRMATION_SITES, ids=SITE_IDS)
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
    assert mutations(google_calls) == []


@pytest.mark.parametrize(("tool", "arguments"), CONFIRMATION_SITES, ids=SITE_IDS)
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
    assert mutations(google_calls), google_calls


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
