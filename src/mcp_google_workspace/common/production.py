"""Production admission control, lifecycle state, and privacy-safe telemetry."""

from __future__ import annotations

import anyio
import copy
from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
import logging
import os
import threading
import time
from typing import Any, Final
from uuid import uuid4

import mcp.types as mt
from mcp_types.version import (
    HANDSHAKE_PROTOCOL_VERSIONS,
    LATEST_MODERN_VERSION,
    MODERN_PROTOCOL_VERSIONS,
)
import redis
from fastmcp.exceptions import NotFoundError
from fastmcp.prompts import Prompt
from fastmcp.resources import Resource, ResourceTemplate
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import InputRequiredToolResult, Tool, ToolResult
from opentelemetry import trace
from prometheus_client import Counter, Gauge, Histogram
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..auth.identity import current_principal
from .admission import (
    AdmissionController,
    AdmissionError,
    AdmissionLimits,
    tool_cost,
)
from .approvals import COMMIT_ACTIVE, requires_prepare
from .async_ops import run_blocking
from .confirmation import shared_request_state_keys_configured
from .errors import (
    RPC_AUTHORIZATION_UNAVAILABLE,
    RPC_UNAUTHORIZED,
    ProtocolRejection,
    RecoverableToolError,
)
from .tracing import request_meta, tool_span_context, without_baggage

LOGGER = logging.getLogger("mcp_google_workspace.production")
CORRELATION_ID: ContextVar[str | None] = ContextVar("mcp_correlation_id", default=None)

__all__ = [
    "AdmissionError",
    "CapabilityCatalogMiddleware",
    "ConsequentialActionMiddleware",
    "METRICS",
    "ProductionControlMiddleware",
    "RUNTIME_STATE",
    "RequestSizeLimitMiddleware",
    "admission_controller",
    "build_version_payload",
    "deployment_topology",
    "principal_revoked",
    "production_lifespan",
    "readiness_report",
]


@dataclass(slots=True)
class RuntimeState:
    started_at: float = field(default_factory=time.time)
    draining: bool = False
    active_requests: int = 0
    active_tasks: int = 0

    def begin_draining(self) -> None:
        self.draining = True

    def ready(self) -> bool:
        return not self.draining

    @property
    def active_total(self) -> int:
        return self.active_requests + self.active_tasks


RUNTIME_STATE = RuntimeState()


class Metrics:
    """Low-cardinality in-process metrics suitable for OTEL/Prometheus scraping."""

    def __init__(self) -> None:
        self.calls: dict[tuple[str, str], int] = defaultdict(int)
        self.duration_ms: dict[str, list[float]] = defaultdict(list)
        self.queue_ms: dict[str, list[float]] = defaultdict(list)
        self.rejections: dict[str, int] = defaultdict(int)

    def observe(self, tool: str, outcome: str, duration_ms: float, queue_ms: float) -> None:
        self.calls[(tool, outcome)] += 1
        for target, value in ((self.duration_ms[tool], duration_ms), (self.queue_ms[tool], queue_ms)):
            target.append(value)
            if len(target) > 2_000:
                del target[:1_000]

    def snapshot(self) -> dict[str, Any]:
        def summary(values: list[float]) -> dict[str, float | int]:
            ordered = sorted(values)
            if not ordered:
                return {"count": 0, "avg": 0.0, "p95": 0.0}
            return {
                "count": len(ordered),
                "avg": round(sum(ordered) / len(ordered), 2),
                "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2),
            }

        return {
            "active_requests": RUNTIME_STATE.active_requests,
            "active_tasks": RUNTIME_STATE.active_tasks,
            "calls": {f"{tool}:{outcome}": count for (tool, outcome), count in self.calls.items()},
            "duration_ms": {tool: summary(values) for tool, values in self.duration_ms.items()},
            "queue_ms": {tool: summary(values) for tool, values in self.queue_ms.items()},
            "rejections": dict(self.rejections),
        }


