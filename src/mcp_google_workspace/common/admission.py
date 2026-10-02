"""Admission control: per-process and fleet-wide limits for requests and tasks.

Two scopes, deliberately distinct:

**Per process** (always in-process ``anyio`` semaphores; they protect this
process's threads, sockets and memory):

* ``MCP_GLOBAL_CONCURRENCY`` — tool executions running in this process
  (foreground requests plus background-task executions of its worker).
* ``MCP_EXPENSIVE_CONCURRENCY`` — Gemini/download/export/batch executions in
  this process.

**Per principal** (the documented policy is "per user", so it must hold no
matter which replica serves a request):

* ``MCP_RATE_LIMIT_PER_MINUTE`` — sliding one-minute request rate.
* ``MCP_PRINCIPAL_CONCURRENCY`` — tool executions of one principal in flight.

With a shared backend (``MCP_REDIS_URL`` outside the stdio bundle, or
``MCP_ADMISSION_BACKEND=redis``) the per-principal limits are enforced
fleet-wide in Redis (sorted-set sliding window and expiring concurrency
leases). Without one (stdio, a single development process) they are enforced
in process, which is then the whole "fleet". Background-task executions take
the same concurrency slots as foreground calls; only the request rate is
charged once, when the task is submitted.

A shared backend that cannot be reached fails closed with a retryable
protocol rejection rather than silently dropping the policy.
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from hashlib import sha256
import logging
import os
import secrets
import time
from typing import Any, Final, Literal, Protocol

import anyio

from .errors import RPC_RATE_LIMITED, RPC_SERVER_UNAVAILABLE, ProtocolRejection

LOGGER = logging.getLogger("mcp_google_workspace.admission")

ExecutionKind = Literal["request", "task"]

_RATE_WINDOW_MS: Final[int] = 60_000
_LEASE_GRACE_SECONDS: Final[int] = 30
_LEASE_POLL_SECONDS: Final[float] = 0.05
_REDIS_PREFIX: Final[str] = "mcp:admission"

# Sliding-window rate limit: KEYS[1] sorted set; ARGV now_ms, window_ms, limit, member.
_RATE_SCRIPT: Final[str] = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[3]) then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  return {0, tostring(tonumber(oldest[2]) + window - now)}
end
redis.call('ZADD', KEYS[1], now, ARGV[4])
redis.call('PEXPIRE', KEYS[1], window)
return {1, '0'}
"""

# Concurrency lease: KEYS[1] sorted set of lease-id -> expiry; ARGV now_ms, limit, lease, expiry_ms.
_LEASE_SCRIPT: Final[str] = """
local now = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
  return 0
end
redis.call('ZADD', KEYS[1], tonumber(ARGV[4]), ARGV[3])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[4]) - now)
return 1
"""


