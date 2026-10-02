"""Durable operation records and mutation recovery (W4b).

Plan section 8, "Mutation recovery": lost response after provider success,
duplicate commit, simultaneous commits, timeout while the provider thread
executes, saved-result replay across replicas, changed payload, expiry, the
decline/accept property, and the Gmail Message-ID / Calendar idempotency
reconciliation paths. Every scenario runs on the in-memory store and on the
Redis store (burner-redis), and Google is mocked at ``_build_service_now``.
"""

from __future__ import annotations

import base64
import email
import importlib
import json
import re
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import burner_redis
import httplib2
import mcp_types
import pytest
from cryptography.fernet import Fernet
from fastmcp import Client, Context, FastMCP
from fastmcp.tools import ToolResult
from googleapiclient.errors import HttpError
from mcp.shared.exceptions import MCPError

import mcp_google_workspace.auth.google_auth as google_auth
import mcp_google_workspace.server as server_module
from mcp_google_workspace.auth.identity import current_principal
from mcp_google_workspace.common import confirmation, operations
from mcp_google_workspace.common.app_state import RedisAppStateStore
from mcp_google_workspace.common.approvals import prepare_action
from mcp_google_workspace.common.confirmation import (
    confirm_destructive_action,
    install_confirmation_guard,
    reset_confirmation_keys,
)
from mcp_google_workspace.common.crypto import FernetKeyring
from mcp_google_workspace.common.errors import ConfirmationRejectedError, OperationOutcomeError
from mcp_google_workspace.common.operations import (
    OPERATION_META_KEY,
    REDIS_PREFIX,
    OperationStore,
    memory_operation_store,
    minimize_result,
    operation_key,
    set_operation_store,
)
from mcp_google_workspace.common.repeat_safety import (
    LateProviderCallError,
    MutationTracker,
    RepeatSafety,
    classify,
    generated_request_id,
    http_request_params,
    http_retry_budget,
    track_provider_call,
    tracking_scope,
)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from contracts.confirmation_sites import CANNED_RESPONSES, OPTIONAL_FLAGS, answer  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures: stores on both backends, a programmable Google, the composition
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _redis_store(client: Any, clock: Callable[[], float] = time.time) -> OperationStore:
    return OperationStore(
        RedisAppStateStore(client, prefix=REDIS_PREFIX, keyring=_KEYRING),
        clock=clock,
    )


_KEYRING = FernetKeyring.single(Fernet.generate_key().decode())


@pytest.fixture(params=["memory", "redis"])
def backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def redis_client() -> Any:
    return burner_redis.BurnerRedis()


