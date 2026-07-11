"""Production admission control, lifecycle state, and privacy-safe telemetry."""

from __future__ import annotations

import anyio
import copy
from collections import defaultdict, deque
from collections.abc import Sequence
from contextvars import ContextVar
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
import logging
import os
import time
from typing import Any
from uuid import uuid4

import mcp.types as mt
import redis
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool
from fastmcp.tools.base import ToolResult
from opentelemetry import trace
from prometheus_client import Counter, Gauge, Histogram
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..auth.identity import current_principal
from .approvals import COMMIT_ACTIVE, requires_prepare
from .errors import RecoverableToolError

LOGGER = logging.getLogger("mcp_google_workspace.production")
CORRELATION_ID: ContextVar[str | None] = ContextVar("mcp_correlation_id", default=None)


class AdmissionError(RuntimeError):
    """A structured, retryable admission-control rejection."""

    def __init__(self, code: str, message: str, *, retry_after: float) -> None:
        super().__init__(message)
        self.error_code = code
        self.retry_after = retry_after
        self.required_action = {"action": "retry", "after_seconds": retry_after}


@dataclass(slots=True)
class RuntimeState:
    started_at: float = field(default_factory=time.time)
    draining: bool = False
    active_requests: int = 0

    def begin_draining(self) -> None:
        self.draining = True

    def ready(self) -> bool:
        return not self.draining


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
            "calls": {f"{tool}:{outcome}": count for (tool, outcome), count in self.calls.items()},
            "duration_ms": {tool: summary(values) for tool, values in self.duration_ms.items()},
            "queue_ms": {tool: summary(values) for tool, values in self.queue_ms.items()},
            "rejections": dict(self.rejections),
        }


METRICS = Metrics()
TOOL_CALLS = Counter(
    "mcp_workspace_tool_calls_total", "Workspace tool calls", ("tool", "outcome")
)
TOOL_DURATION = Histogram(
    "mcp_workspace_tool_duration_seconds", "Workspace tool duration", ("tool",)
)
TOOL_QUEUE = Histogram(
    "mcp_workspace_tool_queue_seconds", "Workspace tool admission queue duration", ("tool",)
)
ACTIVE_REQUESTS = Gauge("mcp_workspace_active_requests", "Currently executing Workspace tools")
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
UPLOAD_BYTES = Gauge(
    "mcp_workspace_upload_bytes", "Live uploaded bytes", ("principal_hash", "backend")
)
UPLOAD_CLEANUPS = Counter(
    "mcp_workspace_upload_cleanup_total", "Expired upload objects cleaned", ("backend",)
)
IDEMPOTENCY_EVENTS = Counter(
    "mcp_workspace_idempotency_events_total", "Idempotency claim events", ("event", "backend")
)


class _Window:
    def __init__(self) -> None:
        self.timestamps: deque[float] = deque()
        self.lock = anyio.Lock()

    async def consume(self, limit: int, seconds: float) -> float | None:
        now = time.monotonic()
        cutoff = now - seconds
        async with self.lock:
            while self.timestamps and self.timestamps[0] <= cutoff:
                self.timestamps.popleft()
            if len(self.timestamps) >= limit:
                return max(0.05, seconds - (now - self.timestamps[0]))
            self.timestamps.append(now)
        return None


