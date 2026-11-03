"""W5 policy units: error classification, admission scopes, readiness, tracing,
request-size limiting, catalog freshness, Google OAuth callback, and token
separation."""

from __future__ import annotations

from collections.abc import Iterator
import json
import logging
import threading
from types import SimpleNamespace
from typing import Any

import anyio
import burner_redis
import pytest
from cryptography.fernet import Fernet
from fastmcp import Client
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools import InputRequiredToolResult
from googleapiclient.errors import HttpError
from httplib2 import Response
import mcp.types as mt
import mcp_types
from mcp_types import jsonrpc
from opentelemetry import baggage, trace
from opentelemetry import context as otel_context
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from starlette.testclient import TestClient

import mcp_google_workspace.auth.google_auth as google_auth
from mcp_google_workspace.auth.grants import GrantSnapshot, clear_grant_cache, read_grant
from mcp_google_workspace.auth.identity import Principal
from mcp_google_workspace.common import production
from mcp_google_workspace.common.admission import (
    AdmissionController,
    AdmissionError,
    AdmissionLimits,
    RedisFleetLimits,
    admission_backend_url,
)
from mcp_google_workspace.common.errors import (
    APPLICATION_RPC_CODES,
    JSONRPC_INTERNAL_ERROR,
    RPC_AUTHORIZATION_UNAVAILABLE,
    RPC_RATE_LIMITED,
    RPC_SERVER_UNAVAILABLE,
    RPC_UNAUTHORIZED,
    ConfirmationRequiredError,
    RecoverableToolError,
    classify_error,
    provider_tool_error,
)
from mcp_google_workspace.common.production import (
    RUNTIME_STATE,
    ProductionControlMiddleware,
    RequestSizeLimitMiddleware,
    deployment_topology,
    readiness_report,
)
from mcp_google_workspace.common.tracing import (
    bounded_tracestate,
    parent_context_from_meta,
    tool_span_context,
    valid_traceparent,
)
from mcp_google_workspace.server import workspace_mcp

TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
MODERN = "2026-07-28"


# ---------------------------------------------------------------------------
# Error codes and classification
# ---------------------------------------------------------------------------


def test_application_rpc_codes_are_implementation_defined_and_collision_free() -> None:
    sdk_allocated = {
        jsonrpc.CONNECTION_CLOSED,  # -32000, SDK-internal
        jsonrpc.REQUEST_TIMEOUT,  # -32001, SDK-internal
        -32002,  # reserved, never reused (and FastMCP's optional middleware)
        jsonrpc.HEADER_MISMATCH,
        jsonrpc.MISSING_REQUIRED_CLIENT_CAPABILITY,
        jsonrpc.UNSUPPORTED_PROTOCOL_VERSION,
        jsonrpc.URL_ELICITATION_REQUIRED,
    }
    assert APPLICATION_RPC_CODES == {RPC_RATE_LIMITED, RPC_SERVER_UNAVAILABLE, RPC_UNAUTHORIZED, RPC_AUTHORIZATION_UNAVAILABLE}
    for code in APPLICATION_RPC_CODES:
        assert -32019 <= code <= -32000
        assert code not in sdk_allocated
    assert len(APPLICATION_RPC_CODES) == 4


@pytest.mark.parametrize(
    ("status", "reason", "code", "retryable"),
    [
        (400, "Bad range", "invalid_input", False),
        (401, "Invalid Credentials", "reauth_required", False),
        (403, "Denied", "permission_denied", False),
        (403, "User Rate Limit Exceeded", "rate_limited", True),
        (404, "Not found", "not_found", False),
        (409, "Conflict", "conflict", False),
        (429, "Too many requests", "rate_limited", True),
        (500, "Backend Error", "provider_unavailable", True),
        (503, "Unavailable", "provider_unavailable", True),
        (418, "Teapot", "provider_error", False),
    ],
)
def test_google_failures_are_tool_execution_errors(status: int, reason: str, code: str, retryable: bool) -> None:
    error = HttpError(Response({"status": str(status)}), json.dumps({"error": {"message": reason}}).encode())
    raised = provider_tool_error(error, document_id="d-1")
    rpc_code, envelope = classify_error(raised)
    assert rpc_code is None  # an isError result, never a JSON-RPC error
    assert envelope["code"] == code
    assert envelope["retryable"] is retryable
    assert envelope["provider_status"] == status
    assert envelope["details"] == {"context": {"document_id": "d-1"}}
    assert envelope["required_action"]