@pytest.fixture
def store(backend: str, clock: FakeClock, redis_client: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[OperationStore]:
    monkeypatch.setenv("MCP_LOCAL_PRINCIPAL", "alice")
    monkeypatch.delenv("MCP_CONFIRMATION_TTL_SECONDS", raising=False)
    monkeypatch.delenv("MCP_REQUEST_STATE_KEYS", raising=False)
    reset_confirmation_keys()
    selected = memory_operation_store(clock=clock) if backend == "memory" else _redis_store(redis_client, clock)
    set_operation_store(selected)
    try:
        yield selected
    finally:
        set_operation_store(None)
        reset_confirmation_keys()


class Google:
    """Programmable stand-in for googleapiclient, installed at ``_build_service_now``.

    ``behaviors[method]`` receives the call's keyword arguments and returns the
    response (or raises); every executed call is recorded first, so a call
    counts as having reached Google even when its response is lost.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.behaviors: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self._lock = threading.Lock()

    def named(self, method: str) -> list[dict[str, Any]]:
        return [kwargs for name, kwargs in self.calls if name == method]


class _Chain:
    def __init__(self, google: Google, path: tuple[str, ...] = (), kwargs: dict[str, Any] | None = None) -> None:
        self._google = google
        self._path = path
        self._kwargs = kwargs or {}

    def __getattr__(self, name: str) -> "_Chain":
        if name.startswith("__"):
            raise AttributeError(name)
        return _Chain(self._google, (*self._path, name))

    def __call__(self, *_args: Any, **kwargs: Any) -> "_Chain":
        return _Chain(self._google, self._path, kwargs)

    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        method = ".".join(self._path)
        with self._google._lock:
            self._google.calls.append((method, self._kwargs))
        behavior = self._google.behaviors.get(method)
        if behavior is not None:
            return behavior(self._kwargs)
        return dict(CANNED_RESPONSES.get(method, {}))


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> Google:
    fake = Google()
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _Chain(fake))
    return fake


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


def _reload() -> FastMCP:
    return importlib.reload(server_module).workspace_mcp


async def _never(*_args: Any) -> Any:  # pragma: no cover - rounds are driven by hand
    raise AssertionError("unexpected elicitation callback")


async def _acall(
    server: FastMCP,
    tool: str,
    arguments: dict[str, Any],
    *,
    responses: dict[str, Any] | None = None,
    state: str | None = None,
) -> Any:
    async with Client(server, elicitation_handler=_never) as client:
        try:
            return await client.session.call_tool(
                tool, arguments, allow_input_required=True, input_responses=responses, request_state=state
            )
        except MCPError as exc:
            return exc


def _call(server: FastMCP, tool: str, arguments: dict[str, Any], **kwargs: Any) -> Any:
    return anyio.run(lambda: _acall(server, tool, arguments, **kwargs))


def _envelope(result: Any) -> dict[str, Any]:
    assert isinstance(result, mcp_types.CallToolResult), result
    assert result.is_error is True, result
    return dict(result.structured_content or {})


def _raw_message_id(send_kwargs: dict[str, Any]) -> str:
    raw = send_kwargs["body"]["raw"]
    parsed = email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    return str(parsed["Message-ID"])


def _socket_timeout_after_recording(_kwargs: dict[str, Any]) -> Any:
    # Google accepted the request (it is recorded) but the response is lost.
    raise socket.timeout("The read operation timed out")


SEND = {"to": ["bob@example.com"], "subject": "Quarterly numbers", "text_body": "Attached.", "confirm_send": True}
BULK = {
    "subject": "Announcement",
    "to": [f"person-{index}@example.com" for index in range(10)],
    "text_body": "Hello",
}


def _state(store: OperationStore, raw_id: str) -> str | None:
    record = anyio.run(lambda: store.get(operation_key(current_principal().storage_key, raw_id)))
    return None if record is None else str(record["state"])


def _prepare(tool: str, arguments: dict[str, Any]) -> str:
    return str(anyio.run(lambda: prepare_action(tool, arguments))["commit_token"])


# ---------------------------------------------------------------------------
# Lost response after provider success; duplicate commit
# ---------------------------------------------------------------------------


def test_lost_response_after_success_retry_returns_the_saved_result(workspace_env, store, google) -> None:
    """A confirmed send whose response never reached the client is not re-sent."""
    server = _reload()
    ask = _call(server, "gmail_send_email", SEND)
    assert isinstance(ask, mcp_types.InputRequiredResult)
    google.behaviors["users.messages.send"] = lambda _k: {"id": "sent-1", "threadId": "thr-1", "labelIds": ["SENT"]}

    first = _call(server, "gmail_send_email", SEND, responses=answer(ask, "accept"), state=ask.request_state)
    assert first.is_error is False
    assert first.structured_content["message_id"] == "sent-1"
    # ... the response is lost; the client retries the same answering round.
    again = _call(server, "gmail_send_email", SEND, responses=answer(ask, "accept"), state=ask.request_state)

    assert again.is_error is False
    assert again.structured_content == first.structured_content
    assert again.meta[OPERATION_META_KEY]["replayed"] is True
    assert len(google.named("users.messages.send")) == 1


def test_duplicate_commit_returns_the_saved_result_without_re_executing(workspace_env, store, google) -> None:
    server = _reload()
    token = _prepare("gmail_send_email", BULK)
    assert _state(store, token) == "prepared"
    google.behaviors["users.messages.send"] = lambda _k: {"id": "bulk-1", "threadId": "thr-9"}

    first = _call(server, "commit_workspace_action", {"commit_token": token})
    second = _call(server, "commit_workspace_action", {"commit_token": token})

    assert first.is_error is False and first.structured_content["status"] == "committed"
    assert second.is_error is False
    assert second.structured_content["result"]["message_id"] == "bulk-1"
    assert second.meta[OPERATION_META_KEY]["replayed"] is True
    assert len(google.named("users.messages.send")) == 1
    assert _state(store, token) == "succeeded"


def test_saved_results_do_not_retain_message_text() -> None:
    saved, retained = minimize_result(
        {"status": "ok", "message": {"name": "spaces/A/messages/B", "text": "secret plan", "thread": {"name": "t"}}}
    )
    assert retained is True
    assert saved["message"]["name"] == "spaces/A/messages/B"
    assert saved["message"]["text"] != "secret plan"
    assert "secret plan" not in json.dumps(saved)


def test_oversized_results_are_not_retained_and_replay_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_OPERATION_RESULT_MAX_BYTES", "1024")
    saved, retained = minimize_result({"rows": ["x" * 100] * 50})
    assert (saved, retained) == (None, False)
    with pytest.raises(OperationOutcomeError) as raised:
        operations.replay_result({"tool": "sheets_append", "result_retained": False}, "op_x")
    assert raised.value.error_code == "operation_already_succeeded"


# ---------------------------------------------------------------------------
# Simultaneous commits: exactly one executes
# ---------------------------------------------------------------------------


def test_simultaneous_claims_have_exactly_one_winner(store) -> None:
    token = _prepare("gmail_batch_modify", {"message_ids": ["m"] * 10})
    key = operation_key(current_principal().storage_key, token)

    async def race() -> list[str]:
        statuses: list[str] = []

        async def contend() -> None:
            outcome = await store.claim(key, kind="commit", ref="r")
            statuses.append(outcome.status)

        async with anyio.create_task_group() as group:
            for _ in range(8):
                group.start_soon(contend)
        return statuses

    statuses = anyio.run(race)
    assert statuses.count("claimed") == 1
    assert statuses.count("in_progress") == 7


def test_simultaneous_commits_execute_once(workspace_env, store, google) -> None:
    server = _reload()
    token = _prepare("gmail_send_email", BULK)
    started, release = threading.Event(), threading.Event()

    def slow_send(_kwargs: dict[str, Any]) -> Any:
        started.set()
        release.wait(10)
        return {"id": "bulk-2", "threadId": "thr-2"}

    google.behaviors["users.messages.send"] = slow_send

    async def scenario() -> tuple[Any, Any, Any]:
        results: dict[str, Any] = {}

        async def winner() -> None:
            results["a"] = await _acall(server, "commit_workspace_action", {"commit_token": token})

        async with anyio.create_task_group() as group:
            group.start_soon(winner)
            await anyio.to_thread.run_sync(started.wait, 10)
            results["b"] = await _acall(server, "commit_workspace_action", {"commit_token": token})
            release.set()
        results["c"] = await _acall(server, "commit_workspace_action", {"commit_token": token})
        return results["a"], results["b"], results["c"]

    try:
        first, concurrent, after = anyio.run(scenario)
    finally:
        release.set()
    assert first.is_error is False and first.structured_content["status"] == "committed"
    envelope = _envelope(concurrent)
    assert envelope["code"] == "operation_in_progress"
    assert envelope["retryable"] is True
    assert after.is_error is False and after.meta[OPERATION_META_KEY]["replayed"] is True
    assert len(google.named("users.messages.send")) == 1


# ---------------------------------------------------------------------------
# Timeout while the provider thread executes -> outcome_unknown
# ---------------------------------------------------------------------------


def test_deadline_while_a_send_executes_reports_outcome_unknown(workspace_env, store, google) -> None:
    """``run_blocking(..., abandon_on_cancel=True)`` cannot undo the in-flight send."""
    workspace_env.setenv("MCP_TOOL_DEADLINE_SECONDS", "1")
    server = _reload()
    release = threading.Event()

    def stuck_send(_kwargs: dict[str, Any]) -> Any:
        release.wait(10)
        return {"id": "late-1", "threadId": "thr-late"}

    google.behaviors["users.messages.send"] = stuck_send
    plain = {**SEND, "confirm_send": False}
    try:
        result = _call(server, "gmail_send_email", plain)
    finally:
        release.set()

    envelope = _envelope(result)
    assert envelope["code"] == "outcome_unknown"
    assert envelope["retryable"] is False
    action = envelope["required_action"]
    assert action["action"] == "verify_before_retry"
    assert action["uncertain_calls"] == ["gmail.users.messages.send"]
    assert action["operation_ref"].startswith("op_")
    message_id = _raw_message_id(google.named("users.messages.send")[0])
    assert {"tool": "gmail_search_emails", "arguments": {"query": f"in:sent rfc822msgid:{message_id}"}}.items() <= action[
        "verify"
    ][0].items()
    assert "outcome_unknown" in result.content[0].text


def test_timeout_during_a_confirmed_send_never_re_executes(workspace_env, store, google) -> None:
    workspace_env.setenv("MCP_TOOL_DEADLINE_SECONDS", "1")
    server = _reload()
    ask = _call(server, "gmail_send_email", SEND)
    release = threading.Event()
    google.behaviors["users.messages.send"] = lambda _k: (release.wait(10), {"id": "late-2"})[1]
    try:
        timed_out = _call(server, "gmail_send_email", SEND, responses=answer(ask, "accept"), state=ask.request_state)
    finally:
        release.set()
    assert _envelope(timed_out)["code"] == "outcome_unknown"

    retried = _call(server, "gmail_send_email", SEND, responses=answer(ask, "accept"), state=ask.request_state)
    assert _envelope(retried)["code"] == "outcome_unknown"
    assert len(google.named("users.messages.send")) == 1
    # The Sent search ran (reconciliation) and found nothing: still unknown.
    assert google.named("users.messages.list")


def test_non_idempotent_calls_get_no_transport_retries() -> None:
    assert http_retry_budget("gmail.users.messages.send", {}, 3) == 0
    assert http_retry_budget("sheets.spreadsheets.batchUpdate", {}, 3) == 0
    assert http_retry_budget("gmail.users.messages.get", {}, 3) == 3
    assert http_retry_budget("gmail.users.messages.modify", {}, 3) == 3
    assert http_retry_budget("calendar.events.insert", {"body": {"id": "mcpabc"}}, 3) == 3
    assert http_retry_budget("calendar.events.insert", {"body": {}}, 3) == 0
    assert http_retry_budget("chat.spaces.messages.create", {"requestId": generated_request_id()}, 3) == 3
    params = http_request_params(
        "https://chat.googleapis.com/v1/spaces/A/messages?requestId=abc&alt=json", '{"text": "hi"}'
    )
    assert params == {"requestId": "abc", "alt": "json", "body": {"text": "hi"}}


def test_retrying_http_request_applies_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from googleapiclient.http import HttpRequest

    seen: list[int] = []
    monkeypatch.setattr(HttpRequest, "execute", lambda self, http=None, num_retries=0: seen.append(num_retries))
    monkeypatch.setattr(google_auth, "get_runtime_settings", lambda: SimpleNamespace(http_retries=4))
    for method, uri, body in [
        ("gmail.users.messages.send", "https://g/send", '{"raw": "x"}'),
        ("gmail.users.messages.list", "https://g/list?q=x", None),
    ]:
        request = google_auth.RetryingHttpRequest(
            httplib2.Http(), lambda _r, content: content, uri, method="POST", body=body, methodId=method
        )
        request.execute()
    assert seen == [0, 4]


# ---------------------------------------------------------------------------
# Crash between claim and completion; late threads
# ---------------------------------------------------------------------------


def test_crash_between_claim_and_completion_becomes_outcome_unknown(workspace_env, store, clock, google) -> None:
    server = _reload()
    token = _prepare("gmail_send_email", BULK)
    key = operation_key(current_principal().storage_key, token)
    crashed = anyio.run(lambda: store.claim(key, kind="commit", ref="r"))
    assert crashed.status == "claimed"  # ... and that worker died.

    during_lease = _call(server, "commit_workspace_action", {"commit_token": token})
    assert _envelope(during_lease)["code"] == "operation_in_progress"

    clock.advance(operations.lease_seconds() + 1)
    after_lease = _call(server, "commit_workspace_action", {"commit_token": token})
    envelope = _envelope(after_lease)
    assert envelope["code"] == "outcome_unknown"
    assert envelope["required_action"]["uncertain_calls"] == ["(lease expired)"]
    assert _state(store, token) == "outcome_unknown"
    assert google.named("users.messages.send") == []  # never re-executed

    # The original claimant finishing late still records the real outcome.
    assert crashed.handle is not None
    anyio.run(lambda: store.succeed(crashed.handle, {"status": "committed"}))
    assert _state(store, token) == "succeeded"


def test_a_provider_call_started_after_the_tool_call_ended_is_refused() -> None:
    with tracking_scope() as tracker:
        tracker.close()
        with pytest.raises(LateProviderCallError):
            with track_provider_call("gmail.users.messages.send", {}):
                raise AssertionError("must not start")  # pragma: no cover
        with track_provider_call("gmail.users.messages.get", {}):  # reads are not tracked
            pass
    assert tracker.in_flight == {}


# ---------------------------------------------------------------------------
# Replica switch: two server instances sharing one store
# ---------------------------------------------------------------------------


def test_saved_result_replays_on_another_replica_sharing_the_store(workspace_env, backend, redis_client, clock, google) -> None:
    workspace_env.setenv("MCP_REQUEST_STATE_KEYS", "k" * 64)
    workspace_env.setenv("MCP_LOCAL_PRINCIPAL", "alice")
    reset_confirmation_keys()
    shared_memory = memory_operation_store(clock=clock)

    def replica_store() -> OperationStore:
        return shared_memory if backend == "memory" else _redis_store(redis_client, clock)

    replica_a, store_a = _reload(), replica_store()
    replica_b, store_b = _reload(), replica_store()
    google.behaviors["users.messages.send"] = lambda _k: {"id": "sent-r", "threadId": "thr-r"}
    try:
        set_operation_store(store_a)
        ask = _call(replica_a, "gmail_send_email", SEND)
        done = _call(replica_a, "gmail_send_email", SEND, responses=answer(ask, "accept"), state=ask.request_state)
        set_operation_store(store_b)
        replayed = _call(replica_b, "gmail_send_email", SEND, responses=answer(ask, "accept"), state=ask.request_state)
        token = _prepare("gmail_send_email", BULK)
        set_operation_store(store_a)
        committed = _call(replica_a, "commit_workspace_action", {"commit_token": token})
        set_operation_store(store_b)
        recommitted = _call(replica_b, "commit_workspace_action", {"commit_token": token})
    finally:
        set_operation_store(None)
    assert done.is_error is False
    assert replayed.is_error is False and replayed.structured_content == done.structured_content
    assert replayed.meta[OPERATION_META_KEY]["replayed"] is True
    assert committed.is_error is False
    assert recommitted.is_error is False and recommitted.meta[OPERATION_META_KEY]["replayed"] is True
    assert len(google.named("users.messages.send")) == 2  # one per operation


# ---------------------------------------------------------------------------
# Changed payload, decline/accept, expiry (application layer)
# ---------------------------------------------------------------------------


class _ModernContext:
    is_background_task = False

    def __init__(self, *, state: str | None = None, responses: dict[str, Any] | None = None) -> None:
        self.request_context = SimpleNamespace(protocol_version="2026-07-28")
        self.session = SimpleNamespace(client_capabilities=SimpleNamespace(elicitation=object()))
        self.request_state = state
        self.input_responses = responses


_DELETED: list[str] = []


async def _delete_thing(thing_id: str, ctx: Context | None = None) -> dict[str, Any]:
    if not await confirm_destructive_action(ctx, "delete_thing", f"Delete {thing_id}?"):
        return {"status": "cancelled"}
    _DELETED.append(thing_id)
    return {"status": "deleted", "thing_id": thing_id}


def _guarded() -> Any:
    component = SimpleNamespace(fn=_delete_thing)
    install_confirmation_guard(component, "delete_thing")
    return component.fn


DELETE = _guarded()


@pytest.fixture(autouse=True)
def _clear_deleted() -> Iterator[None]:
    _DELETED.clear()
    yield
    _DELETED.clear()


def _run(*args: Any, **kwargs: Any) -> Any:
    return anyio.run(lambda: DELETE(*args, **kwargs))


def _app_ask(thing: str = "t1") -> mcp_types.InputRequiredResult:
    result = _run(thing, ctx=_ModernContext())
    assert isinstance(result, mcp_types.InputRequiredResult)
    return result


def test_changed_payload_for_the_same_operation_is_rejected(store) -> None:
    ask = _app_ask("t1")
    done = _run("t1", ctx=_ModernContext(state=ask.request_state, responses=answer(ask, "accept")))
    assert done["status"] == "deleted"
    with pytest.raises(ConfirmationRejectedError) as raised:
        _run("t2", ctx=_ModernContext(state=ask.request_state, responses=answer(ask, "accept")))
    assert raised.value.reason == "arguments_changed"
    assert _DELETED == ["t1"]

    # At the record level too: a claim with another argument digest is refused.
    anyio.run(
        lambda: store.open_confirmation(
            "p:x", tool="t", action="a", args_digest="one", preview_digest="p", ttl_seconds=60
        )
    )
    outcome = anyio.run(lambda: store.claim("p:x", kind="confirmation", ref="r", args_digest="two"))
    assert outcome.status == "payload_changed"


def test_a_decline_after_an_accept_cannot_flip_the_outcome(store) -> None:
    ask = _app_ask()
    accepted = _run("t1", ctx=_ModernContext(state=ask.request_state, responses=answer(ask, "accept")))
    assert accepted["status"] == "deleted"
    with pytest.raises(ConfirmationRejectedError) as raised:
        _run("t1", ctx=_ModernContext(state=ask.request_state, responses=answer(ask, "decline")))
    assert raised.value.reason == "replayed"
    # ... and the saved outcome is still the accepted one.
    replay = _run("t1", ctx=_ModernContext(state=ask.request_state, responses=answer(ask, "accept")))
    assert isinstance(replay, ToolResult) and replay.structured_content["status"] == "deleted"
    assert _DELETED == ["t1"]


def test_a_decline_is_final_and_cannot_become_an_accept(store) -> None:
    ask = _app_ask()
    declined = _run("t1", ctx=_ModernContext(state=ask.request_state, responses=answer(ask, "decline")))
    assert declined == {"status": "cancelled"}
    with pytest.raises(ConfirmationRejectedError) as raised:
        _run("t1", ctx=_ModernContext(state=ask.request_state, responses=answer(ask, "accept")))
    assert raised.value.reason == "replayed"
    assert _DELETED == []


def test_expired_operations_cannot_be_claimed(backend, store, clock, monkeypatch: pytest.MonkeyPatch) -> None:
    # Continuation: the sealed claims expire with the record.
    ask = _app_ask()
    real_time = time.time
    monkeypatch.setattr(confirmation.time, "time", lambda: real_time() + confirmation.DEFAULT_CONFIRMATION_TTL_SECONDS + 61)
    with pytest.raises(ConfirmationRejectedError) as raised:
        _run("t1", ctx=_ModernContext(state=ask.request_state, responses=answer(ask, "accept")))
    assert raised.value.reason == "expired"
    monkeypatch.setattr(confirmation.time, "time", real_time)

    # Commit tokens and awaiting_input records: expired records are gone.
    token = _prepare("gmail_batch_modify", {"message_ids": ["m"] * 10})
    key = operation_key(current_principal().storage_key, token)
    if backend == "memory":
        clock.advance(operations.pending_ttl_seconds() + 1)
    else:
        anyio.run(lambda: store.open_confirmation("p:short", tool="t", action="a", args_digest="d", preview_digest="p", ttl_seconds=1))
        time.sleep(1.2)
        assert anyio.run(lambda: store.claim("p:short", kind="confirmation", ref="r")).status == "missing"
        clock.advance(operations.pending_ttl_seconds() + 1)  # pending_expires_at is checked on claim
    assert anyio.run(lambda: store.claim(key, kind="commit", ref="r")).status == "missing"
    assert _DELETED == []


def test_commit_token_and_its_confirmation_share_one_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """W4a: approval tokens lived 300 s while their confirmation lived 600 s."""
    monkeypatch.delenv("MCP_CONFIRMATION_TTL_SECONDS", raising=False)
    assert operations.pending_ttl_seconds() == confirmation.confirmation_ttl_seconds() == 600
    monkeypatch.setenv("MCP_CONFIRMATION_TTL_SECONDS", "120")
    assert operations.pending_ttl_seconds() == 120


def test_records_hold_no_continuation_prompt_or_argument_values(backend, store, redis_client) -> None:
    ask = _app_ask("people/c-secret")
    state = ask.request_state or ""
    if backend == "memory":
        entries = store._backend._entries  # type: ignore[attr-defined]
        stored = json.dumps([entry[1] for entry in entries.values()])
    else:
        keys = anyio.run(lambda: redis_client.keys("*"))
        stored = json.dumps([str(anyio.run(lambda k=k: redis_client.hgetall(k))) for k in keys])
        assert keys and all(key.decode().startswith(REDIS_PREFIX) for key in keys)
    op_id = json.loads(base64.urlsafe_b64decode(state.split(".")[1] + "==="))["op"]
    for secret in (state, op_id, "people/c-secret", "Delete people/c-secret?"):
        assert secret not in stored


# ---------------------------------------------------------------------------
# Reconciliation: Gmail Message-ID and Calendar idempotent create
# ---------------------------------------------------------------------------


def test_gmail_send_with_unknown_outcome_is_reconciled_by_message_id(workspace_env, store, google) -> None:
    server = _reload()
    ask = _call(server, "gmail_send_email", SEND)
    google.behaviors["users.messages.send"] = _socket_timeout_after_recording

    lost = _call(server, "gmail_send_email", SEND, responses=answer(ask, "accept"), state=ask.request_state)
    envelope = _envelope(lost)
    assert envelope["code"] == "outcome_unknown"
    message_id = _raw_message_id(google.named("users.messages.send")[0])
    assert message_id.startswith("<") and message_id.endswith("@mcp-google-workspace.local>")
    assert envelope["required_action"]["verify"][0]["arguments"] == {"query": f"in:sent rfc822msgid:{message_id}"}

    # Not visible in Sent yet: stays unknown, nothing is re-sent.
    google.behaviors["users.messages.list"] = lambda _k: {"resultSizeEstimate": 0}
    still = _call(server, "gmail_send_email", SEND, responses=answer(ask, "accept"), state=ask.request_state)
    assert _envelope(still)["code"] == "outcome_unknown"

    # Found by its Message-ID: the operation is resolved to succeeded.
    def sent_search(kwargs: dict[str, Any]) -> Any:
        assert kwargs["q"] == f"in:sent rfc822msgid:{message_id}"
        return {"messages": [{"id": "found-1", "threadId": "thr-found"}]}

    google.behaviors["users.messages.list"] = sent_search
    resolved = _call(server, "gmail_send_email", SEND, responses=answer(ask, "accept"), state=ask.request_state)
    assert resolved.is_error is False
    assert resolved.structured_content["status"] == "sent"
    assert resolved.structured_content["message_id"] == "found-1"
    assert resolved.meta[OPERATION_META_KEY]["reconciled"] is True
    assert len(google.named("users.messages.send")) == 1


def test_every_outgoing_email_carries_a_fresh_message_id(workspace_env, store, google) -> None:
    server = _reload()
    plain = {**SEND, "confirm_send": False}
    google.behaviors["users.messages.send"] = lambda _k: {"id": "x"}
    _call(server, "gmail_send_email", plain)
    _call(server, "gmail_send_email", plain)
    ids = [_raw_message_id(kwargs) for kwargs in google.named("users.messages.send")]
    assert len(set(ids)) == 2 and all(value.endswith("@mcp-google-workspace.local>") for value in ids)


def test_message_id_header_is_never_folded() -> None:
    from email.message import EmailMessage

    from mcp_google_workspace.gmail.mime_utils import stamp_message_id

    for _ in range(50):
        message = EmailMessage()
        message_id = stamp_message_id(message)
        assert re.fullmatch(r"<[0-9a-f]{32}@mcp-google-workspace\.local>", message_id)
        header_lines = [
            line for line in message.as_bytes().split(b"\n") if line.lower().startswith(b"message-id:")
        ]
        assert header_lines == [f"Message-ID: {message_id}".encode()]


def _http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b"{}")


def test_calendar_create_with_idempotency_key_is_retry_safe(workspace_env, store, google) -> None:
    """Lost insert response: the retry finds the event by its idempotent id."""
    server = _reload()
    created: dict[str, Any] = {}

    def lookup(kwargs: dict[str, Any]) -> Any:
        if created and kwargs["eventId"] == created["id"]:
            return dict(created)
        raise _http_error(404)

    def insert(kwargs: dict[str, Any]) -> Any:
        created.update(kwargs["body"])  # Google stored it ...
        raise socket.timeout("timed out")  # ... and the response was lost

    google.behaviors["events.get"] = lookup
    google.behaviors["events.insert"] = insert
    arguments = {
        "summary": "Planning",
        "start_datetime": "2026-10-01T10:00:00Z",
        "end_datetime": "2026-10-01T11:00:00Z",
        "timezone": "UTC",
        "idempotency_key": "planning-2026-10-01",
    }

    first = _call(server, "calendar_create_event", arguments)
    # Repeat safe (deterministic event id): an ordinary retryable failure,
    # not outcome_unknown. W5: a Google API failure is a tool execution error
    # (isError result), not a JSON-RPC error.
    assert not isinstance(first, MCPError), first
    assert first.is_error is True
    assert first.structured_content["code"] == "timeout"
    assert first.structured_content["retryable"] is True

    retry = _call(server, "calendar_create_event", arguments)
    assert retry.is_error is False
    assert retry.structured_content["deduplicated"] is True
    assert retry.structured_content["event"]["id"] == created["id"]
    assert len(google.named("events.insert")) == 1
    assert classify("calendar.events.insert", {"body": {"id": created["id"]}}) is RepeatSafety.CALLER_KEYED


def test_calendar_create_without_idempotency_key_reports_outcome_unknown(workspace_env, store, google) -> None:
    server = _reload()
    google.behaviors["events.insert"] = _socket_timeout_after_recording
    result = _call(
        server,
        "calendar_create_event",
        {"summary": "Planning", "start_datetime": "2026-10-01T10:00:00Z", "end_datetime": "2026-10-01T11:00:00Z", "timezone": "UTC"},
    )
    envelope = _envelope(result)
    assert envelope["code"] == "outcome_unknown"
    assert envelope["required_action"]["verify"][0]["tool"] == "calendar_search_events"
    assert len(google.named("events.insert")) == 1


# ---------------------------------------------------------------------------
# Repeat-safety table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "params", "expected"),
    [
        ("gmail.users.messages.send", {}, RepeatSafety.NON_IDEMPOTENT),
        ("gmail.users.drafts.send", {}, RepeatSafety.NON_IDEMPOTENT),
        ("gmail.users.messages.modify", {}, RepeatSafety.IDEMPOTENT),
        ("gmail.users.messages.batchDelete", {}, RepeatSafety.IDEMPOTENT),
        ("gmail.users.messages.get", {}, RepeatSafety.READ),
        ("gmail.users.getProfile", {}, RepeatSafety.READ),
        ("calendar.events.insert", {"body": {"summary": "x"}}, RepeatSafety.NON_IDEMPOTENT),
        ("calendar.events.insert", {"body": {"id": "mcpabc"}}, RepeatSafety.CALLER_KEYED),
        ("calendar.events.patch", {}, RepeatSafety.IDEMPOTENT),
        ("drive.files.create", {"body": {"name": "a"}}, RepeatSafety.NON_IDEMPOTENT),
        ("drive.files.copy", {}, RepeatSafety.NON_IDEMPOTENT),
        ("drive.files.update", {}, RepeatSafety.IDEMPOTENT),
        ("drive.permissions.create", {}, RepeatSafety.NON_IDEMPOTENT),
        ("drive.drives.create", {"requestId": "abc"}, RepeatSafety.CALLER_KEYED),
        ("sheets.spreadsheets.batchUpdate", {}, RepeatSafety.NON_IDEMPOTENT),
        ("sheets.spreadsheets.values.append", {}, RepeatSafety.NON_IDEMPOTENT),
        ("sheets.spreadsheets.values.update", {}, RepeatSafety.IDEMPOTENT),
        ("docs.documents.batchUpdate", {"body": {"requests": []}}, RepeatSafety.NON_IDEMPOTENT),
        (
            "docs.documents.batchUpdate",
            {"body": {"writeControl": {"requiredRevisionId": "r1"}}},
            RepeatSafety.CALLER_KEYED,
        ),
        ("slides.presentations.batchUpdate", {}, RepeatSafety.NON_IDEMPOTENT),
        ("forms.forms.batchUpdate", {}, RepeatSafety.NON_IDEMPOTENT),
        ("tasks.tasks.insert", {}, RepeatSafety.NON_IDEMPOTENT),
        ("people.people.createContact", {}, RepeatSafety.NON_IDEMPOTENT),
        ("keep.notes.create", {}, RepeatSafety.NON_IDEMPOTENT),
        ("chat.spaces.messages.create", {}, RepeatSafety.NON_IDEMPOTENT),
        ("chat.spaces.messages.create", {"requestId": "caller-key"}, RepeatSafety.CALLER_KEYED),
        ("chat.spaces.messages.create", {"requestId": generated_request_id()}, RepeatSafety.TRANSPORT_KEYED),
        ("meet.spaces.create", {}, RepeatSafety.NON_IDEMPOTENT),
        ("unknown.things.frobnicate", {}, RepeatSafety.NON_IDEMPOTENT),
        ("unknown.things.list", {}, RepeatSafety.READ),
    ],
)
def test_repeat_safety_is_classified_from_the_google_method(method: str, params: dict[str, Any], expected: RepeatSafety) -> None:
    assert classify(method, params) is expected


def test_every_mutating_method_the_server_calls_is_in_the_table() -> None:
    """Scan the source for Google method chains; mutating ones need explicit entries."""
    import ast

    from mcp_google_workspace.common.repeat_safety import METHOD_POLICIES

    root = Path(__file__).resolve().parent.parent / "src" / "mcp_google_workspace"
    api_of = {"gmail": "gmail", "calendar": "calendar", "drive": "drive", "sheets": "sheets", "docs": "docs",
              "tasks": "tasks", "people": "people", "forms": "forms", "slides": "slides", "keep": "keep",
              "chat": "chat", "meet": "meet"}
    missing: set[str] = set()
    found: set[str] = set()
    for package, api in api_of.items():
        for path in (root / package).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                if not node.args and not node.keywords:
                    continue  # a resource accessor such as users() or messages()
                chain, current = [node.func.attr], node.func.value
                while isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute) and not current.args and not current.keywords:
                    chain.append(current.func.attr)
                    current = current.func.value
                if len(chain) < 2:
                    continue
                method = ".".join([api, *reversed(chain)])
                found.add(method)
                if classify(method) is not RepeatSafety.READ and method not in METHOD_POLICIES:
                    missing.add(method)
    assert sorted(missing) == []
    assert {
        "gmail.users.messages.send",
        "calendar.events.insert",
        "drive.files.create",
        "sheets.spreadsheets.batchUpdate",
        "docs.documents.batchUpdate",
        "chat.spaces.messages.create",
        "keep.notes.create",
    } <= found


def test_tracker_verdicts() -> None:
    tracker = MutationTracker()
    with pytest.raises(TimeoutError):
        with _tracked(tracker, "gmail.users.messages.send"):
            raise TimeoutError
    assert [call.method_id for call in tracker.uncertain_calls(body_failed=False)] == ["gmail.users.messages.send"]

    idempotent = MutationTracker()
    with pytest.raises(TimeoutError):
        with _tracked(idempotent, "gmail.users.messages.trash"):
            raise TimeoutError
    assert idempotent.uncertain_calls(body_failed=True) == []
    assert idempotent.attempted_mutation() is True

    rejected = MutationTracker()
    with pytest.raises(HttpError):
        with _tracked(rejected, "gmail.users.messages.send"):
            raise _http_error(400)
    assert rejected.uncertain_calls(body_failed=True) == []
    assert rejected.rejected_non_repeatable() is True

    server_error = MutationTracker()
    with pytest.raises(HttpError):
        with _tracked(server_error, "gmail.users.messages.send"):
            raise _http_error(503)
    assert server_error.uncertain_calls(body_failed=False)  # a 5xx send may have been delivered

    applied = MutationTracker()
    with _tracked(applied, "gmail.users.messages.send"):
        pass
    assert applied.uncertain_calls(body_failed=False) == []
    assert applied.uncertain_calls(body_failed=True)  # sent, but the caller got no result


def _tracked(tracker: MutationTracker, method: str) -> Any:
    from contextlib import contextmanager

    from mcp_google_workspace.common import repeat_safety

    @contextmanager
    def scope() -> Iterator[None]:
        token = repeat_safety._TRACKER.set(tracker)
        try:
            with track_provider_call(method, {}):
                yield
        finally:
            repeat_safety._TRACKER.reset(token)

    return scope()
