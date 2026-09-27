"""W5: background-task executions enforce authorization, admission, deadlines
and error envelopes at *execution* time.

A Tasks-extension worker calls the raw tool function without any FastMCP
middleware. These tests submit real tasks over authenticated HTTP (uvicorn on
an ephemeral port, in-process ``memory://`` Docket worker with concurrency 1)
and check what the worker does, not what the submission path did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import threading
import time
from typing import Any
from uuid import uuid4

import anyio
import httpx
import pytest
from cryptography.fernet import Fernet
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp_tasks import TasksExtension, call_tool_task
from googleapiclient.errors import HttpError
from httplib2 import Response

from http_harness import LiveServers, SigningKeys, mcp_body, mcp_headers, mint, reserve_sockets

import mcp_google_workspace.auth.google_auth as google_auth
from mcp_google_workspace.auth.grants import clear_grant_cache
from mcp_google_workspace.auth.identity import Principal, PrincipalRequiredError, current_principal
from mcp_google_workspace.auth.remote_auth import build_jwt_verifier, build_remote_auth
from mcp_google_workspace.common.admission import AdmissionController, AdmissionLimits
from mcp_google_workspace.common.component_annotations import apply_default_tool_annotations
from mcp_google_workspace.common.errors import ProtocolRejection, provider_tool_error
from mcp_google_workspace.common.production import (
    RUNTIME_STATE,
    CapabilityCatalogMiddleware,
    ProductionControlMiddleware,
    production_lifespan,
    set_admission_controller,
)
from mcp_google_workspace.common.errors import StructuredToolErrorMiddleware
from mcp_google_workspace.runtime import RemoteSecuritySettings
from mcp_google_workspace.server_http import HttpServingPolicy, build_http_app

ISSUER = "https://issuer.tasks.test"
AUDIENCE = "tasks-mcp"
TIMEOUT = 15.0
TASKS_EXTENSION = {"extensions": {"io.modelcontextprotocol/tasks": {}}}


@dataclass
class Gate:
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)


def _credentials(capabilities: list[str]) -> str:
    return json.dumps(
        {
            "token": "google-access-token",
            "refresh_token": "google-refresh-token",
            "client_id": "google-client",
            "client_secret": "google-secret",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": google_auth.get_google_scopes(capabilities),
            "expiry": "2099-01-01T00:00:00Z",
        }
    )


def _server(gate: Gate, limits: AdmissionLimits) -> FastMCP:
    # The "sheets-" name prefix makes these tools sheets_* capability-bound
    # tools, exactly like the mounted Sheets namespace.
    server = FastMCP("sheets-guard-test", lifespan=production_lifespan)

    @server.tool(task=True)
    async def whoami(wait: bool = False) -> dict[str, Any]:
        """Report the principal the worker runs as."""
        if wait:
            gate.started.set()
            await anyio.to_thread.run_sync(gate.release.wait, TIMEOUT)
        principal = current_principal()
        return {
            "issuer": principal.issuer,
            "subject": principal.subject,
            "running": RUNTIME_STATE.active_tasks,
        }

    @server.tool(task=True)
    async def failing(spreadsheet_id: str) -> dict[str, Any]:
        """Fail like a Google outage."""
        error = HttpError(Response({"status": "503"}), b'{"error":{"message":"Backend Error"}}')
        raise provider_tool_error(error, spreadsheet_id=spreadsheet_id) from error

    @server.tool(task=True)
    async def stuck() -> dict[str, Any]:
        """Never finishes on its own."""
        await anyio.sleep(120)
        return {}

    apply_default_tool_annotations(server)
    controller = AdmissionController(limits)
    server.add_middleware(StructuredToolErrorMiddleware())
    server.add_middleware(ProductionControlMiddleware(controller))
    server.add_middleware(CapabilityCatalogMiddleware())
    # Worker executions share the process controller.
    set_admission_controller(controller)
    server.add_extension(TasksExtension(url="memory://", name=f"w5-{uuid4().hex}", concurrency=1))
    return server


@dataclass
class TaskServer:
    url: str
    keys: SigningKeys
    gate: Gate

    def token(self, subject: str) -> str:
        return mint(self.keys.published["k1"], kid="k1", subject=subject, issuer=ISSUER, audience=AUDIENCE,
                    client_id=f"host-{subject}")

    def client(self, subject: str) -> Client:
        return Client(StreamableHttpTransport(f"{self.url}/mcp", auth=self.token(subject)))


@pytest.fixture()
def task_server_factory(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_HTTP_JWT_ISSUER", ISSUER)
    monkeypatch.setenv("MCP_SHUTDOWN_GRACE_SECONDS", "1")
    monkeypatch.setenv("MCP_USER_TOKEN_DIR", str(tmp_path / "grants"))
    monkeypatch.setenv("MCP_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("MCP_REDIS_URL", raising=False)
    monkeypatch.delenv("MCP_REVOKED_PRINCIPALS", raising=False)
    clear_grant_cache()
    for subject in ("alice", "bob"):
        google_auth.get_token_store().save_credentials_json(
            Principal(issuer=ISSUER, subject=subject), _credentials(["sheets"])
        )
    stack: list[LiveServers] = []

    def start(limits: AdmissionLimits | None = None) -> TaskServer:
        keys = SigningKeys()
        keys.add("k1")
        gate = Gate()
        jwks_sock, mcp_sock = reserve_sockets(2)
        jwks_url = "http://{}:{}".format(*jwks_sock.getsockname()[:2])
        url = "http://{}:{}".format(*mcp_sock.getsockname()[:2])
        settings = RemoteSecuritySettings(
            base_url=url,
            google_oauth_redirect_url=f"{url}/google/oauth/callback",
            jwt_audience=AUDIENCE,
            jwt_issuer=ISSUER,
            jwt_jwks_uri=f"{jwks_url}/jwks",
            token_encryption_key="unused",
            user_token_dir=tmp_path / "grants",
        )
        server = _server(gate, limits or AdmissionLimits())
        server.auth = build_remote_auth(settings, verifier=build_jwt_verifier(settings))
        app = build_http_app(server, HttpServingPolicy.from_environment(url))
        live = LiveServers([jwks_sock, mcp_sock], [keys.app(), app])
        live.__enter__()
        stack.append(live)
        return TaskServer(url, keys, gate)

    try:
        yield start
    finally:
        for live in reversed(stack):
            live.__exit__(None, None, None)
        set_admission_controller(None)
        clear_grant_cache()


def test_worker_restores_the_submitting_caller_and_counts_the_task(task_server_factory) -> None:
    server: TaskServer = task_server_factory()

    async def scenario() -> tuple[Any, Any]:
        async with server.client("alice") as client:
            task = await call_tool_task(client, "whoami", {"wait": True})
            # The worker is now executing (and holds an admission slot).
            await anyio.to_thread.run_sync(server.gate.started.wait, TIMEOUT)
            running = RUNTIME_STATE.active_tasks
            server.gate.release.set()
            result = await task.result()
            return running, result

    running, result = anyio.run(scenario)
    assert running == 1
    assert result.is_error is False
    assert result.structured_content["issuer"] == ISSUER
    assert result.structured_content["subject"] == "alice"
    assert result.structured_content["running"] == 1
    assert RUNTIME_STATE.active_tasks == 0


def test_revocation_between_submission_and_execution_is_enforced_by_the_worker(
    task_server_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    server: TaskServer = task_server_factory()
    alice_key = Principal(issuer=ISSUER, subject="alice").storage_key

    async def scenario() -> tuple[Any, Any]:
        async with server.client("alice") as client:
            blocker = await call_tool_task(client, "whoami", {"wait": True})
            await anyio.to_thread.run_sync(server.gate.started.wait, TIMEOUT)
            # Queued behind the blocker (worker concurrency 1): admitted and
            # authorized at submission ...
            queued = await call_tool_task(client, "whoami", {}, raise_on_error=False)
            # ... then the principal is administratively revoked.
            monkeypatch.setenv("MCP_REVOKED_PRINCIPALS", alice_key)
            server.gate.release.set()
            await blocker.wait(timeout=TIMEOUT)
            status = await queued.wait(timeout=TIMEOUT)
            return status, queued.task_id

    status, task_id = anyio.run(scenario)
    assert status.status == "failed"
    assert status.error["code"] == -32007
    assert status.error["data"]["code"] == "principal_revoked"


def test_google_grant_revoked_before_execution_is_a_tool_error(task_server_factory) -> None:
    server: TaskServer = task_server_factory()

    async def scenario() -> Any:
        async with server.client("alice") as client:
            blocker = await call_tool_task(client, "whoami", {"wait": True})
            await anyio.to_thread.run_sync(server.gate.started.wait, TIMEOUT)
            queued = await call_tool_task(client, "whoami", {}, raise_on_error=False)
            # The user disconnects Google Sheets while the task is queued.
            google_auth.get_token_store().save_credentials_json(
                Principal(issuer=ISSUER, subject="alice"), _credentials(["gmail"])
            )
            server.gate.release.set()
            await blocker.wait(timeout=TIMEOUT)
            return await queued.result()

    result = anyio.run(scenario)
    assert result.is_error is True
    assert result.structured_content["code"] == "missing_capability"
    assert result.structured_content["required_action"]["arguments"] == {"capabilities": ["sheets"]}


def test_another_user_cannot_read_or_cancel_a_task_handle(task_server_factory) -> None:
    server: TaskServer = task_server_factory()

    async def submit() -> str:
        async with server.client("alice") as client:
            task = await call_tool_task(client, "whoami", {"wait": True})
            await anyio.to_thread.run_sync(server.gate.started.wait, TIMEOUT)
            return task.task_id

    task_id = anyio.run(submit)

    def raw(subject: str, method: str) -> dict[str, Any]:
        response = httpx.post(
            f"{server.url}/mcp",
            headers=mcp_headers(method, token=server.token(subject)),
            json=mcp_body(1, method, {"taskId": task_id}, capabilities=TASKS_EXTENSION),
            timeout=TIMEOUT,
        )
        return response.json()

    try:
        for method in ("tasks/get", "tasks/cancel"):
            foreign = raw("bob", method)
            assert foreign["error"]["code"] == -32602
            assert foreign["error"]["message"] == f"Task {task_id} not found"
        owner = raw("alice", "tasks/get")
        assert owner["result"]["status"] == "working"
    finally:
        server.gate.release.set()


def test_worker_applies_the_error_envelope(task_server_factory) -> None:
    server: TaskServer = task_server_factory()

    async def scenario() -> Any:
        async with server.client("alice") as client:
            task = await call_tool_task(client, "failing", {"spreadsheet_id": "s-9"}, raise_on_error=False)
            return await task.result()

    result = anyio.run(scenario)
    assert result.is_error is True
    envelope = result.structured_content
    assert envelope["code"] == "provider_unavailable"
    assert envelope["provider_status"] == 503
    assert envelope["retryable"] is True
    assert envelope["details"] == {"context": {"spreadsheet_id": "s-9"}}
    assert "[code: provider_unavailable]" in result.content[0].text


def test_deadline_covers_the_task_runtime(task_server_factory) -> None:
    server: TaskServer = task_server_factory(AdmissionLimits(standard_deadline=1))

    async def scenario() -> tuple[Any, float]:
        async with server.client("alice") as client:
            started = time.monotonic()
            task = await call_tool_task(client, "stuck", {}, raise_on_error=False)
            result = await task.result()
            return result, time.monotonic() - started

    result, elapsed = anyio.run(scenario)
    assert result.is_error is True
    assert result.structured_content["code"] == "deadline_exceeded"
    assert elapsed < TIMEOUT
    assert RUNTIME_STATE.active_tasks == 0


def test_task_without_a_restorable_caller_never_runs_as_local_or_anonymous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_google_workspace.common import execution

    monkeypatch.setenv("MCP_HTTP_JWT_ISSUER", ISSUER)
    monkeypatch.setenv("MCP_SHUTDOWN_GRACE_SECONDS", "1")
    monkeypatch.setattr("fastmcp.server.dependencies.get_access_token", lambda: None)
    # An expired snapshot token leaves the worker unauthenticated.
    with pytest.raises(PrincipalRequiredError):
        current_principal()
    assert current_principal(require_authenticated=False).issuer == "unauthenticated"

    async def run() -> None:
        async with execution.task_execution_scope("sheets_whoami"):
            raise AssertionError("must not run")  # pragma: no cover

    with pytest.raises(ProtocolRejection) as raised:
        anyio.run(run)
    assert raised.value.error_code == "task_caller_unavailable"
    assert raised.value.rpc_code == -32007


def test_client_without_the_tasks_extension_runs_task_tools_in_the_foreground(task_server_factory) -> None:
    """No tasks capability declared: synchronous, bounded, and not a task execution."""
    server: TaskServer = task_server_factory()
    server.gate.release.set()
    response = httpx.post(
        f"{server.url}/mcp",
        headers=mcp_headers("tools/call", token=server.token("bob"), name="whoami"),
        json=mcp_body(1, "tools/call", {"name": "whoami", "arguments": {}}),
        timeout=TIMEOUT,
    )
    result = response.json()["result"]
    assert result["resultType"] == "complete"
    assert result["structuredContent"]["subject"] == "bob"
    assert result["structuredContent"]["running"] == 0
