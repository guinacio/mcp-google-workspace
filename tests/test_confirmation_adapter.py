"""Continuation security of the confirmation adapter (W4a).

Two layers are exercised:

* the application continuation itself (``common.confirmation``), driven
  directly through a guarded tool body with a stand-in modern context, so each
  claim check (integrity, expiry, principal, tool, argument digest, preview,
  answer type, replay) is observed in isolation;
* the full production composition over a modern in-memory client, where
  FastMCP's ``RequestStateBoundary`` also seals the continuation, including a
  replica switch and key rotation across independently constructed servers.

Every failure case asserts that no mutation happened.
"""

from __future__ import annotations

import base64
import importlib
import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import mcp_types
import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.tools import ToolResult
from mcp.shared.exceptions import MCPError

import mcp_google_workspace.auth.google_auth as google_auth
import mcp_google_workspace.server as server_module
from mcp_google_workspace.common import confirmation
from mcp_google_workspace.common.confirmation import (
    ConfirmationInputRequired,
    build_request_state_security,
    canonical_arguments,
    confirm_destructive_action,
    configured_request_state_keys,
    install_confirmation_guard,
    reset_confirmation_keys,
)
from mcp_google_workspace.common.operations import (
    OPERATION_META_KEY,
    OperationStore,
    memory_operation_store,
    set_operation_store,
)
from mcp_google_workspace.common.errors import ConfirmationRejectedError, ConfirmationRequiredError

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from contracts.confirmation_sites import OPTIONAL_FLAGS, GoogleRecorder, answer, mutations  # noqa: E402

KEY_OLD = "old-" + "a" * 60
KEY_NEW = "new-" + "b" * 60
KEY_OTHER = "oth-" + "c" * 60


@pytest.fixture(autouse=True)
def isolated_keys_and_operations(monkeypatch: pytest.MonkeyPatch) -> Iterator[OperationStore]:
    monkeypatch.delenv("MCP_REQUEST_STATE_KEYS", raising=False)
    monkeypatch.delenv("MCP_CONFIRMATION_TTL_SECONDS", raising=False)
    monkeypatch.setenv("MCP_LOCAL_PRINCIPAL", "alice")
    reset_confirmation_keys()
    store = memory_operation_store()
    set_operation_store(store)
    try:
        yield store
    finally:
        set_operation_store(None)
        reset_confirmation_keys()


# ---------------------------------------------------------------------------
# Application layer: a guarded tool body with a stand-in modern request
# ---------------------------------------------------------------------------


class _ModernContext:
    """Just enough of a 2026-07-28 ``Context`` for the adapter."""

    is_background_task = False

    def __init__(self, *, state: str | None = None, responses: dict[str, Any] | None = None) -> None:
        self.request_context = SimpleNamespace(protocol_version="2026-07-28")
        self.session = SimpleNamespace(client_capabilities=SimpleNamespace(elicitation=object()))
        self.request_state = state
        self.input_responses = responses


def _guarded(fn: Any, name: str) -> Any:
    component = SimpleNamespace(fn=fn)
    install_confirmation_guard(component, name)
    return component.fn


_MUTATIONS: list[str] = []
_PREVIEW_SUFFIX = {"value": ""}


async def _delete_thing(thing_id: str, permanent: bool = True, ctx: Context | None = None) -> dict[str, Any]:
    if not await confirm_destructive_action(
        ctx, "delete_thing", f"Delete {thing_id}?{_PREVIEW_SUFFIX['value']}"
    ):
        return {"status": "cancelled"}
    _MUTATIONS.append(thing_id)
    return {"status": "deleted", "thing_id": thing_id}


async def _other_tool(thing_id: str, permanent: bool = True, ctx: Context | None = None) -> dict[str, Any]:
    if not await confirm_destructive_action(ctx, "delete_thing", f"Delete {thing_id}?"):
        return {"status": "cancelled"}
    _MUTATIONS.append("other:" + thing_id)
    return {"status": "deleted"}


async def _send_thing(to: str, ctx: Context | None = None) -> dict[str, Any]:
    if not await confirm_destructive_action(ctx, "send_thing", f"Send to {to}?", explicit_confirm_field=True):
        return {"status": "cancelled"}
    _MUTATIONS.append("sent:" + to)
    return {"status": "sent"}