METRICS = Metrics()
TOOL_CALLS = Counter(
    "mcp_workspace_tool_calls_total",
    "Workspace tool executions per round (input_required = one multi-round-trip leg)",
    ("tool", "outcome"),
)
LOGICAL_OPERATIONS = Counter(
    "mcp_workspace_logical_operations_total",
    "Completed logical Workspace operations (multi-round-trip questions excluded)",
    ("tool", "outcome"),
)
TOOL_DURATION = Histogram(
    "mcp_workspace_tool_duration_seconds", "Workspace tool duration", ("tool",)
)
TOOL_QUEUE = Histogram(
    "mcp_workspace_tool_queue_seconds", "Workspace tool admission queue duration", ("tool",)
)
ACTIVE_REQUESTS = Gauge("mcp_workspace_active_requests", "Currently executing Workspace tools")
ACTIVE_TASKS = Gauge(
    "mcp_workspace_active_tasks", "Background-task executions running in this process"
)
ADMISSION_REJECTIONS = Counter(
    "mcp_workspace_admission_rejections_total", "Rejected Workspace requests", ("reason",)
)
GOOGLE_REQUESTS = Counter(
    "mcp_workspace_google_requests_total", "Logical Google API requests", ("service", "outcome")
)
GOOGLE_HTTP_ATTEMPTS = Counter(
    "mcp_workspace_google_http_attempts_total", "Google API HTTP attempts including retries", ("service",)
)
OAUTH_REFRESHES = Counter(
    "mcp_workspace_oauth_refresh_total", "Google OAuth refresh outcomes", ("outcome",)
)
UPLOAD_STORED_BYTES = Counter(
    "mcp_workspace_upload_stored_bytes_total",
    "Encrypted upload bytes accepted by the backend",
    ("backend",),
)
UPLOAD_REMOVED_BYTES = Counter(
    "mcp_workspace_upload_removed_bytes_total",
    "Encrypted upload bytes removed or expired",
    ("backend",),
)
UPLOAD_CLEANUPS = Counter(
    "mcp_workspace_upload_cleanup_total", "Expired upload objects cleaned", ("backend",)
)


def _integer_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return value


# ---------------------------------------------------------------------------
# HTTP request-size limit (pure ASGI, streaming)
# ---------------------------------------------------------------------------


