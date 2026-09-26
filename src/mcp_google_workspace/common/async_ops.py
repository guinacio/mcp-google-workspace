"""Async helpers for offloading blocking SDK and filesystem work."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
import logging
from pathlib import Path
import time
from typing import Any, TypeVar

import anyio
from fastmcp import Context
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS

from .errors import ConfirmationRequiredError

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


@dataclass
class Confirmation:
    """Legacy elicitation schema with an explicit ``confirm`` checkbox."""

    confirm: bool


def _is_legacy_request(ctx: Context) -> bool:
    """Whether this request negotiated a handshake-era protocol that supports ``ctx.elicit``.

    An allowlist, so an unknown or missing version (e.g. worker execution
    without a live request) fails closed instead of attempting elicitation.
    """
    request_context = getattr(ctx, "request_context", None)
    version = getattr(request_context, "protocol_version", None)
    return version in HANDSHAKE_PROTOCOL_VERSIONS


async def confirm_destructive_action(
    ctx: Context | None,
    action_name: str,
    message: str,
    *,
    explicit_confirm_field: bool = False,
) -> bool:
    """Gate an irreversible action on explicit user confirmation.

    Returns ``True`` only for an accepted, affirmative answer; ``False`` means
    the user declined or cancelled and the caller must not mutate anything.

    W4: this is the single seam the confirmation adapter replaces.
    * Legacy (handshake-era) requests keep imperative ``ctx.elicit``.
    * MCP 2026-07-28 has no server-initiated requests, so ``ctx.elicit`` is
      unavailable there. Until W4 adds the multi-round-trip
      ``InputRequiredResult`` branch, a modern request fails closed: nothing is
      mutated and the caller receives a ``confirmation_required`` tool result.
      Unavailable confirmation is never treated as consent.
    """
    if ctx is None or not _is_legacy_request(ctx):
        raise ConfirmationRequiredError(action_name, message)
    if explicit_confirm_field:
        response = await ctx.elicit(message, response_type=Confirmation)
        return response.action == "accept" and bool(
            getattr(response.data, "confirm", False)
        )
    answer = await ctx.elicit(message, response_type=bool)
    return answer.action == "accept" and bool(getattr(answer, "data", False))


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