DELETE = _guarded(_delete_thing, "delete_thing")
OTHER = _guarded(_other_tool, "other_tool")
SEND = _guarded(_send_thing, "send_thing")


@pytest.fixture(autouse=True)
def clear_mutations() -> Iterator[None]:
    _MUTATIONS.clear()
    _PREVIEW_SUFFIX["value"] = ""
    yield
    _MUTATIONS.clear()


def _run(fn: Any, *args: Any, **kwargs: Any) -> Any:
    return anyio.run(lambda: fn(*args, **kwargs))


def _ask(fn: Any = DELETE, *args: Any, **kwargs: Any) -> mcp_types.InputRequiredResult:
    result = _run(fn, *(args or ("t1",)), ctx=_ModernContext(), **kwargs)
    assert isinstance(result, mcp_types.InputRequiredResult)
    assert _MUTATIONS == []
    return result


def _accept(ask: mcp_types.InputRequiredResult, value: Any = True) -> dict[str, Any]:
    return answer(ask, "accept", value)


def _reject_reason(fn: Any, *args: Any, ctx: _ModernContext, **kwargs: Any) -> str:
    with pytest.raises(ConfirmationRejectedError) as raised:
        _run(fn, *args, ctx=ctx, **kwargs)
    assert _MUTATIONS == []
    envelope_action = raised.value.required_action
    assert envelope_action["action"] == "restart_confirmation"
    assert "No changes were made" in str(raised.value)
    return raised.value.reason


def test_ask_then_accept_runs_once_and_state_carries_no_plaintext() -> None:
    ask = _ask()
    state = ask.request_state or ""
    assert state.startswith("cw1.")
    # Only digests and ids: the preview text and argument values are absent.
    # Inspect the decoded claims, not the base64 text, where short substrings
    # can occur by chance.
    encoded_claims = state.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(encoded_claims + "=" * (-len(encoded_claims) % 4)))
    serialized_values = json.dumps(list(claims.values()))
    assert '"t1"' not in serialized_values and "Delete" not in serialized_values
    result = _run(DELETE, "t1", ctx=_ModernContext(state=state, responses=_accept(ask)))
    assert result == {"status": "deleted", "thing_id": "t1"}
    assert _MUTATIONS == ["t1"]


@pytest.mark.parametrize("action", ["decline", "cancel"])
def test_decline_and_cancel_do_not_mutate(action: str) -> None:
    ask = _ask()
    result = _run(DELETE, "t1", ctx=_ModernContext(state=ask.request_state, responses=answer(ask, action)))
    assert result == {"status": "cancelled"}
    assert _MUTATIONS == []


def test_accept_with_unchecked_box_is_not_consent() -> None:
    ask = _ask(SEND, "bob@example.com")
    result = _run(
        SEND, "bob@example.com", ctx=_ModernContext(state=ask.request_state, responses=_accept(ask, False))
    )
    assert result == {"status": "cancelled"}
    assert _MUTATIONS == []


def test_tampered_state_is_rejected() -> None:
    ask = _ask()
    prefix, payload, kid, mac = (ask.request_state or "").split(".")
    forged = payload[:-2] + ("A" if payload[-2] != "A" else "B") + payload[-1]
    ctx = _ModernContext(state=".".join([prefix, forged, kid, mac]), responses=_accept(ask))
    assert _reject_reason(DELETE, "t1", ctx=ctx) == "tampered"
    ctx = _ModernContext(state="cw1.not-a-state", responses=_accept(ask))
    assert _reject_reason(DELETE, "t1", ctx=ctx) == "tampered"


def test_expired_state_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    ask = _ask()
    later = time.time() + confirmation.DEFAULT_CONFIRMATION_TTL_SECONDS + 1
    monkeypatch.setattr(confirmation.time, "time", lambda: later)
    ctx = _ModernContext(state=ask.request_state, responses=_accept(ask))
    assert _reject_reason(DELETE, "t1", ctx=ctx) == "expired"


def test_ttl_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_CONFIRMATION_TTL_SECONDS", "60")
    ask = _ask()
    real_time = time.time
    monkeypatch.setattr(confirmation.time, "time", lambda: real_time() + 61)
    ctx = _ModernContext(state=ask.request_state, responses=_accept(ask))
    assert _reject_reason(DELETE, "t1", ctx=ctx) == "expired"
    monkeypatch.setenv("MCP_CONFIRMATION_TTL_SECONDS", "5")
    with pytest.raises(ValueError):
        confirmation.confirmation_ttl_seconds()


