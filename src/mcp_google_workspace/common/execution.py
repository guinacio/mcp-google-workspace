"""Execution guard installed around every tool body.

It is the one service-layer seam every execution path shares — the root
server, a directly served subserver, a nested call (``commit_workspace_action``,
the ``call_tool`` search proxy) and, crucially, a background task executed by a
Tasks-extension worker, which calls the raw tool function without any FastMCP
middleware.

On every path it converts exceptions raised by the tool body into the stable
error envelope (``common.errors``): tool execution failures become ``isError``
results; protocol rejections and unexpected faults are re-raised.

On the background-task path (a Docket worker execution) it additionally
enforces, at *execution* time rather than only at submission:

* **Caller restored.** In remote mode the submitting caller's bearer token must
  have been restored from the task snapshot (it is not when the snapshot is
  missing or the token expired while queued). Otherwise the task fails with a
  protocol error; it never runs as the local principal or anonymously.
* **Principal not revoked** (``MCP_REVOKED_PRINCIPALS`` / Redis set).
* **Google capability still granted**, read fresh from the token store.
* **Admission**: the process and per-principal concurrency slots shared with
  foreground requests, counted in drain accounting (``active_tasks``).
* **Deadline** over the task's runtime (``MCP_TOOL_DEADLINE_SECONDS`` /
  ``MCP_EXPENSIVE_DEADLINE_SECONDS``), not only its submission.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import wraps
from hashlib import sha256
import inspect
import logging
import time
from typing import Any
from uuid import uuid4

import anyio
from fastmcp.exceptions import McpError

from .errors import (
    RPC_UNAUTHORIZED,
    ProtocolRejection,
    RecoverableToolError,
    classify_error,
    error_tool_result,
    protocol_error,
)

LOGGER = logging.getLogger("mcp_google_workspace.execution")

_GUARD_MARKER = "_workspace_execution_guard"


def background_task_id() -> str | None:
    """The MCP task ID when running inside a Tasks-extension worker execution."""
    try:
        from fastmcp_tasks.context import get_task_context
    except ImportError:  # pragma: no cover - tasks extra always installed
        return None
    info = get_task_context()
    return info.task_id if info is not None else None


def qualified_tool_name(name: str, namespace: str | None) -> str:
    if namespace and not name.startswith(f"{namespace}_"):
        return f"{namespace}_{name}"
    return name


def _convert(error: Exception) -> Any:
    """Tool execution error result for *error*; re-raise protocol-level errors."""
    rpc_code, envelope = classify_error(error)
    if rpc_code is None:
        return error_tool_result(envelope)
    if envelope.get("code") == "internal_error":
        LOGGER.exception("Unhandled tool exception (%s): %s", type(error).__name__, error)
    raise protocol_error(rpc_code, envelope) from error


def _task_caller_unavailable() -> ProtocolRejection:
    return ProtocolRejection(
        "task_caller_unavailable",
        (
            "The background task's submitting caller could not be restored (its bearer "
            "token is missing or expired). Nothing was executed; submit the task again."
        ),
        rpc_code=RPC_UNAUTHORIZED,
        required_action={"action": "resubmit_with_valid_token"},
    )


@asynccontextmanager
async def task_execution_scope(tool: str) -> AsyncIterator[None]:
    """Authorization, admission, deadline and accounting for one task execution."""
    from fastmcp.server.dependencies import get_access_token

    from ..auth.grants import is_tool_granted, read_grant_async, required_capability
    from ..auth.identity import current_principal, remote_identity_required
    from .admission import tool_cost
    from .production import (
        ACTIVE_TASKS,
        RUNTIME_STATE,
        admission_controller,
        missing_capability_error,
        principal_revoked,
        principal_revoked_rejection,
        record_execution,
    )

    token = get_access_token()
    if token is None and remote_identity_required():
        raise _task_caller_unavailable()
    try:
        principal = current_principal()
    except PermissionError as exc:
        raise _task_caller_unavailable() from exc
    principal_key = principal.storage_key
    if await principal_revoked(principal_key):
        raise principal_revoked_rejection()
    capability = required_capability(tool)
    if token is not None and capability is not None:
        if not is_tool_granted(tool, await read_grant_async(principal)):
            raise missing_capability_error(tool, capability)

    controller = admission_controller()
    from .operations import lease_seconds

    # A task's run deadline never outlives the W4b operation lease (startup
    # and readiness validate this; the clamp covers a misconfigured worker).
    deadline = min(controller.limits.deadline_for(tool_cost(tool)), max(1, lease_seconds() - 1))
    correlation_id = uuid4().hex
    started = time.perf_counter()
    queue_ms: float | None = None
    outcome = "error"
    try:
        async with controller.execution_slot(
            principal_key, tool, kind="task", counters=RUNTIME_STATE
        ) as queued:
            queue_ms = queued
            ACTIVE_TASKS.inc()
            try:
                with anyio.fail_after(deadline):
                    yield
                outcome = "ok"
            finally:
                ACTIVE_TASKS.dec()
    except TimeoutError as exc:
        outcome = "timeout"
        raise RecoverableToolError(
            "deadline_exceeded",
            f"Background task exceeded its {deadline}s deadline; it may or may not have completed.",
            required_action={"action": "verify_outcome_before_retry"},
        ) from exc
    finally:
        record_execution(
            tool=tool,
            outcome=outcome,
            started=started,
            queue_ms=queue_ms,
            principal_hash=sha256(principal_key.encode()).hexdigest()[:16],
            correlation_id=correlation_id,
            kind="task",
            continuation=False,
        )


async def run_task_delivery(tool: str, task_id: str, call: Callable[[], Awaitable[Any]]) -> Any:
    """Run one Docket delivery of a background task at most once (W7a).

    Docket redelivers a task whose worker stopped mid-execution (crash,
    SIGKILL, lost Redis), and the redelivered body would repeat Google calls
    that are not safe to repeat (a batchUpdate twice). The delivery is
    therefore claimed in the W4b operation store under the task id: a second
    delivery of a task whose first run started returns ``outcome_unknown``
    (with verification guidance) instead of executing again, and one whose
    first run finished replays the saved result. The claim is settled like a
    confirmed call: released when nothing non-repeatable reached Google,
    ``failed`` on a definitive rejection, ``outcome_unknown`` when a
    non-repeatable call may have been applied (deadline, cancellation).
    """
    from .operations import (
        current_principal_key,
        get_operation_store,
        operation_key,
        operation_ref,
        resolve_claim,
        settle_after_failure,
        settle_after_return,
    )
    from .repeat_safety import tracking_scope

    operation_id = f"task:{task_id}"
    key = operation_key(current_principal_key(), operation_id)
    ref = operation_ref(operation_id)
    store = get_operation_store()
    outcome = await store.claim_task_delivery(key, tool=tool, ref=ref)
    if outcome.status == "missing":  # the record expired between two steps
        outcome = await store.claim_task_delivery(key, tool=tool, ref=ref)
    if outcome.status != "claimed" or outcome.handle is None:
        LOGGER.warning("task delivery not re-executed tool=%s ref=%s status=%s", tool, ref, outcome.status)
        return await resolve_claim(outcome, tool=tool, ref=ref)
    with tracking_scope() as tracker:
        tracker.operation = outcome.handle
        try:
            result = await call()
        except BaseException as exc:
            replacement = await settle_after_failure(tracker, exc, tool=tool)
            if replacement is not None and isinstance(exc, Exception):
                raise replacement from exc
            raise
        replacement = await settle_after_return(tracker, result, tool=tool)
        if replacement is not None:
            raise replacement
        return result


def install_execution_guard(component: Any, *, namespace: str | None) -> None:
    """Wrap ``component.fn`` with the execution guard (idempotent).

    Must be the outermost wrapper (outside the confirmation guard), so that a
    multi-round-trip question passes through as a normal return value. The
    signature is preserved (``functools.wraps``) for FastMCP and Docket
    dependency injection.
    """
    original: Callable[..., Any] = component.fn
    if getattr(original, _GUARD_MARKER, False):
        return
    tool = qualified_tool_name(component.name, namespace)

    if inspect.iscoroutinefunction(original):

        @wraps(original)
        async def guarded(*args: Any, **kwargs: Any) -> Any:
            try:
                task_id = background_task_id()
                if task_id is None:
                    return await original(*args, **kwargs)
                async with task_execution_scope(tool):
                    return await run_task_delivery(tool, task_id, lambda: original(*args, **kwargs))
            except McpError:
                raise
            except Exception as error:  # noqa: BLE001 - classified below
                return _convert(error)

        wrapped: Callable[..., Any] = guarded
    else:

        @wraps(original)
        def guarded_sync(*args: Any, **kwargs: Any) -> Any:
            try:
                return original(*args, **kwargs)
            except McpError:
                raise
            except Exception as error:  # noqa: BLE001 - classified below
                return _convert(error)

        wrapped = guarded_sync
    setattr(wrapped, _GUARD_MARKER, True)
    component.fn = wrapped


__all__ = [
    "background_task_id",
    "install_execution_guard",
    "qualified_tool_name",
    "run_task_delivery",
    "task_execution_scope",
]