class AdmissionError(ProtocolRejection):
    """A structured, retryable admission-control rejection (JSON-RPC error)."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retry_after: float,
        rpc_code: int | None = None,
    ) -> None:
        super().__init__(
            code,
            message,
            rpc_code=rpc_code if rpc_code is not None else (
                RPC_RATE_LIMITED if code == "rate_limited" else RPC_SERVER_UNAVAILABLE
            ),
            required_action={"action": "retry", "after_seconds": round(retry_after, 2)},
            retryable=True,
            retry_after=retry_after,
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


@dataclass(frozen=True, slots=True)
class AdmissionLimits:
    rate_limit_per_minute: int = 120
    global_concurrency: int = 64
    principal_concurrency: int = 8
    expensive_concurrency: int = 4
    standard_deadline: int = 120
    expensive_deadline: int = 600
    principal_state_limit: int = 10_000
    principal_state_ttl: int = 900
    principal_queue_seconds: int = 10

    @classmethod
    def from_environment(cls) -> AdmissionLimits:
        return cls(
            rate_limit_per_minute=_integer_env("MCP_RATE_LIMIT_PER_MINUTE", 120, 1, 100_000),
            global_concurrency=_integer_env("MCP_GLOBAL_CONCURRENCY", 64, 1, 10_000),
            principal_concurrency=_integer_env("MCP_PRINCIPAL_CONCURRENCY", 8, 1, 1_000),
            expensive_concurrency=_integer_env("MCP_EXPENSIVE_CONCURRENCY", 4, 1, 1_000),
            standard_deadline=_integer_env("MCP_TOOL_DEADLINE_SECONDS", 120, 1, 3_600),
            expensive_deadline=_integer_env("MCP_EXPENSIVE_DEADLINE_SECONDS", 600, 1, 7_200),
            principal_state_limit=_integer_env("MCP_PRINCIPAL_STATE_LIMIT", 10_000, 100, 1_000_000),
            principal_state_ttl=_integer_env("MCP_PRINCIPAL_STATE_TTL_SECONDS", 900, 60, 86_400),
            principal_queue_seconds=_integer_env("MCP_PRINCIPAL_QUEUE_SECONDS", 10, 0, 600),
        )

    def deadline_for(self, cost: str) -> int:
        return self.expensive_deadline if cost == "expensive" else self.standard_deadline


def tool_cost(name: str) -> str:
    lowered = name.lower()
    if any(part in lowered for part in ("gemini", "video", "audio", "export", "download", "batch")):
        return "expensive"
    return "standard"


def admission_backend_url(environ: Mapping[str, str] | None = None) -> str | None:
    """Redis URL for fleet-wide admission, or ``None`` for process-local limits."""
    env = os.environ if environ is None else environ
    mode = env.get("MCP_ADMISSION_BACKEND", "auto").strip().lower() or "auto"
    if mode not in {"auto", "local", "redis"}:
        raise ValueError("MCP_ADMISSION_BACKEND must be auto, local, or redis.")
    if mode == "local":
        return None
    url = env.get("MCP_REDIS_URL", "").strip()
    bundle = env.get("MCP_RUNTIME_MODE", "").strip().lower() == "bundle"
    if mode == "redis" and not url:
        raise ValueError("MCP_ADMISSION_BACKEND=redis requires MCP_REDIS_URL.")
    if not url or (bundle and mode == "auto"):
        return None
    return url


# ---------------------------------------------------------------------------
# Process-local per-principal state
# ---------------------------------------------------------------------------


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


@dataclass(slots=True)
class _PrincipalAdmission:
    window: _Window
    semaphore: anyio.Semaphore
    last_seen: float


# ---------------------------------------------------------------------------
# Fleet-wide per-principal state in Redis
# ---------------------------------------------------------------------------


class RedisFleetLimits:
    """Fleet-wide per-principal rate and concurrency on a shared Redis.

    ``client`` exposes a ``redis.asyncio.Redis``-compatible ``eval`` and
    ``zrem``; tests pass an in-memory Redis implementation.
    """

    backend_name = "redis"

    def __init__(self, client: Any, *, prefix: str = _REDIS_PREFIX) -> None:
        self._client = client
        self._prefix = prefix.rstrip(":")

    @classmethod
    def from_url(cls, url: str) -> RedisFleetLimits:
        import redis.asyncio as redis_asyncio

        return cls(redis_asyncio.Redis.from_url(url))

    def _key(self, kind: str, principal: str) -> str:
        digest = sha256(principal.encode("utf-8")).hexdigest()[:32]
        return f"{self._prefix}:{kind}:{digest}"

    async def consume_rate(self, principal: str, limit: int) -> float | None:
        now = int(time.time() * 1000)
        member = f"{now}:{secrets.token_hex(6)}"
        try:
            raw = await self._client.eval(
                _RATE_SCRIPT, 1, self._key("rate", principal),
                str(now), str(_RATE_WINDOW_MS), str(limit), member,
            )
        except Exception as exc:  # noqa: BLE001 - any backend failure fails closed
            raise _backend_unavailable() from exc
        if int(raw[0]) == 1:
            return None
        retry_ms = float(raw[1].decode() if isinstance(raw[1], bytes) else raw[1])
        return max(0.05, retry_ms / 1000)

    async def try_lease(self, principal: str, limit: int, lease: str, ttl_seconds: float) -> bool:
        now = int(time.time() * 1000)
        expiry = now + int(ttl_seconds * 1000)
        try:
            acquired = await self._client.eval(
                _LEASE_SCRIPT, 1, self._key("active", principal),
                str(now), str(limit), lease, str(expiry),
            )
        except Exception as exc:  # noqa: BLE001
            raise _backend_unavailable() from exc
        return int(acquired) == 1

    async def release_lease(self, principal: str, lease: str) -> None:
        try:
            await self._client.zrem(self._key("active", principal), lease)
        except Exception:  # noqa: BLE001 - the lease expires on its own
            LOGGER.warning("admission lease release failed; it expires with its TTL")


def _backend_unavailable() -> AdmissionError:
    return AdmissionError(
        "admission_backend_unavailable",
        "Admission state could not be verified; the request was not admitted.",
        retry_after=5,
    )


class _NullAsyncContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class ActivityCounters(Protocol):
    """Work currently executing in this process (drain and readiness input)."""

    active_requests: int
    active_tasks: int


class AdmissionController:
    """Rate and concurrency admission shared by foreground calls and task workers."""

    def __init__(
        self,
        limits: AdmissionLimits | None = None,
        *,
        fleet: RedisFleetLimits | None = None,
    ) -> None:
        self.limits = limits or AdmissionLimits()
        self.fleet = fleet
        self.global_limit = anyio.Semaphore(self.limits.global_concurrency)
        self.expensive_limit = anyio.Semaphore(self.limits.expensive_concurrency)
        self._principal_states: dict[str, _PrincipalAdmission] = {}
        self._last_state_eviction = 0.0

    @classmethod
    def from_environment(cls) -> AdmissionController:
        url = admission_backend_url()
        return cls(
            AdmissionLimits.from_environment(),
            fleet=RedisFleetLimits.from_url(url) if url else None,
        )

    @property
    def scope(self) -> str:
        """``fleet`` when per-principal limits are shared, else ``process``."""
        return "fleet" if self.fleet is not None else "process"

    # -- process-local principal state -------------------------------------

    def admission_state(self, principal: str) -> _PrincipalAdmission:
        now = time.monotonic()
        state = self._principal_states.get(principal)
        if state is None:
            state = _PrincipalAdmission(
                window=_Window(),
                semaphore=anyio.Semaphore(self.limits.principal_concurrency),
                last_seen=now,
            )
            self._principal_states[principal] = state
        else:
            state.last_seen = now
        if (
            len(self._principal_states) > self.limits.principal_state_limit
            or now - self._last_state_eviction >= 60
        ):
            self._evict_principal_states(now, preserve=principal)
        return state

    def _evict_principal_states(self, now: float, *, preserve: str) -> None:
        limit = self.limits.principal_state_limit
        ttl = self.limits.principal_state_ttl
        idle = [
            (key, state)
            for key, state in self._principal_states.items()
            if key != preserve
            and state.semaphore.value == self.limits.principal_concurrency
            and state.semaphore.statistics().tasks_waiting == 0
            and (now - state.last_seen >= ttl or len(self._principal_states) > limit)
        ]
        idle.sort(key=lambda item: item[1].last_seen)
        target = min(
            len(idle),
            max(
                len(self._principal_states) - limit,
                sum(1 for _, state in idle if now - state.last_seen >= ttl),
            ),
        )
        for key, _ in idle[:target]:
            self._principal_states.pop(key, None)
        self._last_state_eviction = now

    # -- admission ----------------------------------------------------------

    async def check_rate(self, principal: str) -> None:
        """Charge one request against the principal's rate; raise when exceeded."""
        if self.fleet is not None:
            retry_after = await self.fleet.consume_rate(principal, self.limits.rate_limit_per_minute)
        else:
            retry_after = await self.admission_state(principal).window.consume(
                self.limits.rate_limit_per_minute, 60.0
            )
        if retry_after is not None:
            raise AdmissionError(
                "rate_limited", "Per-principal request rate exceeded.", retry_after=retry_after
            )

    @asynccontextmanager
    async def _principal_slot(self, principal: str, deadline: float) -> AsyncIterator[None]:
        if self.fleet is None:
            async with self.admission_state(principal).semaphore:
                yield
            return
        lease = secrets.token_hex(12)
        ttl = deadline + _LEASE_GRACE_SECONDS
        waited_until = time.monotonic() + self.limits.principal_queue_seconds
        while not await self.fleet.try_lease(principal, self.limits.principal_concurrency, lease, ttl):
            if time.monotonic() >= waited_until:
                raise AdmissionError(
                    "principal_concurrency_exceeded",
                    "Too many concurrent Workspace calls for this principal across the fleet.",
                    retry_after=1,
                )
            await anyio.sleep(_LEASE_POLL_SECONDS)
        try:
            yield
        finally:
            with anyio.CancelScope(shield=True):
                await self.fleet.release_lease(principal, lease)

    @asynccontextmanager
    async def execution_slot(
        self,
        principal: str,
        tool: str,
        *,
        kind: ExecutionKind,
        counters: ActivityCounters,
    ) -> AsyncIterator[float]:
        """Hold process and principal concurrency for one execution.

        Yields the time spent queueing, in milliseconds. ``counters`` tracks the
        execution for drain accounting until the slot is released.
        """
        cost = tool_cost(tool)
        deadline = self.limits.deadline_for(cost)
        started = time.perf_counter()
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(self.global_limit)
            await stack.enter_async_context(self._principal_slot(principal, deadline))
            await stack.enter_async_context(
                self.expensive_limit if cost == "expensive" else _NullAsyncContext()
            )
            queue_ms = (time.perf_counter() - started) * 1_000
            if kind == "task":
                counters.active_tasks += 1
            else:
                counters.active_requests += 1
            try:
                yield queue_ms
            finally:
                if kind == "task":
                    counters.active_tasks -= 1
                else:
                    counters.active_requests -= 1


__all__ = [
    "AdmissionController",
    "AdmissionError",
    "AdmissionLimits",
    "RedisFleetLimits",
    "ActivityCounters",
    "admission_backend_url",
    "tool_cost",
]