def test_another_principal_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    ask = _ask()
    monkeypatch.setenv("MCP_LOCAL_PRINCIPAL", "mallory")
    ctx = _ModernContext(state=ask.request_state, responses=_accept(ask))
    assert _reject_reason(DELETE, "t1", ctx=ctx) == "principal_mismatch"


def test_changed_arguments_are_rejected() -> None:
    ask = _ask()
    ctx = _ModernContext(state=ask.request_state, responses=_accept(ask))
    assert _reject_reason(DELETE, "t2", ctx=ctx) == "arguments_changed"
    ctx = _ModernContext(state=ask.request_state, responses=_accept(ask))
    assert _reject_reason(DELETE, "t1", permanent=False, ctx=ctx) == "arguments_changed"


def test_defaults_and_argument_order_do_not_change_the_digest() -> None:
    ask = _ask(DELETE, "t1")
    result = _run(DELETE, permanent=True, thing_id="t1", ctx=_ModernContext(state=ask.request_state, responses=_accept(ask)))
    assert result["status"] == "deleted"


def test_state_for_another_tool_is_rejected() -> None:
    ask = _ask(DELETE, "t1")
    ctx = _ModernContext(state=ask.request_state, responses=_accept(ask))
    assert _reject_reason(OTHER, "t1", ctx=ctx) == "tool_mismatch"


def test_changed_preview_is_rejected() -> None:
    """The user confirmed one exact preview; a different one is not consented."""
    ask = _ask()
    _PREVIEW_SUFFIX["value"] = " (now including 40 more recipients)"
    ctx = _ModernContext(state=ask.request_state, responses=_accept(ask))
    assert _reject_reason(DELETE, "t1", ctx=ctx) == "preview_changed"


def test_replayed_accepted_continuation_returns_the_saved_result() -> None:
    """W4b: a repeat of a succeeded operation replays its result, never re-executes.

    (W4a rejected this repeat as ``replayed``, which left a client that lost
    the first response with no result at all.)
    """
    ask = _ask()
    ctx = _ModernContext(state=ask.request_state, responses=_accept(ask))
    assert _run(DELETE, "t1", ctx=ctx)["status"] == "deleted"
    assert _MUTATIONS == ["t1"]
    replay = _run(DELETE, "t1", ctx=_ModernContext(state=ask.request_state, responses=_accept(ask)))
    assert isinstance(replay, ToolResult)
    assert replay.structured_content == {"status": "deleted", "thing_id": "t1"}
    assert (replay.meta or {})[OPERATION_META_KEY]["replayed"] is True
    assert _MUTATIONS == ["t1"]


def test_a_declined_continuation_cannot_be_reused_to_accept() -> None:
    ask = _ask()
    ctx = _ModernContext(state=ask.request_state, responses=answer(ask, "decline"))
    assert _run(DELETE, "t1", ctx=ctx) == {"status": "cancelled"}
    ctx = _ModernContext(state=ask.request_state, responses=_accept(ask))
    assert _reject_reason(DELETE, "t1", ctx=ctx) == "replayed"


@pytest.mark.parametrize(
    "responses",
    [
        {"confirm": mcp_types.ElicitResult(action="accept", content={"value": "yes"})},
        {"confirm": mcp_types.ElicitResult(action="accept", content={"value": 1})},
        {"confirm": mcp_types.ElicitResult(action="accept", content={"confirm": True})},
        {"confirm": mcp_types.ElicitResult(action="accept", content={"value": True, "extra": True})},
        {"confirm": mcp_types.ElicitResult(action="accept", content=None)},
        {"confirm": mcp_types.ListRootsResult(roots=[])},
    ],
    ids=["string", "integer", "wrong-field", "extra-field", "no-content", "roots-result"],
)
def test_answer_of_the_wrong_type_is_rejected(responses: dict[str, Any]) -> None:
    ask = _ask()
    ctx = _ModernContext(state=ask.request_state, responses=responses)
    assert _reject_reason(DELETE, "t1", ctx=ctx) == "wrong_answer_type"


@pytest.mark.parametrize("responses", [None, {}, {"other": mcp_types.ElicitResult(action="accept")}])
def test_missing_answer_on_retry_is_rejected(responses: dict[str, Any] | None) -> None:
    ask = _ask()
    ctx = _ModernContext(state=ask.request_state, responses=responses)
    assert _reject_reason(DELETE, "t1", ctx=ctx) == "missing_answer"


