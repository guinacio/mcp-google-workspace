"""Async helpers for offloading blocking SDK and filesystem work."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
import logging
from pathlib import Path
import time
from typing import Any, TypeVar

import anyio
from fastmcp import Context

T = TypeVar("T")
LOGGER = logging.getLogger("mcp_google_workspace.google_api")


class ProviderCircuitOpen(RuntimeError):
    """Raised while a failing Google service circuit is cooling down."""

    error_code = "provider_unavailable"
    retry_after = 30.0
    required_action = {"action": "retry", "after_seconds": 30}


class _CircuitBreaker:
    def __init__(self) -> None:
        self.failures: dict[str, deque[float]] = defaultdict(deque)
        self.opened_at: dict[str, float] = {}

    def allow(self, service: str) -> None:
        opened = self.opened_at.get(service)
        if opened is None:
            return
        elapsed = time.monotonic() - opened
        if elapsed < ProviderCircuitOpen.retry_after:
            error = ProviderCircuitOpen(f"Google {service} is temporarily unavailable.")
            error.retry_after = ProviderCircuitOpen.retry_after - elapsed
            error.required_action = {
                "action": "retry",
                "after_seconds": round(error.retry_after, 2),
            }
            raise error
        self.opened_at.pop(service, None)
        self.failures.pop(service, None)

    def success(self, service: str) -> None:
        self.failures.pop(service, None)
        self.opened_at.pop(service, None)

    def failure(self, service: str) -> None:
        now = time.monotonic()
        failures = self.failures[service]
        while failures and failures[0] < now - 60:
            failures.popleft()
        failures.append(now)
        if len(failures) >= 5:
            self.opened_at[service] = now


_CIRCUITS = _CircuitBreaker()


def require_elicitation_context(ctx: Context | None, action_name: str) -> Context:
    """Validate that *ctx* is not ``None`` before an elicitation call.

    Returns the narrowed ``Context`` so callers can use it directly.
    """
    if ctx is None:
        raise RuntimeError(f"{action_name} requires MCP context for user confirmation.")
    return ctx


async def confirm_destructive_action(
    ctx: Context | None,
    action_name: str,
    message: str,
) -> bool:
    """Require an explicit host-mediated confirmation for an irreversible action."""
    confirm_ctx = require_elicitation_context(ctx, action_name)
    response = await confirm_ctx.elicit(
        message,
        response_type=bool,  # type: ignore[arg-type]
    )
    return response.action == "accept" and bool(response.data)


async def run_blocking(
    func: Callable[..., T],
    /,
    *args: Any,
    **kwargs: Any,
) -> T:
    return await anyio.to_thread.run_sync(
        lambda: func(*args, **kwargs), abandon_on_cancel=True
    )


async def execute_google_request(request: Any) -> Any:
    from .production import GOOGLE_REQUESTS

    service = str(getattr(request, "_api_name", request.__class__.__name__)).lower()
    _CIRCUITS.allow(service)
    started = time.perf_counter()
    try:
        result = await run_blocking(request.execute)
        _CIRCUITS.success(service)
        GOOGLE_REQUESTS.labels(service, "ok").inc()
        LOGGER.info(
            "google_api service=%s outcome=ok duration_ms=%.2f",
            service,
            (time.perf_counter() - started) * 1_000,
        )
        return result
    except Exception as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status in {500, 502, 503, 504} or isinstance(exc, TimeoutError):
            _CIRCUITS.failure(service)
        GOOGLE_REQUESTS.labels(service, "error").inc()
        LOGGER.warning(
            "google_api service=%s outcome=error status=%s duration_ms=%.2f",
            service,
            status,
            (time.perf_counter() - started) * 1_000,
        )
        message = str(exc).lower()
        auth_failure = status == 401 or any(
            marker in message
            for marker in ("invalid_grant", "invalid_token", "unauthenticated", "token has been expired")
        )
        if auth_failure:
            # Real Google requests conditionally invalidate the exact credential
            # generation that failed. Unknown/custom requests preserve storage
            # rather than deleting a potentially newer concurrent refresh.
            raise RuntimeError(
                '{"error":"reauth_required","action":"Retry the request to start Google Workspace OAuth consent"}'
            ) from exc
        raise


async def read_text_file(path: Path, *, encoding: str = "utf-8") -> str:
    return await run_blocking(path.read_text, encoding=encoding)


async def read_bytes_file(path: Path) -> bytes:
    return await run_blocking(path.read_bytes)


async def write_bytes_file(path: Path, data: bytes) -> int:
    return await run_blocking(path.write_bytes, data)


async def unlink_file(path: Path, *, missing_ok: bool = False) -> None:
    await run_blocking(path.unlink, missing_ok=missing_ok)