class _Arguments(BaseModel):
    count: int


def test_argument_validation_is_a_tool_error_with_field_errors() -> None:
    try:
        _Arguments.model_validate({"count": "many"})
    except PydanticValidationError as exc:
        wrapped = FastMCPValidationError("invalid")
        wrapped.__cause__ = exc
    rpc_code, envelope = classify_error(wrapped)
    assert rpc_code is None
    assert envelope["code"] == "invalid_input"
    assert envelope["field_errors"][0]["field"] == "count"
    assert envelope["required_action"]["field_errors"] == envelope["field_errors"]


@pytest.mark.parametrize(
    ("error", "expected_rpc", "expected_code"),
    [
        (AdmissionError("rate_limited", "slow down", retry_after=2), RPC_RATE_LIMITED, "rate_limited"),
        (AdmissionError("server_draining", "draining", retry_after=5), RPC_SERVER_UNAVAILABLE, "server_draining"),
        (production.principal_revoked_rejection(), RPC_UNAUTHORIZED, "principal_revoked"),
        (RecoverableToolError("authorization_backend_unavailable", "x", required_action=None), RPC_AUTHORIZATION_UNAVAILABLE, "authorization_backend_unavailable"),
        (RuntimeError("secret provider detail"), JSONRPC_INTERNAL_ERROR, "internal_error"),
        (ValueError("bad value"), None, "invalid_input"),
        (ConfirmationRequiredError("delete_x", "Delete x?"), None, "confirmation_required"),
        (RecoverableToolError("deadline_exceeded", "late", required_action=None), None, "deadline_exceeded"),
        (RecoverableToolError("rate_limited", "Google 429", required_action=None, retryable=True), None, "rate_limited"),
        (PermissionError("no"), None, "permission_denied"),
    ],
)
def test_protocol_versus_tool_classification(error: Exception, expected_rpc: int | None, expected_code: str) -> None:
    rpc_code, envelope = classify_error(error)
    assert rpc_code == expected_rpc
    assert envelope["code"] == expected_code
    if expected_code == "internal_error":
        assert "secret provider detail" not in envelope["message"]


@pytest.fixture(scope="module")
def wire() -> Iterator[TestClient]:
    app = workspace_mcp.http_app(
        transport="http", stateless_http=False, json_response=False,
        allowed_hosts=["testserver"], allowed_origins=["http://testserver"],
    )
    with TestClient(app) as client:
        yield client


def _modern(client: TestClient, method: str, params: dict[str, Any] | None = None, name: str | None = None):
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": MODERN,
        "Mcp-Method": method,
    }
    if name:
        headers["Mcp-Name"] = name
    body_params = dict(params or {})
    body_params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    return client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 5, "method": method, "params": body_params})


def test_wire_unknown_tool_is_a_protocol_error(wire: TestClient) -> None:
    response = _modern(wire, "tools/call", {"name": "no_such_tool", "arguments": {}}, name="no_such_tool")
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == -32602
    assert error["data"]["code"] == "unknown_tool"


def test_wire_invalid_arguments_are_a_tool_error(wire: TestClient) -> None:
    response = _modern(
        wire, "tools/call",
        {"name": "search_workspace", "arguments": {"query": 5, "max_results_per_service": "many"}},
        name="search_workspace",
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    fields = {entry["field"] for entry in result["structuredContent"]["field_errors"]}
    assert fields == {"query", "max_results_per_service"}


def test_wire_business_rule_failure_in_a_sync_tool_is_a_tool_error(wire: TestClient) -> None:
    response = _modern(
        wire, "tools/call",
        {"name": "prepare_workspace_action", "arguments": {"tool_name": "not_consequential", "arguments": {}}},
        name="prepare_workspace_action",
    )
    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "invalid_input"


@pytest.mark.parametrize(
    ("raw", "code", "status"),
    [
        (b"{not json", -32700, 400),
        (b'[{"jsonrpc":"2.0","id":1,"method":"tools/list"}]', -32600, 400),
    ],
)
def test_wire_malformed_requests_stay_json_rpc_errors(wire: TestClient, raw: bytes, code: int, status: int) -> None:
    response = wire.post(
        "/mcp",
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": MODERN,
            "Mcp-Method": "tools/list",
        },
        content=raw,
    )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_wire_unknown_method_is_method_not_found(wire: TestClient) -> None:
    response = _modern(wire, "tools/frobnicate")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == -32601