def test_answer_without_continuation_is_not_consent() -> None:
    ctx = _ModernContext(responses={"confirm": mcp_types.ElicitResult(action="accept", content={"value": True})})
    assert _reject_reason(DELETE, "t1", ctx=ctx) == "answer_without_continuation"


def test_rejections_do_not_consume_the_continuation() -> None:
    ask = _ask()
    bad = _ModernContext(state=ask.request_state, responses=None)
    assert _reject_reason(DELETE, "t1", ctx=bad) == "missing_answer"
    good = _ModernContext(state=ask.request_state, responses=_accept(ask))
    assert _run(DELETE, "t1", ctx=good)["status"] == "deleted"


def test_application_key_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_REQUEST_STATE_KEYS", KEY_OLD)
    reset_confirmation_keys()
    ask = _ask()
    monkeypatch.setenv("MCP_REQUEST_STATE_KEYS", f"{KEY_NEW},{KEY_OLD}")
    reset_confirmation_keys()
    probe = _ask(DELETE, "t9")  # new continuations are minted under the new key
    assert (probe.request_state or "").split(".")[2] != (ask.request_state or "").split(".")[2]
    ctx = _ModernContext(state=ask.request_state, responses=_accept(ask))
    assert _run(DELETE, "t1", ctx=ctx)["status"] == "deleted"
    _MUTATIONS.clear()

    second = _ask(DELETE, "t2")
    monkeypatch.setenv("MCP_REQUEST_STATE_KEYS", KEY_OLD)
    reset_confirmation_keys()
    # Minted under KEY_NEW, which is no longer listed.
    ctx = _ModernContext(state=second.request_state, responses=_accept(second))
    assert _reject_reason(DELETE, "t2", ctx=ctx) == "tampered"


def test_adapter_without_guard_fails_closed() -> None:
    async def unguarded(ctx: Any) -> bool:
        return await confirm_destructive_action(ctx, "delete_thing", "Delete?")

    with pytest.raises(ConfirmationRequiredError) as raised:
        _run(unguarded, _ModernContext())
    assert not isinstance(raised.value, ConfirmationInputRequired)


def test_escaped_ask_is_still_a_fail_closed_confirmation_error() -> None:
    assert issubclass(ConfirmationInputRequired, ConfirmationRequiredError)


def test_canonical_arguments_are_sorted_compact_and_typed() -> None:
    from pydantic import BaseModel

    class Request(BaseModel):
        b: int
        a: list[str]

    first = canonical_arguments({"z": Request(b=1, a=["x"]), "a": None})
    assert first == '{"a":null,"z":{"a":["x"],"b":1}}'
    assert canonical_arguments({"a": None, "z": {"b": 1, "a": ["x"]}}) == first
    with pytest.raises(ValueError):
        canonical_arguments({"x": float("nan")})


def test_key_ring_configuration_is_validated() -> None:
    assert configured_request_state_keys({"MCP_REQUEST_STATE_KEYS": f" {KEY_NEW} , {KEY_OLD} ,"}) == [KEY_NEW, KEY_OLD]
    assert configured_request_state_keys({}) == []
    with pytest.raises(ValueError):
        configured_request_state_keys({"MCP_REQUEST_STATE_KEYS": "short"})
    with pytest.raises(ValueError):
        configured_request_state_keys({"MCP_REQUEST_STATE_KEYS": f"{KEY_OLD},{KEY_OLD}"})
    security = build_request_state_security(environ={"MCP_REQUEST_STATE_KEYS": KEY_OLD})
    assert security.audience == "google-workspace-mcp"
    assert security.ttl == 600.0


@pytest.mark.parametrize("backend", ["memory", "redis"])
def test_memory_and_redis_operation_records_are_claimed_once(backend: str) -> None:
    """The W4b record store replaces the W4a replay set: one claim per operation."""
    import burner_redis
    from cryptography.fernet import Fernet

    from mcp_google_workspace.common.app_state import RedisAppStateStore
    from mcp_google_workspace.common.crypto import FernetKeyring

    store = (
        memory_operation_store()
        if backend == "memory"
        else OperationStore(
            RedisAppStateStore(
                burner_redis.BurnerRedis(), keyring=FernetKeyring.single(Fernet.generate_key().decode())
            )
        )
    )

    async def exercise() -> list[str]:
        await store.open_confirmation(
            "p:op", tool="t", action="a", args_digest="d", preview_digest="p", ttl_seconds=60
        )
        first = await store.claim("p:op", kind="confirmation", ref="r", answer="accept")
        second = await store.claim("p:op", kind="confirmation", ref="r", answer="accept")
        return [first.status, second.status]

    assert anyio.run(exercise) == ["claimed", "in_progress"]