class RequestSizeLimitMiddleware:
    """Bound request bodies while they stream in, before anything buffers them.

    A declared ``Content-Length`` above the limit is refused immediately. A
    chunked (or under-declared) body is counted chunk by chunk as the
    application reads it; the first chunk that crosses the limit answers
    ``413`` (when no response has started), tells the application the client
    disconnected, and discards anything it still tries to send. Nothing is
    read ahead, so streaming responses and ``http.disconnect`` pass through
    unchanged.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    def _too_large(self) -> JSONResponse:
        return JSONResponse(
            {"error": "request_too_large", "max_bytes": self.max_bytes}, status_code=413
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared: str | None = None
        for name, value in scope.get("headers", ()):
            if name == b"content-length":
                declared = value.decode("latin-1")
                break
        if declared is not None:
            try:
                size = int(declared)
            except ValueError:
                size = -1
            if size < 0:
                await JSONResponse({"error": "invalid_content_length"}, status_code=400)(
                    scope, receive, send
                )
                return
            if size > self.max_bytes:
                await self._too_large()(scope, receive, send)
                return

        received = 0
        rejected = False
        started = False

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if rejected:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        async def limited_receive() -> Message:
            nonlocal received, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    rejected = True
                    if not started:
                        await self._too_large()(scope, receive, send)
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, limited_receive, guarded_send)


# ---------------------------------------------------------------------------
# Revocation and structural limits
# ---------------------------------------------------------------------------

_REDIS_CLIENTS: dict[str, Any] = {}
_REDIS_CLIENTS_LOCK = threading.Lock()


def _sync_redis(url: str) -> Any:
    with _REDIS_CLIENTS_LOCK:
        client = _REDIS_CLIENTS.get(url)
        if client is None:
            client = redis.Redis.from_url(url)
            _REDIS_CLIENTS[url] = client
        return client


def _principal_revoked(principal: str) -> bool:
    configured = {
        value.strip()
        for value in os.getenv("MCP_REVOKED_PRINCIPALS", "").split(",")
        if value.strip()
    }
    if principal in configured:
        return True
    redis_url = os.getenv("MCP_REDIS_URL", "").strip()
    if redis_url:
        try:
            return bool(_sync_redis(redis_url).sismember("mcp:revoked_principals", principal))
        except Exception as exc:
            raise ProtocolRejection(
                "authorization_backend_unavailable",
                "Principal revocation state could not be verified.",
                rpc_code=RPC_AUTHORIZATION_UNAVAILABLE,
                required_action={"action": "retry", "after_seconds": 5},
                retryable=True,
                retry_after=5,
            ) from exc
    return False


async def principal_revoked(principal: str) -> bool:
    """Revocation check off the event loop (fails closed on backend errors)."""
    return await run_blocking(_principal_revoked, principal)


def principal_revoked_rejection() -> ProtocolRejection:
    return ProtocolRejection(
        "principal_revoked",
        "This principal has been administratively invalidated.",
        rpc_code=RPC_UNAUTHORIZED,
        required_action={"action": "contact_administrator"},
    )


def _validate_payload_shape(value: Any, *, depth: int = 0) -> None:
    if depth > 20:
        raise ValueError("Tool arguments exceed the maximum nesting depth of 20.")
    if isinstance(value, str) and len(value) > 1_000_000:
        raise ValueError("A tool string argument exceeds the 1,000,000 character limit.")
    if isinstance(value, list):
        if len(value) > 10_000:
            raise ValueError("A tool array argument exceeds the 10,000 item limit.")
        for item in value:
            _validate_payload_shape(item, depth=depth + 1)
    elif isinstance(value, dict):
        if len(value) > 10_000:
            raise ValueError("A tool object argument exceeds the 10,000 property limit.")
        for item in value.values():
            _validate_payload_shape(item, depth=depth + 1)


# ---------------------------------------------------------------------------
# Admission controller shared by the middleware and task workers
# ---------------------------------------------------------------------------

_ADMISSION: AdmissionController | None = None
_ADMISSION_LOCK = threading.Lock()


def admission_controller() -> AdmissionController:
    """The process admission controller (built from the environment on first use)."""
    global _ADMISSION
    with _ADMISSION_LOCK:
        if _ADMISSION is None:
            _ADMISSION = AdmissionController.from_environment()
        return _ADMISSION


def set_admission_controller(controller: AdmissionController | None) -> None:
    global _ADMISSION
    with _ADMISSION_LOCK:
        _ADMISSION = controller


def _principal_key() -> str:
    try:
        return current_principal(require_authenticated=False).storage_key
    except Exception:
        return "unavailable"


def _outcome_of(result: object) -> str:
    if isinstance(result, InputRequiredToolResult):
        return "input_required"
    if isinstance(result, ToolResult):
        return "tool_error" if result.is_error else "ok"
    return "task_submitted"


def record_execution(
    *,
    tool: str,
    outcome: str,
    started: float,
    queue_ms: float | None,
    principal_hash: str,
    correlation_id: str,
    kind: str,
    continuation: bool,
) -> None:
    """Metrics and one privacy-safe log line per execution (or MRTR round)."""
    duration_ms = (time.perf_counter() - started) * 1_000
    queue = max(0.0, duration_ms if queue_ms is None else queue_ms)
    METRICS.observe(tool, outcome, duration_ms, queue)
    TOOL_CALLS.labels(tool, outcome).inc()
    if outcome != "input_required":
        LOGICAL_OPERATIONS.labels(tool, outcome).inc()
    TOOL_DURATION.labels(tool).observe(duration_ms / 1_000)
    TOOL_QUEUE.labels(tool).observe(queue / 1_000)
    LOGGER.info(json.dumps({
        "event": "mcp_tool_call",
        "tool": tool,
        "outcome": outcome,
        "phase": "round" if outcome == "input_required" else "complete",
        "continuation": continuation,
        "execution": kind,
        "duration_ms": round(duration_ms, 2),
        "queue_ms": round(queue, 2),
        "principal_hash": principal_hash,
        "correlation_id": correlation_id,
    }, separators=(",", ":")))


class ProductionControlMiddleware(Middleware):
    """Enforce bounded remote work and emit correlation-safe telemetry.

    The middleware owns the process :class:`AdmissionController` (shared with
    background-task executions, see ``common.execution``). Per-principal rate
    and concurrency are fleet-wide when a shared Redis is configured; global
    and expensive-tool concurrency are per process.
    """

    def __init__(self, controller: AdmissionController | None = None) -> None:
        if controller is None:
            controller = AdmissionController.from_environment()
            set_admission_controller(controller)
        self.controller = controller
        self._tracer = trace.get_tracer("mcp_google_workspace")

    # Backward-compatible views of the controller's limits/state.
    @property
    def limits(self) -> AdmissionLimits:
        return self.controller.limits

    @property
    def _principal_states(self) -> dict[str, Any]:
        return self.controller._principal_states

    def _admission_state(self, principal: str) -> Any:
        return self.controller.admission_state(principal)

    def _principal(self) -> str:
        return _principal_key()

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        tool = context.message.name
        _validate_payload_shape(context.message.arguments or {})
        correlation_id = uuid4().hex
        token = CORRELATION_ID.set(correlation_id)
        try:
            principal = self._principal()
            if await principal_revoked(principal):
                raise principal_revoked_rejection()
            try:
                await self.controller.check_rate(principal)
            except AdmissionError as exc:
                METRICS.rejections[exc.error_code] += 1
                ADMISSION_REJECTIONS.labels(exc.error_code).inc()
                raise
            if RUNTIME_STATE.draining:
                METRICS.rejections["draining"] += 1
                ADMISSION_REJECTIONS.labels("draining").inc()
                raise AdmissionError(
                    "server_draining", "Server is draining and not accepting new work.", retry_after=5
                )
            return await self._execute(context, call_next, tool, principal, correlation_id)
        finally:
            CORRELATION_ID.reset(token)

    async def _execute(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
        tool: str,
        principal: str,
        correlation_id: str,
    ) -> ToolResult:
        principal_hash = sha256(principal.encode()).hexdigest()[:16]
        deadline = self.controller.limits.deadline_for(tool_cost(tool))
        continuation = context.message.request_state is not None
        started = time.perf_counter()
        queue_ms: float | None = None
        outcome = "error"
        try:
            with self._tracer.start_as_current_span(
                f"mcp.tool.{tool}", context=tool_span_context(request_meta())
            ) as span, without_baggage():
                span.set_attribute("mcp.tool.name", tool)
                span.set_attribute("mcp.principal.hash", principal_hash)
                span.set_attribute("mcp.correlation_id", correlation_id)
                span.set_attribute("mcp.tool.continuation", continuation)
                span.set_attribute("mcp.execution", "request")
                async with self.controller.execution_slot(
                    principal, tool, kind="request", counters=RUNTIME_STATE
                ) as queued:
                    queue_ms = queued
                    ACTIVE_REQUESTS.inc()
                    try:
                        with anyio.fail_after(deadline):
                            result = await call_next(context)
                    finally:
                        ACTIVE_REQUESTS.dec()
                outcome = _outcome_of(result)
                # One multi-round-trip leg that asked the client a question is
                # a round, not a completed (or failed) logical operation.
                span.set_attribute(
                    "mcp.tool.round", "input_required" if outcome == "input_required" else "final"
                )
                span.set_attribute("mcp.tool.outcome", outcome)
                return result
        except TimeoutError as exc:
            outcome = "timeout"
            raise RecoverableToolError(
                "deadline_exceeded",
                f"Tool exceeded its {deadline}s deadline; it may or may not have completed.",
                required_action={"action": "verify_outcome_before_retry"},
            ) from exc
        finally:
            record_execution(
                tool=tool,
                outcome=outcome,
                started=started,
                queue_ms=queue_ms,
                principal_hash=principal_hash,
                correlation_id=correlation_id,
                kind="request",
                continuation=continuation,
            )


# ---------------------------------------------------------------------------
# Authorization-aware, deterministic catalog
# ---------------------------------------------------------------------------

_REMOTE_HIDDEN: Final[frozenset[str]] = frozenset(
    {
        "gmail_download_attachment",
        "calendar_download_event_attachment",
        "drive_download_file",
        "drive_export_google_file",
    }
)


def missing_capability_error(tool: str, capability: str) -> RecoverableToolError:
    return RecoverableToolError(
        "missing_capability",
        (
            f"{tool} needs the Google {capability} capability, which the caller has not "
            "granted (or has since revoked). Nothing was executed."
        ),
        required_action={
            "tool": "connect_google_workspace",
            "arguments": {"capabilities": [capability]},
        },
    )


class CapabilityCatalogMiddleware(Middleware):
    """Filter and enforce tools by the caller's *current* Google grants.

    ``tools/list`` is filtered against the grant read on that request and
    every catalog is sorted by name, so equal authorization yields the same
    ordering on any replica and page. ``tools/call`` re-checks the grant at
    execution time, so a catalog a client cached before a revocation cannot
    authorize a call. Unauthenticated (stdio) callers see and call the full
    catalog; the local user owns one complete grant.
    """

    _always = {
        "connect_google_workspace",
        "get_google_connection_status",
        "disconnect_google_workspace",
        "refresh_workspace_catalog",
        "get_workspace_capabilities",
        "get_mcp_apps_diagnostics",
        "search_workspace",
        "resolve_workspace_resource",
        "search_tools",
        "call_tool",
    }
    _remote_hidden = set(_REMOTE_HIDDEN)

    @staticmethod
    def _remote_tool(tool: Tool) -> Tool:
        clone = tool.model_copy()
        clone.parameters = copy.deepcopy(tool.parameters)
        local_fields = {"file_path", "local_path", "input_path", "output_path", "output_dir"}

        def strip(schema: Any) -> None:
            if not isinstance(schema, dict):
                return
            properties = schema.get("properties")
            if isinstance(properties, dict):
                for name in local_fields:
                    properties.pop(name, None)
                required = schema.get("required")
                if isinstance(required, list):
                    schema["required"] = [name for name in required if name not in local_fields]
                for child in properties.values():
                    strip(child)
            if "items" in schema:
                strip(schema["items"])
            for keyword in ("anyOf", "oneOf", "allOf"):
                branches = schema.get(keyword)
                if not isinstance(branches, list):
                    continue
                kept = []
                for branch in branches:
                    branch_required = set(branch.get("required", [])) if isinstance(branch, dict) else set()
                    if branch_required & local_fields:
                        continue
                    strip(branch)
                    kept.append(branch)
                schema[keyword] = kept

        strip(clone.parameters)
        return clone

    @staticmethod
    async def _grant() -> Any:
        from ..auth.grants import GrantSnapshot, read_grant_async

        try:
            return await read_grant_async()
        except Exception:  # noqa: BLE001 - unreadable grant authorizes nothing
            LOGGER.warning("Google grant could not be read; treating the caller as unconnected.")
            return GrantSnapshot(principal_key="unavailable", revision=None, capabilities=frozenset())

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        from ..auth.grants import is_tool_granted

        tools = sorted(await call_next(context), key=lambda tool: tool.name)
        if get_access_token() is None:
            return tools
        grant = await self._grant()
        return [
            self._remote_tool(tool)
            for tool in tools
            if tool.name not in self._remote_hidden and is_tool_granted(tool.name, grant)
        ]

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        from ..auth.grants import is_tool_granted, required_capability

        if get_access_token() is not None:
            name = context.message.name
            if name in self._remote_hidden:
                raise NotFoundError(f"Unknown tool: {name!r}")
            capability = required_capability(name)
            if capability is not None and not is_tool_granted(name, await self._grant()):
                raise missing_capability_error(name, capability)
        return await call_next(context)

    async def on_list_resources(
        self,
        context: MiddlewareContext[mt.ListResourcesRequest],
        call_next: CallNext[mt.ListResourcesRequest, Sequence[Resource]],
    ) -> Sequence[Resource]:
        return sorted(await call_next(context), key=lambda resource: str(resource.uri))

    async def on_list_resource_templates(
        self,
        context: MiddlewareContext[mt.ListResourceTemplatesRequest],
        call_next: CallNext[mt.ListResourceTemplatesRequest, Sequence[ResourceTemplate]],
    ) -> Sequence[ResourceTemplate]:
        return sorted(await call_next(context), key=lambda template: template.uri_template)

    async def on_list_prompts(
        self,
        context: MiddlewareContext[mt.ListPromptsRequest],
        call_next: CallNext[mt.ListPromptsRequest, Sequence[Prompt]],
    ) -> Sequence[Prompt]:
        return sorted(await call_next(context), key=lambda prompt: prompt.name)


class ConsequentialActionMiddleware(Middleware):
    """Require prepare/commit for high-impact otherwise-reversible writes."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        arguments = dict(context.message.arguments or {})
        if not COMMIT_ACTIVE.get() and requires_prepare(context.message.name, arguments):
            raise RecoverableToolError(
                "prepare_required",
                "This consequential action requires a short-lived impact preview before commit.",
                required_action={
                    "tool": "prepare_workspace_action",
                    "arguments": {"tool_name": context.message.name, "arguments": arguments},
                },
            )
        return await call_next(context)