class _Failing:
    def __getattr__(self, name: str) -> "_Failing":
        if name.startswith("__"):
            raise AttributeError(name)
        return self

    def __call__(self, *_a: Any, **_k: Any) -> "_Failing":
        return self

    def execute(self, *_a: Any, **_k: Any) -> Any:
        raise HttpError(Response({"status": "503"}), b'{"error":{"message":"down"}}')


def test_partial_batch_success_versus_total_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp_google_workspace.server as server_module

    class _Drive:
        def files(self) -> "_Drive":
            return self

        def list(self, **_k: Any) -> "_Drive":
            return self

        def execute(self) -> dict[str, Any]:
            return {"files": [{"id": "f1", "name": "Plan"}]}

    monkeypatch.setattr(server_module, "build_gmail_service", lambda: _Failing())
    monkeypatch.setattr(server_module, "build_people_service", lambda: _Failing())
    monkeypatch.setattr(server_module, "build_drive_service", lambda: _Drive())

    async def call(services: list[str]) -> Any:
        async with Client(workspace_mcp) as client:
            return await client.call_tool(
                "search_workspace", {"query": "plan", "services": services}, raise_on_error=False
            )

    partial = anyio.run(call, ["drive", "gmail"])
    assert partial.is_error is False
    assert partial.structured_content["status"] == "partial"
    assert set(partial.structured_content["errors"]) == {"gmail"}
    assert partial.structured_content["count"] == 1

    failed = anyio.run(call, ["gmail", "people"])
    assert failed.is_error is True
    assert failed.structured_content["code"] == "provider_unavailable"
    assert set(failed.structured_content["details"]["errors"]) == {"gmail", "people"}


# ---------------------------------------------------------------------------
# Admission: per-process versus fleet-wide
# ---------------------------------------------------------------------------


class _Counters:
    active_requests = 0
    active_tasks = 0


def test_fleet_rate_limit_is_shared_between_replicas() -> None:
    shared = burner_redis.BurnerRedis()
    limits = AdmissionLimits(rate_limit_per_minute=3)
    replica_a = AdmissionController(limits, fleet=RedisFleetLimits(shared))
    replica_b = AdmissionController(limits, fleet=RedisFleetLimits(shared))
    local = AdmissionController(limits)

    async def run() -> None:
        await replica_a.check_rate("alice")
        await replica_b.check_rate("alice")
        await replica_a.check_rate("alice")
        with pytest.raises(AdmissionError) as raised:
            await replica_b.check_rate("alice")
        assert raised.value.rpc_code == RPC_RATE_LIMITED
        await replica_b.check_rate("bob")  # independent principal
        # A process-local controller only sees its own traffic.
        for _ in range(3):
            await local.check_rate("alice")

    anyio.run(run)
    assert replica_a.scope == "fleet" and local.scope == "process"


def test_fleet_principal_concurrency_is_shared_and_process_limits_are_not() -> None:
    shared = burner_redis.BurnerRedis()
    limits = AdmissionLimits(principal_concurrency=1, principal_queue_seconds=0, global_concurrency=1)
    replica_a = AdmissionController(limits, fleet=RedisFleetLimits(shared))
    replica_b = AdmissionController(limits, fleet=RedisFleetLimits(shared))
    counters = _Counters()

    async def run() -> None:
        async with replica_a.execution_slot("alice", "sheets_get_values", kind="task", counters=counters):
            assert counters.active_tasks == 1
            with pytest.raises(AdmissionError) as raised:
                async with replica_b.execution_slot("alice", "sheets_get_values", kind="request", counters=counters):
                    pass  # pragma: no cover
            assert raised.value.error_code == "principal_concurrency_exceeded"
            # Global concurrency is per process: replica B still admits bob.
            async with replica_b.execution_slot("bob", "sheets_get_values", kind="request", counters=counters):
                assert counters.active_requests == 1
        assert (counters.active_requests, counters.active_tasks) == (0, 0)
        # The lease was released: alice is admitted again anywhere.
        async with replica_b.execution_slot("alice", "sheets_get_values", kind="request", counters=counters):
            pass

    anyio.run(run)