# ---------------------------------------------------------------------------
# Full composition over a modern client (FastMCP seal + application checks)
# ---------------------------------------------------------------------------

_SITE = ("people_delete_contact", {"person_name": "people/c1"})


def _reload_workspace() -> FastMCP:
    return importlib.reload(server_module).workspace_mcp


@pytest.fixture
def workspace_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    for name in OPTIONAL_FLAGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MCP_RATE_LIMIT_PER_MINUTE", "100000")
    try:
        yield monkeypatch
    finally:
        monkeypatch.undo()
        reset_confirmation_keys()
        importlib.reload(server_module)


@pytest.fixture
def google_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: GoogleRecorder(calls))
    return calls


async def _never(*_args: Any) -> Any:  # pragma: no cover - rounds are driven by hand
    raise AssertionError("unexpected elicitation callback")


async def _call(
    server: FastMCP,
    tool: str,
    arguments: dict[str, Any],
    *,
    responses: dict[str, Any] | None = None,
    state: str | None = None,
) -> Any:
    async with Client(server, elicitation_handler=_never) as client:
        return await client.session.call_tool(
            tool, arguments, allow_input_required=True, input_responses=responses, request_state=state
        )


def _wire_ask(server: FastMCP, tool: str = _SITE[0], arguments: dict[str, Any] = _SITE[1]) -> mcp_types.InputRequiredResult:
    first = anyio.run(lambda: _call(server, tool, arguments))
    assert isinstance(first, mcp_types.InputRequiredResult)
    return first


def _wire_answer(
    server: FastMCP,
    ask: mcp_types.InputRequiredResult,
    *,
    tool: str = _SITE[0],
    arguments: dict[str, Any] = _SITE[1],
    responses: dict[str, Any] | None = None,
    state: str | None = None,
) -> Any:
    return anyio.run(
        lambda: _call(
            server,
            tool,
            arguments,
            responses=responses if responses is not None else answer(ask, "accept"),
            state=state if state is not None else ask.request_state,
        )
    )


def _invalid_code(result: Any) -> tuple[str, str]:
    assert isinstance(result, mcp_types.CallToolResult)
    assert result.is_error is True
    envelope = result.structured_content or {}
    return envelope["code"], envelope["required_action"]["reason"]


def test_wire_tampered_state_is_rejected_by_the_framework_seal(workspace_env, google_calls) -> None:
    server = _reload_workspace()
    ask = _wire_ask(server)
    state = ask.request_state or ""
    tampered = state[:-3] + ("A" if state[-3] != "A" else "B") + state[-2:]
    with pytest.raises(MCPError) as raised:
        _wire_answer(server, ask, state=tampered)
    assert raised.value.error.code == -32602
    assert mutations(google_calls) == []


def test_wire_expired_state_is_rejected(workspace_env, google_calls, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _reload_workspace()
    ask = _wire_ask(server)
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 601)
    try:
        with pytest.raises(MCPError) as raised:
            _wire_answer(server, ask)
    finally:
        monkeypatch.setattr(time, "time", real_time)
    assert raised.value.error.code == -32602
    assert mutations(google_calls) == []


def test_wire_changed_arguments_are_rejected(workspace_env, google_calls) -> None:
    server = _reload_workspace()
    ask = _wire_ask(server)
    with pytest.raises(MCPError) as raised:
        _wire_answer(server, ask, arguments={"person_name": "people/c2"})
    assert raised.value.error.code == -32602
    assert mutations(google_calls) == []


def test_wire_another_principal_is_rejected(workspace_env, google_calls) -> None:
    server = _reload_workspace()
    ask = _wire_ask(server)
    workspace_env.setenv("MCP_LOCAL_PRINCIPAL", "mallory")
    assert _invalid_code(_wire_answer(server, ask)) == ("confirmation_invalid", "principal_mismatch")
    assert mutations(google_calls) == []


