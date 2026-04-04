"""Async helpers for offloading blocking SDK and filesystem work."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import anyio

T = TypeVar("T")


async def run_blocking(
    func: Callable[..., T],
    /,
    *args: Any,
    **kwargs: Any,
) -> T:
    return await anyio.to_thread.run_sync(lambda: func(*args, **kwargs))


async def execute_google_request(request: Any) -> Any:
    return await run_blocking(request.execute)


async def read_text_file(path: Path, *, encoding: str = "utf-8") -> str:
    return await run_blocking(path.read_text, encoding=encoding)


async def read_bytes_file(path: Path) -> bytes:
    return await run_blocking(path.read_bytes)


async def write_bytes_file(path: Path, data: bytes) -> int:
    return await run_blocking(path.write_bytes, data)


async def unlink_file(path: Path, *, missing_ok: bool = False) -> None:
    await run_blocking(path.unlink, missing_ok=missing_ok)