def test_unreachable_fleet_backend_fails_closed() -> None:
    class Broken:
        async def eval(self, *_a: Any) -> Any:
            raise ConnectionError("redis down")

    controller = AdmissionController(AdmissionLimits(), fleet=RedisFleetLimits(Broken()))

    async def run() -> None:
        with pytest.raises(AdmissionError) as raised:
            await controller.check_rate("alice")
        assert raised.value.error_code == "admission_backend_unavailable"
        assert raised.value.rpc_code == RPC_SERVER_UNAVAILABLE

    anyio.run(run)


def test_admission_backend_selection() -> None:
    assert admission_backend_url({}) is None
    assert admission_backend_url({"MCP_REDIS_URL": "redis://r/0"}) == "redis://r/0"
    assert admission_backend_url({"MCP_REDIS_URL": "redis://r/0", "MCP_RUNTIME_MODE": "bundle"}) is None
    assert admission_backend_url({"MCP_REDIS_URL": "redis://r/0", "MCP_ADMISSION_BACKEND": "local"}) is None
    with pytest.raises(ValueError):
        admission_backend_url({"MCP_ADMISSION_BACKEND": "redis"})


def test_draining_refuses_new_requests_as_a_protocol_error(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = ProductionControlMiddleware(AdmissionController(AdmissionLimits()))
    monkeypatch.setattr(RUNTIME_STATE, "draining", True)
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(name="gmail_read_emails", arguments={}), method="tools/call"
    )

    async def call_next(_context: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("draining server executed a tool")

    async def run() -> None:
        with pytest.raises(AdmissionError) as raised:
            await middleware.on_call_tool(context, call_next)
        assert raised.value.rpc_code == RPC_SERVER_UNAVAILABLE

    anyio.run(run)


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


@pytest.fixture()
def storage_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("MCP_USER_TOKEN_DIR", str(tmp_path / "tokens"))
    monkeypatch.setenv("MCP_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    for name in (
        "MCP_SECRET_FILE", "MCP_REDIS_URL", "MCP_UPLOAD_S3_BUCKET", "MCP_WORKERS", "MCP_REPLICAS",
        "MCP_SESSION_AFFINITY", "MCP_REQUEST_STATE_KEYS", "FASTMCP_DOCKET_URL", "FASTMCP_TASKS_ENCRYPTION_KEY",
        "MCP_RUNTIME_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(RUNTIME_STATE, "draining", False)
    return monkeypatch


def test_single_process_is_ready_without_affinity_or_shared_keys(storage_env: pytest.MonkeyPatch) -> None:
    ready, payload = readiness_report()
    assert ready, payload
    assert payload["fleet"] is False
    assert payload["checks"]["app_state"] == {"backend": "memory", "ok": True}
    assert payload["checks"]["task_queue"]["backend"] == "memory"
    assert "continuation_keys" not in payload["checks"]
    assert "legacy_session_affinity" not in payload["checks"]


def test_single_worker_replicas_are_detected_as_a_fleet(storage_env: pytest.MonkeyPatch) -> None:
    storage_env.setenv("MCP_REPLICAS", "3")
    assert deployment_topology()["fleet"] is True
    ready, payload = readiness_report()
    assert not ready
    # Memory app state, an in-process queue and per-process continuation keys
    # cannot serve a replica fleet.
    assert payload["checks"]["app_state"]["ok"] is False
    assert payload["checks"]["task_queue"]["ok"] is False
    assert payload["checks"]["continuation_keys"]["ok"] is False


def test_a_shared_backend_alone_requires_continuation_keys(storage_env: pytest.MonkeyPatch) -> None:
    storage_env.setenv("MCP_REDIS_URL", "redis://fleet:6379/0")
    assert deployment_topology() == {"workers": 1, "replicas": 1, "shared_backend": True, "fleet": True}

    class RedisClient:
        def ping(self) -> bool:
            return True

    storage_env.setattr("mcp_google_workspace.common.production.redis.Redis.from_url", lambda _url: RedisClient())
    ready, payload = readiness_report()
    assert not ready
    assert payload["checks"]["continuation_keys"]["ok"] is False
    # A distributed task queue must encrypt caller snapshots.
    assert payload["checks"]["task_queue"]["ok"] is False
    assert "FASTMCP_TASKS_ENCRYPTION_KEY" in payload["checks"]["task_queue"]["requirement"]
    assert payload["checks"]["app_state"] == {"backend": "redis", "ok": True}


def test_unreachable_task_queue_fails_readiness(storage_env: pytest.MonkeyPatch) -> None:
    storage_env.setenv("FASTMCP_DOCKET_URL", "redis://queue:6379/1")
    storage_env.setenv("FASTMCP_TASKS_ENCRYPTION_KEY", "q" * 40)

    class Down:
        def ping(self) -> bool:
            raise ConnectionError("down")

    storage_env.setattr("mcp_google_workspace.common.production.redis.Redis.from_url", lambda _url: Down())
    ready, payload = readiness_report()
    assert not ready
    assert payload["checks"]["task_queue"] == {
        "backend": "redis", "queue_name": "mcp-google-workspace", "url_source": "FASTMCP_DOCKET_URL",
        "snapshot_encryption": True, "ok": False, "error": "ConnectionError",
    }


def test_draining_is_not_ready(storage_env: pytest.MonkeyPatch) -> None:
    storage_env.setattr(RUNTIME_STATE, "draining", True)
    ready, payload = readiness_report()
    assert not ready and payload["status"] == "not_ready"


def test_operation_records_readiness(storage_env: pytest.MonkeyPatch) -> None:
    ready, payload = readiness_report()
    assert payload["checks"]["operation_records"] == {"backend": "memory", "ok": True}

    class RedisClient:
        def ping(self) -> bool:
            return True

    storage_env.setenv("MCP_REDIS_URL", "redis://fleet:6379/0")
    storage_env.setattr("mcp_google_workspace.common.production.redis.Redis.from_url", lambda _url: RedisClient())
    _, payload = readiness_report()
    assert payload["checks"]["operation_records"] == {"backend": "redis", "ok": True}

    # Redis-backed records are Fernet-encrypted: no key ring, not ready.
    storage_env.delenv("MCP_TOKEN_ENCRYPTION_KEY")
    ready, payload = readiness_report()
    assert not ready
    check = payload["checks"]["operation_records"]
    assert check["ok"] is False and "key ring" in check["requirement"]

    class Down:
        def ping(self) -> bool:
            raise ConnectionError("down")

    storage_env.setenv("MCP_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    storage_env.setattr("mcp_google_workspace.common.production.redis.Redis.from_url", lambda _url: Down())
    _, payload = readiness_report()
    assert payload["checks"]["operation_records"] == {"backend": "redis", "ok": False, "error": "ConnectionError"}


def test_operation_lease_must_exceed_every_deadline(storage_env: pytest.MonkeyPatch) -> None:
    storage_env.delenv("MCP_OPERATION_LEASE_SECONDS", raising=False)
    storage_env.delenv("MCP_EXPENSIVE_DEADLINE_SECONDS", raising=False)
    assert production.validate_operation_lease() == (900, 600)
    ready, payload = readiness_report()
    assert payload["checks"]["operation_lease"] == {"ok": True, "lease_seconds": 900, "longest_deadline_seconds": 600}

    storage_env.setenv("MCP_EXPENSIVE_DEADLINE_SECONDS", "900")
    with pytest.raises(ValueError, match="MCP_OPERATION_LEASE_SECONDS"):
        production.validate_operation_lease()
    ready, payload = readiness_report()
    assert not ready
    assert payload["checks"]["operation_lease"]["ok"] is False


def test_http_entrypoint_refuses_to_start_when_the_lease_is_too_short(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_google_workspace import server_http

    monkeypatch.setenv("MCP_OPERATION_LEASE_SECONDS", "60")
    monkeypatch.setenv("MCP_TOOL_DEADLINE_SECONDS", "120")
    monkeypatch.setattr(server_http, "configure_logging", lambda: None)
    monkeypatch.setattr(server_http, "configure_remote_tool_search", lambda: None)

    def never(**_kwargs: Any) -> None:  # pragma: no cover - must not start
        raise AssertionError("server started with an invalid lease")

    monkeypatch.setattr(server_http.workspace_mcp, "run", never)
    with pytest.raises(ValueError, match="MCP_OPERATION_LEASE_SECONDS"):
        server_http.main()


# ---------------------------------------------------------------------------
# Tracing
# ---------------------------------------------------------------------------


def test_valid_traceparent_parents_the_tool_span() -> None:
    parent = parent_context_from_meta({"traceparent": TRACEPARENT, "tracestate": "vendor=abc", "baggage": "user=eve"})
    assert parent is not None
    span_context = trace.get_current_span(parent).get_span_context()
    assert format(span_context.trace_id, "032x") == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert format(span_context.span_id, "016x") == "00f067aa0ba902b7"
    assert span_context.trace_state.get("vendor") == "abc"
    assert baggage.get_all(parent) == {}


@pytest.mark.parametrize(
    "value",
    [
        "garbage",
        "00-00000000000000000000000000000000-00f067aa0ba902b7-01",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01",
        "ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        TRACEPARENT.upper(),
        None,
        42,
    ],
)
def test_malformed_traceparent_is_ignored(value: object) -> None:
    assert valid_traceparent(value) is None
    assert parent_context_from_meta({"traceparent": value}) is None


def test_tracestate_is_bounded() -> None:
    assert bounded_tracestate("a=1,b=2") == "a=1,b=2"
    assert bounded_tracestate(",".join(f"k{i}=v" for i in range(33))) is None
    assert bounded_tracestate("k=" + "v" * 600) is None
    assert bounded_tracestate("Bad Key=1") is None


def test_ambient_baggage_is_dropped_for_tool_spans() -> None:
    token = otel_context.attach(baggage.set_baggage("tenant", "evil"))
    try:
        context = tool_span_context({"traceparent": TRACEPARENT})
        assert baggage.get_all(context) == {}
    finally:
        otel_context.detach(token)


class _RecordingTracer:
    def __init__(self) -> None:
        self.contexts: list[Any] = []
        self.attributes: dict[str, Any] = {}

    def start_as_current_span(self, name: str, context: Any = None, **_k: Any) -> Any:
        self.contexts.append(context)
        tracer = self

        class _Span:
            def __enter__(self) -> "_Span":
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def set_attribute(self, key: str, value: Any) -> None:
                tracer.attributes[key] = value

        return _Span()


def test_middleware_marks_mrtr_rounds_and_logs_no_payload(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    middleware = ProductionControlMiddleware(AdmissionController(AdmissionLimits()))
    tracer = _RecordingTracer()
    middleware._tracer = tracer  # type: ignore[assignment]
    monkeypatch.setattr(production, "request_meta", lambda: {"traceparent": TRACEPARENT})
    monkeypatch.setattr(RUNTIME_STATE, "draining", False)
    secret_state = "sealed-continuation-SECRET"
    context = MiddlewareContext(
        message=mt.CallToolRequestParams(
            name="gmail_send_email",
            arguments={"body": "confidential mail body", "to": ["x@example.com"]},
            request_state=secret_state,
        ),
        method="tools/call",
    )
    asked = InputRequiredToolResult(
        mcp_types.InputRequiredResult(input_requests={}, request_state="next")
    )
    before_logical = production.LOGICAL_OPERATIONS.labels("gmail_send_email", "input_required")._value.get()

    async def call_next(_context: Any) -> Any:
        return asked

    with caplog.at_level(logging.INFO, logger="mcp_google_workspace.production"):
        result = anyio.run(middleware.on_call_tool, context, call_next)
    assert result is asked
    parent = tracer.contexts[0]
    assert format(trace.get_current_span(parent).get_span_context().trace_id, "032x") == TRACEPARENT[3:35]
    assert tracer.attributes["mcp.tool.round"] == "input_required"
    assert tracer.attributes["mcp.tool.continuation"] is True
    # A question round is not a completed logical operation.
    assert production.LOGICAL_OPERATIONS.labels("gmail_send_email", "input_required")._value.get() == before_logical
    record = json.loads(caplog.records[-1].getMessage())
    assert record["phase"] == "round" and record["outcome"] == "input_required"
    for secret in ("confidential mail body", "x@example.com", secret_state):
        assert secret not in caplog.text


# ---------------------------------------------------------------------------
# Request-size limiter (ASGI level): streaming responses and disconnects
# ---------------------------------------------------------------------------


async def _drive(app: Any, messages: list[dict[str, Any]], headers: list[tuple[bytes, bytes]] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inbound = list(messages)
    sent: list[dict[str, Any]] = []
    seen: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        message = inbound.pop(0) if inbound else {"type": "http.disconnect"}
        seen.append(message)
        return message

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": headers or []}
    await app(scope, receive, send)
    return sent, seen


def test_limiter_passes_streaming_responses_and_disconnects_through() -> None:
    observed: list[dict[str, Any]] = []

    async def streaming_app(scope: Any, receive: Any, send: Any) -> None:
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.request":
                body += message.get("body", b"")
                if not message.get("more_body"):
                    break
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
        for part in (b"event: one\r\n\r\n", b"event: two\r\n\r\n"):
            await send({"type": "http.response.body", "body": part, "more_body": True})
        observed.append(await receive())  # the disconnect watcher's read
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    limiter = RequestSizeLimitMiddleware(streaming_app, max_bytes=10)
    sent, _ = anyio.run(
        _drive, limiter,
        [{"type": "http.request", "body": b"12345", "more_body": True}, {"type": "http.request", "body": b"678", "more_body": False}],
    )
    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body", "http.response.body", "http.response.body"]
    assert observed == [{"type": "http.disconnect"}]


def test_limiter_rejects_an_undeclared_oversized_stream_and_silences_the_app() -> None:
    reads: list[dict[str, Any]] = []

    async def buffering_app(scope: Any, receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            reads.append(message)
            if message["type"] == "http.disconnect" or not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"late", "more_body": False})

    limiter = RequestSizeLimitMiddleware(buffering_app, max_bytes=8)
    chunks = [{"type": "http.request", "body": b"x" * 5, "more_body": True} for _ in range(10)]
    sent, seen = anyio.run(_drive, limiter, chunks)
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 413
    assert all(message.get("body") != b"late" for message in sent)
    # Only two chunks were pulled from the client before the rejection.
    assert len([m for m in seen if m["type"] == "http.request"]) == 2
    assert reads[-1] == {"type": "http.disconnect"}


# ---------------------------------------------------------------------------
# Catalog freshness and the refresh tool
# ---------------------------------------------------------------------------


def _credentials(capabilities: list[str], marker: str = "a") -> str:
    return json.dumps(
        {
            "token": f"google-access-{marker}",
            "refresh_token": "google-refresh",
            "client_id": "google-client",
            "client_secret": "google-secret",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": google_auth.get_google_scopes(capabilities),
            "expiry": "2099-01-01T00:00:00Z",
        }
    )


def test_grant_cache_is_keyed_by_principal_and_revision(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_USER_TOKEN_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    clear_grant_cache()
    alice = Principal(issuer="https://i", subject="alice")
    bob = Principal(issuer="https://i", subject="bob")
    store = google_auth.get_token_store()
    store.save_credentials_json(alice, _credentials(["gmail", "people"]))
    store.save_credentials_json(bob, _credentials(["gmail"]))

    first = read_grant(alice)
    assert first.capabilities == {"gmail", "people"}
    assert read_grant(bob).capabilities == {"gmail"}  # no cross-principal reuse
    store.save_credentials_json(alice, _credentials(["gmail"], marker="b"))
    second = read_grant(alice)
    assert second.revision != first.revision
    assert second.capabilities == {"gmail"}
    store.delete_credentials(alice)
    assert read_grant(alice) == GrantSnapshot(alice.storage_key, None, frozenset())


def test_refresh_catalog_tool_is_truthful_and_needs_no_connection_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "mcp_google_workspace.auth.grants.read_grant",
        lambda principal=None: GrantSnapshot("p", "rev-7", frozenset({"gmail", "calendar"})),
    )

    async def call() -> Any:
        async with Client(workspace_mcp) as client:
            return await client.call_tool("refresh_workspace_catalog", {})

    result = anyio.run(call)
    assert result.structured_content["notification_sent"] is False
    assert result.structured_content["granted_capabilities"] == ["calendar", "gmail"]
    assert result.structured_content["grant_revision"] == "rev-7"


# ---------------------------------------------------------------------------
# Google OAuth callback and token separation
# ---------------------------------------------------------------------------


class _PendingState:
    def __init__(self) -> None:
        self.principal = Principal(issuer="https://i", subject="alice")
        self.code_verifier = "v" * 64
        self.scopes = ("https://www.googleapis.com/auth/gmail.modify",)


class _CallbackStore:
    def __init__(self) -> None:
        self.consumed: list[str] = []
        self.saved: list[str] = []

    def consume_oauth_state(self, state: str) -> Any:
        self.consumed.append(state)
        return _PendingState() if state == "good-state" else None

    def load_credentials_json(self, principal: Any) -> None:
        return None

    def save_credentials_json(self, principal: Any, payload: str) -> None:
        self.saved.append(payload)


@pytest.fixture()
def callback(monkeypatch: pytest.MonkeyPatch) -> tuple[_CallbackStore, list[dict[str, Any]]]:
    from mcp_google_workspace.auth import google_oauth

    store = _CallbackStore()
    exchanges: list[dict[str, Any]] = []

    class _Credentials:
        def has_scopes(self, scopes: list[str]) -> bool:
            return True

        def to_json(self) -> str:
            return json.dumps({"token": "google-access", "refresh_token": "google-refresh"})

    class _Flow:
        credentials = _Credentials()

        def fetch_token(self, **kwargs: Any) -> None:
            exchanges.append({**kwargs, "thread": threading.get_ident()})

    monkeypatch.setattr(google_oauth, "get_token_store", lambda: store)
    monkeypatch.setattr(google_oauth, "_flow", lambda **_kwargs: _Flow())
    return store, exchanges


def _callback_app() -> Any:
    from fastmcp import FastMCP

    from mcp_google_workspace.auth.google_oauth import register_oauth_callback_route

    server = FastMCP("callback-test")
    register_oauth_callback_route(server)
    return server.http_app(transport="http")


def test_callback_exchanges_the_code_off_the_event_loop(callback) -> None:
    store, exchanges = callback
    with TestClient(_callback_app()) as client:
        loop_thread = client.portal.call(lambda: threading.get_ident())
        response = client.get(
            "/google/oauth/callback",
            params={"state": "good-state", "code": "auth-code", "iss": "https://accounts.google.com"},
        )
    assert response.status_code == 200
    assert exchanges[0]["code"] == "auth-code"
    assert "authorization_response" not in exchanges[0]
    assert exchanges[0]["thread"] != loop_thread
    assert len(store.saved) == 1


@pytest.mark.parametrize(
    ("params", "status", "text"),
    [
        ({"state": "good-state", "code": "c", "iss": "https://evil.example"}, 400, "unexpected issuer"),
        ({"state": "good-state", "error": "access_denied"}, 400, "not completed"),
        ({"state": "unknown", "code": "c"}, 400, "Invalid or expired"),
    ],
)
def test_callback_rejections_consume_state_without_exchanging(callback, params: dict[str, str], status: int, text: str) -> None:
    store, exchanges = callback
    with TestClient(_callback_app()) as client:
        response = client.get("/google/oauth/callback", params=params)
    assert response.status_code == status
    assert text in response.text
    assert store.consumed == [params["state"]]
    assert exchanges == [] and store.saved == []


def test_mcp_bearer_token_is_never_used_as_a_google_credential(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp.server.auth import AccessToken
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

    monkeypatch.setenv("MCP_USER_TOKEN_DIR", str(tmp_path / "grants"))
    monkeypatch.setenv("MCP_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("MCP_GOOGLE_OAUTH_REDIRECT_URL", "https://mcp.example.test/google/oauth/callback")
    monkeypatch.setenv("MCP_CREDENTIALS_DIR", str(tmp_path))
    (tmp_path / "credentials.json").write_text("{}")
    alice = Principal(issuer="https://issuer.example.test", subject="alice")
    google_auth.get_token_store().save_credentials_json(alice, _credentials(["people"], marker="stored"))
    captured: dict[str, Any] = {}

    def fake_build(api: str, version: str, *, http: Any, **_kwargs: Any) -> Any:
        captured["credentials"] = http.credentials
        return SimpleNamespace()

    monkeypatch.setattr(google_auth, "build", fake_build)
    bearer = "mcp-bearer-SECRET-token"
    token = auth_context_var.set(
        AuthenticatedUser(
            AccessToken(
                token=bearer, client_id="host", scopes=[],
                claims={"iss": alice.issuer, "sub": alice.subject},
            )
        )
    )
    try:
        google_auth._build_service_now("people", "v1")
    finally:
        auth_context_var.reset(token)
    credentials = captured["credentials"]
    assert credentials.token == "google-access-stored"
    assert bearer not in json.dumps({"token": credentials.token, "refresh": credentials.refresh_token})