def test_wire_replayed_accepted_continuation_returns_the_saved_result(workspace_env, google_calls) -> None:
    server = _reload_workspace()
    ask = _wire_ask(server)
    done = _wire_answer(server, ask)
    assert done.is_error is False
    assert len(mutations(google_calls)) == 1
    replay = _wire_answer(server, ask)
    assert replay.is_error is False
    assert replay.structured_content == done.structured_content
    assert replay.meta[OPERATION_META_KEY]["replayed"] is True
    assert len(mutations(google_calls)) == 1


@pytest.mark.parametrize(
    ("responses", "reason"),
    [
        ({"confirm": mcp_types.ElicitResult(action="accept", content={"value": "yes"})}, "wrong_answer_type"),
        ({"confirm": mcp_types.ListRootsResult(roots=[])}, "wrong_answer_type"),
        ({"unrelated": mcp_types.ElicitResult(action="accept", content={"value": True})}, "missing_answer"),
    ],
    ids=["string-answer", "roots-answer", "missing-answer"],
)
def test_wire_wrong_or_missing_answer_is_rejected(workspace_env, google_calls, responses, reason) -> None:
    server = _reload_workspace()
    ask = _wire_ask(server)
    assert _invalid_code(_wire_answer(server, ask, responses=responses)) == ("confirmation_invalid", reason)
    assert mutations(google_calls) == []


def test_wire_retry_with_state_but_no_answer_is_rejected(workspace_env, google_calls) -> None:
    server = _reload_workspace()
    ask = _wire_ask(server)
    result = anyio.run(lambda: _call(server, _SITE[0], _SITE[1], state=ask.request_state))
    assert _invalid_code(result) == ("confirmation_invalid", "missing_answer")
    assert mutations(google_calls) == []


def test_replica_switch_completes_a_flow_started_on_the_other_replica(workspace_env, google_calls) -> None:
    workspace_env.setenv("MCP_REQUEST_STATE_KEYS", KEY_OLD)
    reset_confirmation_keys()
    replica_a = _reload_workspace()
    replica_b = _reload_workspace()
    assert replica_a is not replica_b
    ask = _wire_ask(replica_a)
    assert mutations(google_calls) == []
    done = _wire_answer(replica_b, ask)
    assert done.is_error is False
    assert len(mutations(google_calls)) == 1
    # The shared operation store replays the saved result on either replica
    # instead of executing the same continuation again.
    replay = _wire_answer(replica_a, ask)
    assert replay.is_error is False
    assert replay.structured_content == done.structured_content
    assert len(mutations(google_calls)) == 1


def test_replica_without_the_shared_ring_rejects_foreign_state(workspace_env, google_calls) -> None:
    workspace_env.setenv("MCP_REQUEST_STATE_KEYS", KEY_OLD)
    reset_confirmation_keys()
    replica_a = _reload_workspace()
    ask = _wire_ask(replica_a)
    workspace_env.setenv("MCP_REQUEST_STATE_KEYS", KEY_OTHER)
    reset_confirmation_keys()
    replica_b = _reload_workspace()
    with pytest.raises(MCPError) as raised:
        _wire_answer(replica_b, ask)
    assert raised.value.error.code == -32602
    assert mutations(google_calls) == []


def test_key_rotation_old_key_verifies_while_listed_and_fails_after_removal(workspace_env, google_calls) -> None:
    workspace_env.setenv("MCP_REQUEST_STATE_KEYS", KEY_OLD)
    reset_confirmation_keys()
    before_rotation = _reload_workspace()
    first = _wire_ask(before_rotation)
    second = _wire_ask(before_rotation, arguments={"person_name": "people/c2"})

    # Phase 2: new key active, old key still listed.
    workspace_env.setenv("MCP_REQUEST_STATE_KEYS", f"{KEY_NEW},{KEY_OLD}")
    reset_confirmation_keys()
    rotating = _reload_workspace()
    assert _wire_answer(rotating, first).is_error is False
    assert len(mutations(google_calls)) == 1

    # Phase 3: old key removed.
    workspace_env.setenv("MCP_REQUEST_STATE_KEYS", KEY_NEW)
    reset_confirmation_keys()
    rotated = _reload_workspace()
    with pytest.raises(MCPError) as raised:
        _wire_answer(rotated, second, arguments={"person_name": "people/c2"})
    assert raised.value.error.code == -32602
    assert len(mutations(google_calls)) == 1
