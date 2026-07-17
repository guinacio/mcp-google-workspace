"""Shared helper functions for Gmail tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar, cast

import anyio

from .schemas import RecipientSet

_S = TypeVar("_S")
_T = TypeVar("_T")

# Cap concurrent per-message fetches so a large heartbeat/digest cannot open a
# burst of connections against the Gmail API.
DEFAULT_FETCH_CONCURRENCY = 8


async def gather_in_order(
    items: Sequence[_S],
    worker: Callable[[_S], Awaitable[_T]],
    *,
    limit: int = DEFAULT_FETCH_CONCURRENCY,
) -> list[_T]:
    """Run *worker* over *items* with bounded concurrency, preserving input order.

    Uses an anyio task group (the repo's concurrency idiom) plus a
    ``CapacityLimiter`` so at most *limit* workers run at once. Results are
    written back into their original positions, so the returned list matches the
    order of *items* regardless of completion order.
    """
    if not items:
        return []
    results: list[_T | None] = [None] * len(items)
    limiter = anyio.CapacityLimiter(limit)

    async def run(index: int, item: _S) -> None:
        async with limiter:
            results[index] = await worker(item)

    try:
        async with anyio.create_task_group() as task_group:
            for index, item in enumerate(items):
                task_group.start_soon(run, index, item)
    except BaseExceptionGroup as group:
        # Surface a lone worker failure as itself so the error middleware can
        # keep mapping exception types (reauth, recoverable) to envelopes.
        if len(group.exceptions) == 1 and not isinstance(
            group.exceptions[0], BaseExceptionGroup
        ):
            raise group.exceptions[0]
        raise
    return cast("list[_T]", results)


def recipient_set(
    *,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> RecipientSet:
    return RecipientSet(
        to=to or [],
        cc=cc or [],
        bcc=bcc or [],
    )
