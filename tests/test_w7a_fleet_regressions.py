"""W7a: regressions for defects the Docker fleet qualification exposed.

The fleet suite itself (``test_fleet_qualification``) needs Docker and runs
only with ``MCP_FLEET_TEST=1``; these tests pin each fix in the default suite.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Any

import anyio
import httpx
import pytest
import uvicorn
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from http_harness import reserve_sockets, socket_url

from mcp_google_workspace import server_http, task_worker
from mcp_google_workspace.common.deployment_guard import (
    TEST_ONLY_FAKE_GOOGLE_ENV,
    reject_test_only_settings,
)
from mcp_google_workspace.common.production import shutdown_grace_seconds
from mcp_google_workspace.common.task_backend import resolve_task_backend_config

REPO = Path(__file__).resolve().parent.parent
FAKE_GOOGLE = REPO / "deploy" / "fleet-test" / "fake_google"


# ---------------------------------------------------------------------------
# Drain: in-flight HTTP requests get the configured grace, not FastMCP's 2 s
# ---------------------------------------------------------------------------


def test_uvicorn_graceful_shutdown_is_the_configured_drain_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_SHUTDOWN_GRACE_SECONDS", "17")
    assert shutdown_grace_seconds() == 17
    assert server_http.uvicorn_options() == {"timeout_graceful_shutdown": 17}
    monkeypatch.delenv("MCP_SHUTDOWN_GRACE_SECONDS")
    assert server_http.uvicorn_options() == {"timeout_graceful_shutdown": 30}


def test_http_main_passes_the_drain_window_to_uvicorn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    for name, value in {
        "MCP_HTTP_BASE_URL": "http://127.0.0.1:8000",
        "MCP_GOOGLE_OAUTH_REDIRECT_URL": "http://127.0.0.1:8000/google/oauth/callback",
        "MCP_HTTP_JWT_ISSUER": "https://issuer.w7a.test",
        "MCP_HTTP_JWT_AUDIENCE": "w7a",
        "MCP_HTTP_JWKS_URI": "https://issuer.w7a.test/jwks.json",
        "MCP_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "MCP_USER_TOKEN_DIR": str(tmp_path),
        "MCP_SHUTDOWN_GRACE_SECONDS": "12",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(TEST_ONLY_FAKE_GOOGLE_ENV, raising=False)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(server_http, "configure_remote_tool_search", lambda: None)
    monkeypatch.setattr(server_http, "register_oauth_callback_route", lambda _server: None)
    monkeypatch.setattr(server_http.workspace_mcp, "auth", server_http.workspace_mcp.auth)
    monkeypatch.setattr(server_http.workspace_mcp, "run", lambda **kwargs: captured.update(kwargs))
    server_http.main()
    assert captured["uvicorn_config"] == {"timeout_graceful_shutdown": 12}
    assert captured["transport"] == "http" and captured["stateless_http"] is False


def _serve_until_exit(server: uvicorn.Server, sock: Any, done: threading.Event) -> None:
    try:
        asyncio.run(server.serve(sockets=[sock]))
    finally:
        done.set()


@pytest.mark.parametrize(("grace", "completes"), [(10, True), (1, False)])
def test_in_flight_request_survives_shutdown_only_within_the_grace(grace: int, completes: bool) -> None:
    started = threading.Event()

    async def slow(_: Request) -> JSONResponse:
        started.set()
        await anyio.sleep(2.5)
        return JSONResponse({"finished": True})

    app = Starlette(routes=[Route("/slow", slow)])
    (sock,) = reserve_sockets(1)
    config = uvicorn.Config(app, lifespan="off", log_level="warning", ws="none", timeout_graceful_shutdown=grace)
    server = uvicorn.Server(config)
    done = threading.Event()
    thread = threading.Thread(target=_serve_until_exit, args=(server, sock, done), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.02)
        result: dict[str, Any] = {}

        def request() -> None:
            try:
                result["response"] = httpx.get(f"{socket_url(sock)}/slow", timeout=15)
            except httpx.HTTPError as exc:
                result["error"] = exc

        client = threading.Thread(target=request)
        client.start()
        assert started.wait(10)
        server.should_exit = True  # what uvicorn's SIGTERM handler does
        client.join(15)
        assert done.wait(15)
    finally:
        server.should_exit = True
        thread.join(15)
        sock.close()
    if completes:
        assert result["response"].status_code == 200
        assert result["response"].json() == {"finished": True}
    else:
        assert "error" in result or result["response"].status_code >= 500


# ---------------------------------------------------------------------------
# Oversized bodies: answered 413 without an ASGI exception
# ---------------------------------------------------------------------------


def test_oversized_chunked_body_is_a_413_without_an_application_exception() -> None:
    from mcp_google_workspace.common.production import RequestSizeLimitMiddleware

    reached: list[bytes] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        # What the SDK does: read the whole body through Starlette.
        reached.append(await Request(scope, receive).body())

    limited = RequestSizeLimitMiddleware(app, max_bytes=1024)
    chunks = [{"type": "http.request", "body": b"x" * 600, "more_body": True} for _ in range(4)]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return chunks.pop(0) if chunks else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": [(b"transfer-encoding", b"chunked")]}
    anyio.run(limited, scope, receive, send)  # must not raise ClientDisconnect
    assert reached == []
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 413


def test_real_client_disconnect_still_propagates() -> None:
    from starlette.requests import ClientDisconnect

    from mcp_google_workspace.common.production import RequestSizeLimitMiddleware

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await Request(scope, receive).body()

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(_: dict[str, Any]) -> None:
        return None

    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": []}
    with pytest.raises(ClientDisconnect):
        anyio.run(RequestSizeLimitMiddleware(app, max_bytes=1024), scope, receive, send)


# ---------------------------------------------------------------------------
# Readiness: an empty task snapshot key is not a key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "encrypted"),
    [(None, False), ("", False), ("   ", False), ("k" * 40, True)],
)
def test_task_snapshot_encryption_requires_a_non_empty_key(
    monkeypatch: pytest.MonkeyPatch, value: str | None, encrypted: bool
) -> None:
    if value is None:
        monkeypatch.delenv("FASTMCP_TASKS_ENCRYPTION_KEY", raising=False)
    else:
        monkeypatch.setenv("FASTMCP_TASKS_ENCRYPTION_KEY", value)
    config = resolve_task_backend_config({"MCP_REDIS_URL": "redis://redis:6379/0"})
    assert config.distributed is True
    assert config.snapshot_encryption is encrypted


# ---------------------------------------------------------------------------
# Task redelivery: a task whose worker died is never executed twice
# ---------------------------------------------------------------------------


@pytest.fixture()
def operations() -> Any:
    from mcp_google_workspace.common.operations import memory_operation_store, set_operation_store

    store = memory_operation_store()
    set_operation_store(store)
    try:
        yield store
    finally:
        set_operation_store(None)


def _task_key(task_id: str) -> str:
    from mcp_google_workspace.common.operations import current_principal_key, operation_key

    return operation_key(current_principal_key(), f"task:{task_id}")


def test_first_task_delivery_runs_and_a_repeat_replays_the_saved_result(operations: Any) -> None:
    from mcp_google_workspace.common.execution import run_task_delivery
    from mcp_google_workspace.common.operations import OPERATION_META_KEY

    runs: list[int] = []

    async def body() -> dict[str, Any]:
        runs.append(1)
        return {"spreadsheetId": "s-1", "replies": [{}]}

    first = anyio.run(run_task_delivery, "sheets_batch_update_spreadsheet", "t-1", body)
    assert first == {"spreadsheetId": "s-1", "replies": [{}]}
    again = anyio.run(run_task_delivery, "sheets_batch_update_spreadsheet", "t-1", body)
    assert runs == [1]
    assert again.structured_content == {"spreadsheetId": "s-1", "replies": [{}]}
    assert again.meta[OPERATION_META_KEY]["replayed"] is True


def test_redelivery_of_a_task_that_started_is_outcome_unknown_not_a_second_run(operations: Any) -> None:
    from mcp_google_workspace.common.errors import OperationOutcomeError
    from mcp_google_workspace.common.execution import run_task_delivery
    from mcp_google_workspace.common.operations import REDELIVERED_TASK, operation_ref

    key = _task_key("t-2")
    # The first delivery claimed the task and its worker was killed mid-run.
    claimed = anyio.run(
        lambda: operations.claim_task_delivery(key, tool="sheets_batch_update_spreadsheet", ref=operation_ref("task:t-2"))
    )
    assert claimed.status == "claimed"
    runs: list[int] = []

    async def body() -> dict[str, Any]:
        runs.append(1)
        return {}

    with pytest.raises(OperationOutcomeError) as raised:
        anyio.run(run_task_delivery, "sheets_batch_update_spreadsheet", "t-2", body)
    assert runs == []
    assert raised.value.error_code == "outcome_unknown"
    assert raised.value.required_action["uncertain_calls"] == [REDELIVERED_TASK]
    record = anyio.run(operations.get, key)
    assert record["state"] == "outcome_unknown" and record["redelivered"] is True


def test_a_task_that_failed_before_google_runs_again_on_redelivery(operations: Any) -> None:
    from mcp_google_workspace.common.execution import run_task_delivery

    runs: list[int] = []

    async def failing() -> dict[str, Any]:
        runs.append(1)
        raise ValueError("invalid request before any Google call")

    with pytest.raises(ValueError):
        anyio.run(run_task_delivery, "sheets_batch_update_spreadsheet", "t-3", failing)
    assert anyio.run(operations.get, _task_key("t-3"))["state"] == "prepared"

    async def succeeding() -> dict[str, Any]:
        runs.append(2)
        return {"ok": True}

    assert anyio.run(run_task_delivery, "sheets_batch_update_spreadsheet", "t-3", succeeding) == {"ok": True}
    assert runs == [1, 2]


# ---------------------------------------------------------------------------
# Task worker: graceful stop, redacted banner
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "redacted"),
    [
        ("rediss://mcp:s3cr3t@redis:6379/0", "rediss://mcp:***@redis:6379/0"),
        ("redis://:s3cr3t@redis/1", "redis://:***@redis/1"),
        ("redis://redis:6379/0", "redis://redis:6379/0"),
        ("memory://", "memory://"),
    ],
)
def test_worker_banner_never_prints_the_backend_password(url: str, redacted: str) -> None:
    assert task_worker.redact_url(url) == redacted


def _lifespan_server(events: list[str]) -> FastMCP:
    @asynccontextmanager
    async def lifespan(_: Any):
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    return FastMCP("w7a-worker", lifespan=lifespan)


def test_worker_serve_runs_the_lifespan_until_stopped() -> None:
    events: list[str] = []
    server = _lifespan_server(events)

    async def scenario() -> None:
        stop = asyncio.Event()
        runner = asyncio.create_task(task_worker.serve(server, stop))
        while "enter" not in events:
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(runner, 10)

    asyncio.run(scenario())
    assert events == ["enter", "exit"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")
def test_worker_serve_stops_gracefully_on_sigterm() -> None:
    events: list[str] = []
    server = _lifespan_server(events)

    async def scenario() -> None:
        runner = asyncio.create_task(task_worker.serve(server))
        while "enter" not in events:
            await asyncio.sleep(0.01)
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(runner, 10)

    asyncio.run(scenario())
    assert events == ["enter", "exit"]


# ---------------------------------------------------------------------------
# Test-only fake Google: production refuses the switch; the fake guards itself
# ---------------------------------------------------------------------------


def test_production_refuses_the_fake_google_switch_without_the_fake() -> None:
    reject_test_only_settings({}, {})
    with pytest.raises(SystemExit, match="test-only"):
        reject_test_only_settings({TEST_ONLY_FAKE_GOOGLE_ENV: "fleet-qualification-only"}, {})
    with pytest.raises(SystemExit):
        reject_test_only_settings({TEST_ONLY_FAKE_GOOGLE_ENV: ""}, {})
    # Only inside the fleet-test image, where the hook validated itself.
    reject_test_only_settings(
        {TEST_ONLY_FAKE_GOOGLE_ENV: "fleet-qualification-only"}, {"fleet_fake_google.hook": object()}
    )


def test_http_and_worker_entrypoints_refuse_the_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TEST_ONLY_FAKE_GOOGLE_ENV, "fleet-qualification-only")
    monkeypatch.setattr(server_http.workspace_mcp, "run", lambda **_: pytest.fail("server started"))
    with pytest.raises(SystemExit):
        server_http.main()
    monkeypatch.setattr(task_worker, "serve", lambda *_: pytest.fail("worker started"))
    with pytest.raises(SystemExit):
        task_worker.main()


def _hook(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    clean = {key: value for key, value in os.environ.items() if not key.startswith(("MCP_", "PYTHONPATH"))}
    return subprocess.run(
        [sys.executable, "-c", "import fleet_fake_google.hook as h; print('active', h.activate.__name__)"],
        env={**clean, "PYTHONPATH": str(FAKE_GOOGLE), **env},
        text=True, capture_output=True, timeout=60, check=False,
    )


TEST_IDENTITY = {
    "MCP_HTTP_JWT_ISSUER": "https://issuer.fleet.test",
    "MCP_HTTP_JWKS_URI": "https://issuer.fleet.test/jwks.json",
    "MCP_HTTP_BASE_URL": "https://localhost:18443/gw",
}


def test_fake_google_is_inert_without_its_switch() -> None:
    result = _hook({})
    assert result.returncode == 0, result.stderr
    assert "TEST-ONLY" not in result.stderr


@pytest.mark.parametrize(
    ("env", "reason"),
    [
        ({**TEST_IDENTITY, "MCP_FLEET_FAKE_GOOGLE": "1"}, "must be exactly"),
        ({**TEST_IDENTITY, "MCP_FLEET_FAKE_GOOGLE": "fleet-qualification-only",
          "MCP_HTTP_JWT_ISSUER": "https://accounts.example.com"}, "MCP_HTTP_JWT_ISSUER"),
        ({**TEST_IDENTITY, "MCP_FLEET_FAKE_GOOGLE": "fleet-qualification-only",
          "MCP_HTTP_JWKS_URI": "https://www.googleapis.com/oauth2/v3/certs"}, "MCP_HTTP_JWKS_URI"),
        ({**TEST_IDENTITY, "MCP_FLEET_FAKE_GOOGLE": "fleet-qualification-only",
          "MCP_HTTP_BASE_URL": "https://mcp.example.com"}, "not loopback"),
    ],
)
def test_fake_google_refuses_to_activate_outside_the_test_identity(env: dict[str, str], reason: str) -> None:
    result = _hook(env)
    assert result.returncode == 78, (result.stdout, result.stderr)
    assert reason in result.stderr


def test_fake_google_activates_only_for_the_test_identity() -> None:
    result = _hook({**TEST_IDENTITY, "MCP_FLEET_FAKE_GOOGLE": "fleet-qualification-only"})
    assert result.returncode == 0, result.stderr
    assert "TEST-ONLY fake transport" in result.stderr