# ---------------------------------------------------------------------------
# Version and readiness
# ---------------------------------------------------------------------------

# MCP protocol revisions this build's automated suite exercises end to end:
# the stateless 2026-07-28 era plus the newest initialize-handshake era. This is
# deliberately independent of the package version, and narrower than the full
# set the SDK is able to negotiate.
TESTED_MCP_PROTOCOL_VERSIONS: Final[tuple[str, ...]] = ("2025-11-25", "2026-07-28")


def build_version_payload() -> dict[str, Any]:
    try:
        package_version = version("mcp-google-workspace")
    except PackageNotFoundError:
        package_version = "development"
    return {
        "name": "mcp-google-workspace",
        "version": package_version,
        "commit": os.getenv("MCP_BUILD_COMMIT", "unknown"),
        "protocol_transport": "streamable-http",
        "mcp_protocol_version": LATEST_MODERN_VERSION,
        "mcp_protocol_versions": {
            "preferred": LATEST_MODERN_VERSION,
            "modern": list(MODERN_PROTOCOL_VERSIONS),
            "legacy": list(HANDSHAKE_PROTOCOL_VERSIONS),
            "tested": list(TESTED_MCP_PROTOCOL_VERSIONS),
        },
    }


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(env: Mapping[str, str], name: str) -> int:
    raw = env.get(name, "").strip() or "1"
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def deployment_topology(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Whether this process belongs to a multi-process fleet.

    A fleet is declared by ``MCP_WORKERS`` > 1 (processes per replica),
    ``MCP_REPLICAS`` > 1 (replicas behind a load balancer, including
    single-worker ones), or a shared backend (``MCP_REDIS_URL`` outside the
    stdio bundle), which only exists to be shared.
    """
    env = os.environ if environ is None else environ
    workers = _positive_int(env, "MCP_WORKERS")
    replicas = _positive_int(env, "MCP_REPLICAS")
    bundle = env.get("MCP_RUNTIME_MODE", "").strip().lower() == "bundle"
    shared_backend = bool(env.get("MCP_REDIS_URL", "").strip()) and not bundle
    return {
        "workers": workers,
        "replicas": replicas,
        "shared_backend": shared_backend,
        "fleet": workers > 1 or replicas > 1 or shared_backend,
    }


def _ping_redis(url: str) -> dict[str, Any]:
    try:
        redis.Redis.from_url(url).ping()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": exc.__class__.__name__}


def readiness_report() -> tuple[bool, dict[str, Any]]:
    """Verify secret, shared-state, queue and continuation-key dependencies.

    Every check with ``"required": false`` is advisory: it is reported but does
    not take the replica out of rotation.
    """
    checks: dict[str, dict[str, Any]] = {}
    topology = deployment_topology()
    fleet = bool(topology["fleet"])
    try:
        from ..runtime import get_token_storage_settings
        from ..auth.google_auth import get_token_store

        storage = get_token_storage_settings()
        checks["encryption"] = {"ok": bool(storage.keyring.active_key_id)}
        token_store = get_token_store()
        checks["token_storage"] = {
            "ok": token_store.ping(),
            "backend": token_store.backend_name,
        }
    except Exception as exc:
        checks["configuration"] = {"ok": False, "error": exc.__class__.__name__}
    redis_url = os.getenv("MCP_REDIS_URL", "").strip()
    if redis_url:
        checks["redis"] = _ping_redis(redis_url)
    bucket = os.getenv("MCP_UPLOAD_S3_BUCKET", "").strip()
    if bucket:
        try:
            import boto3

            boto3.client("s3", endpoint_url=os.getenv("MCP_UPLOAD_S3_ENDPOINT") or None).head_bucket(
                Bucket=bucket
            )
            checks["upload_object_storage"] = {"ok": True}
        except Exception as exc:
            checks["upload_object_storage"] = {"ok": False, "error": exc.__class__.__name__}

    # W3 application state (dashboard views): memory for one process, Redis for a fleet.
    try:
        from .app_state import app_state_backend_url

        state_url = app_state_backend_url()
        app_state: dict[str, Any] = {"backend": "redis" if state_url else "memory"}
        if state_url:
            app_state.update(checks["redis"] if state_url == redis_url and "redis" in checks else _ping_redis(state_url))
        else:
            app_state["ok"] = not fleet
            if fleet:
                app_state["requirement"] = "shared Redis app state (MCP_REDIS_URL) for a fleet"
        checks["app_state"] = app_state
    except Exception as exc:
        checks["app_state"] = {"ok": False, "error": exc.__class__.__name__}

    # MCP Tasks queue: shared and snapshot-encrypted whenever it is distributed.
    try:
        from .task_backend import resolve_task_backend_config

        task_config = resolve_task_backend_config()
        queue: dict[str, Any] = task_config.diagnostics()
        if task_config.distributed:
            queue.update(_ping_redis(task_config.url))
            if not task_config.snapshot_encryption:
                queue["ok"] = False
                queue["requirement"] = "FASTMCP_TASKS_ENCRYPTION_KEY shared by every server and worker"
        else:
            queue["ok"] = not fleet
            if fleet:
                queue["requirement"] = "shared task queue (MCP_REDIS_URL or FASTMCP_DOCKET_URL) for a fleet"
        checks["task_queue"] = queue
    except Exception as exc:
        checks["task_queue"] = {"ok": False, "error": exc.__class__.__name__}

    if fleet:
        # Multi-round-trip confirmations resume on whichever replica receives
        # the answering request, so continuation state must be sealed with a
        # key ring every replica shares (independent of the token and task keys).
        checks["continuation_keys"] = {
            "ok": shared_request_state_keys_configured(),
            "requirement": "MCP_REQUEST_STATE_KEYS shared by every replica",
        }
        token_backend = checks.get("token_storage", {}).get("backend")
        checks["fleet_storage"] = {
            "ok": bool(redis_url and bucket and token_backend == "redis"),  # nosec B105 -- backend identifier
            **topology,
            "requirement": (
                "Redis-backed OAuth credentials/state (MCP_REDIS_URL) and shared upload "
                "object storage (MCP_UPLOAD_S3_BUCKET)"
            ),
        }
        # Modern (2026-07-28) traffic is stateless and needs no affinity. Only
        # handshake-era sessions (Mcp-Session-Id) live in one process; they keep
        # working behind a load balancer only with session affinity.
        checks["legacy_session_affinity"] = {
            "ok": _truthy(os.getenv("MCP_SESSION_AFFINITY")),
            "required": False,
            "scope": "legacy (handshake-era) clients only",
            "requirement": "load-balancer affinity on Mcp-Session-Id, confirmed by MCP_SESSION_AFFINITY=true",
        }
    required = [check for check in checks.values() if check.get("required", True)]
    ready = RUNTIME_STATE.ready() and all(bool(check.get("ok")) for check in required)
    warnings = sorted(
        name for name, check in checks.items() if not check.get("required", True) and not check.get("ok")
    )
    try:
        admission_scope = admission_controller().scope
    except ValueError:
        admission_scope = "misconfigured"
    return ready, {
        "status": "ready" if ready else "not_ready",
        "fleet": fleet,
        "checks": checks,
        "warnings": warnings,
        "admission_scope": admission_scope,
    }


@asynccontextmanager
async def production_lifespan(_: Any):
    """Drain in-flight requests and task executions for a bounded interval."""
    RUNTIME_STATE.draining = False
    try:
        yield
    finally:
        RUNTIME_STATE.begin_draining()
        # Claude Desktop owns the stdio process lifetime. Once stdin closes it
        # must be able to remove the extension directory immediately; HTTP-style
        # draining here can make uninstall wait for the full grace period.
        if os.getenv("MCP_RUNTIME_MODE", "").strip().lower() == "bundle":
            return
        deadline = time.monotonic() + _integer_env("MCP_SHUTDOWN_GRACE_SECONDS", 30, 1, 300)
        while RUNTIME_STATE.active_total and time.monotonic() < deadline:
            await anyio.sleep(0.05)