def _integer_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return value


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject declared oversized HTTP bodies before MCP JSON parsing."""

    def __init__(self, app: Any, *, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
            except ValueError:
                return JSONResponse({"error": "invalid_content_length"}, status_code=400)
            if size > self.max_bytes:
                return JSONResponse(
                    {"error": "request_too_large", "max_bytes": self.max_bytes},
                    status_code=413,
                )
        body = await request.body()
        if len(body) > self.max_bytes:
            return JSONResponse(
                {"error": "request_too_large", "max_bytes": self.max_bytes},
                status_code=413,
            )
        return await call_next(request)


def _tool_cost(name: str) -> str:
    lowered = name.lower()
    if any(part in lowered for part in ("gemini", "video", "audio", "export", "download", "batch")):
        return "expensive"
    return "standard"


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
            return bool(redis.Redis.from_url(redis_url).sismember("mcp:revoked_principals", principal))
        except Exception as exc:
            raise RecoverableToolError(
                "authorization_backend_unavailable",
                "Principal revocation state could not be verified.",
                required_action={"action": "retry", "after_seconds": 5},
                retryable=True,
                retry_after=5,
            ) from exc
    return False


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


class ProductionControlMiddleware(Middleware):
    """Enforce bounded remote work and emit correlation-safe telemetry."""

    def __init__(self) -> None:
        self.rate_limit = _integer_env("MCP_RATE_LIMIT_PER_MINUTE", 120, 1, 100_000)
        self.global_limit = anyio.Semaphore(_integer_env("MCP_GLOBAL_CONCURRENCY", 64, 1, 10_000))
        self.principal_limit = _integer_env("MCP_PRINCIPAL_CONCURRENCY", 8, 1, 1_000)
        self.expensive_limit = anyio.Semaphore(_integer_env("MCP_EXPENSIVE_CONCURRENCY", 4, 1, 1_000))
        self.standard_deadline = _integer_env("MCP_TOOL_DEADLINE_SECONDS", 120, 1, 3_600)
        self.expensive_deadline = _integer_env("MCP_EXPENSIVE_DEADLINE_SECONDS", 600, 1, 7_200)
        self._windows: dict[str, _Window] = defaultdict(_Window)
        self._principal_semaphores: dict[str, anyio.Semaphore] = {}
        self._tracer = trace.get_tracer("mcp_google_workspace")

    def _principal(self) -> str:
        try:
            return current_principal(require_authenticated=False).storage_key
        except Exception:
            return "unavailable"

    def _semaphore(self, principal: str) -> anyio.Semaphore:
        semaphore = self._principal_semaphores.get(principal)
        if semaphore is None:
            semaphore = anyio.Semaphore(self.principal_limit)
            self._principal_semaphores[principal] = semaphore
        return semaphore

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        tool = context.message.name
        _validate_payload_shape(context.message.arguments or {})
        correlation_id = uuid4().hex
        token = CORRELATION_ID.set(correlation_id)
        principal = self._principal()
        if _principal_revoked(principal):
            CORRELATION_ID.reset(token)
            raise RecoverableToolError(
                "principal_revoked",
                "This principal has been administratively invalidated.",
                required_action={"action": "contact_administrator"},
            )
        principal_hash = sha256(principal.encode()).hexdigest()[:16]
        cost = _tool_cost(tool)
        deadline = self.expensive_deadline if cost == "expensive" else self.standard_deadline
        retry_after = await self._windows[principal].consume(self.rate_limit, 60.0)
        if retry_after is not None:
            METRICS.rejections["rate_limited"] += 1
            ADMISSION_REJECTIONS.labels("rate_limited").inc()
            CORRELATION_ID.reset(token)
            raise AdmissionError("rate_limited", "Per-principal request rate exceeded.", retry_after=retry_after)
        if RUNTIME_STATE.draining:
            METRICS.rejections["draining"] += 1
            ADMISSION_REJECTIONS.labels("draining").inc()
            CORRELATION_ID.reset(token)
            raise AdmissionError("server_draining", "Server is draining and not accepting new work.", retry_after=5)

        started = time.perf_counter()
        queue_started = started
        outcome = "ok"
        try:
            with self._tracer.start_as_current_span(f"mcp.tool.{tool}") as span:
                span.set_attribute("mcp.tool.name", tool)
                span.set_attribute("mcp.principal.hash", principal_hash)
                span.set_attribute("mcp.correlation_id", correlation_id)
                async with self.global_limit, self._semaphore(principal):
                    expensive = self.expensive_limit if cost == "expensive" else _NullSemaphore()
                    async with expensive:
                        queue_ms = (time.perf_counter() - queue_started) * 1_000
                        RUNTIME_STATE.active_requests += 1
                        ACTIVE_REQUESTS.inc()
                        try:
                            with anyio.fail_after(deadline):
                                return await call_next(context)
                        finally:
                            RUNTIME_STATE.active_requests -= 1
                            ACTIVE_REQUESTS.dec()
        except TimeoutError as exc:
            outcome = "timeout"
            error = AdmissionError("deadline_exceeded", f"Tool exceeded its {deadline}s deadline.", retry_after=1)
            raise error from exc
        except BaseException:
            outcome = "error"
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1_000
            queue_ms = max(0.0, duration_ms if "queue_ms" not in locals() else queue_ms)
            METRICS.observe(tool, outcome, duration_ms, queue_ms)
            TOOL_CALLS.labels(tool, outcome).inc()
            TOOL_DURATION.labels(tool).observe(duration_ms / 1_000)
            TOOL_QUEUE.labels(tool).observe(queue_ms / 1_000)
            LOGGER.info(json.dumps({
                "event": "mcp_tool_call",
                "tool": tool,
                "outcome": outcome,
                "duration_ms": round(duration_ms, 2),
                "queue_ms": round(queue_ms, 2),
                "principal_hash": principal_hash,
                "correlation_id": correlation_id,
            }, separators=(",", ":")))
            CORRELATION_ID.reset(token)


class _NullSemaphore:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class CapabilityCatalogMiddleware(Middleware):
    """Hide tools whose Google capability is not currently authorized."""

    _always = {
        "connect_google_workspace",
        "get_google_connection_status",
        "disconnect_google_workspace",
        "refresh_workspace_catalog",
        "get_workspace_capabilities",
        "search_workspace",
        "resolve_workspace_resource",
        "search_tools",
        "call_tool",
    }
    _remote_hidden = {
        "gmail_download_attachment",
        "calendar_download_event_attachment",
        "drive_download_file",
        "drive_export_google_file",
    }

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

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        tools = list(await call_next(context))
        if get_access_token() is None:
            return tools
        try:
            from ..auth.google_oauth import google_connection_status

            granted = set(google_connection_status().get("granted_capabilities", []))
        except Exception:
            granted = set()
        infrastructure = {"files", "apps"}
        return [
            self._remote_tool(tool)
            for tool in tools
            if tool.name not in self._remote_hidden
            if tool.name in self._always
            or tool.name.split("_", 1)[0] in infrastructure
            or tool.name.split("_", 1)[0] in granted
        ]


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
        "mcp_protocol_version": mt.LATEST_PROTOCOL_VERSION,
    }


def readiness_report() -> tuple[bool, dict[str, Any]]:
    """Verify secret, durable-state, and object-storage dependencies."""
    checks: dict[str, dict[str, Any]] = {}
    try:
        from ..runtime import get_token_storage_settings

        storage = get_token_storage_settings()
        storage.user_token_dir.mkdir(parents=True, exist_ok=True)
        checks["encryption"] = {"ok": bool(storage.keyring.active_key_id)}
        checks["token_storage"] = {"ok": os.access(storage.user_token_dir, os.W_OK)}
    except Exception as exc:
        checks["configuration"] = {"ok": False, "error": exc.__class__.__name__}
    redis_url = os.getenv("MCP_REDIS_URL", "").strip()
    if redis_url:
        try:
            redis.Redis.from_url(redis_url).ping()
            checks["redis"] = {"ok": True}
        except Exception as exc:
            checks["redis"] = {"ok": False, "error": exc.__class__.__name__}
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
    workers = int(os.getenv("MCP_WORKERS", "1"))
    if workers > 1:
        affinity = os.getenv("MCP_SESSION_AFFINITY", "").strip().lower() in {
            "1", "true", "yes", "on"
        }
        distributed = bool(redis_url and bucket and affinity)
        checks["multi_worker_storage"] = {
            "ok": distributed,
            "workers": workers,
            "requirement": (
                "MCP_REDIS_URL, MCP_UPLOAD_S3_BUCKET, and load-balancer "
                "Mcp-Session-Id affinity confirmed by MCP_SESSION_AFFINITY=true"
            ),
        }
    ready = RUNTIME_STATE.ready() and all(bool(check.get("ok")) for check in checks.values())
    return ready, {"status": "ready" if ready else "not_ready", "checks": checks}


@asynccontextmanager
async def production_lifespan(_: Any):
    """Drain in-flight work for a bounded interval during server shutdown."""
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
        while RUNTIME_STATE.active_requests and time.monotonic() < deadline:
            await anyio.sleep(0.05)
