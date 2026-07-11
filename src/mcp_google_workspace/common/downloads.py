"""Bounded, atomic streaming for Google media downloads."""

from __future__ import annotations

import os
from pathlib import Path
from secrets import token_hex
from typing import Any

from fastmcp import Context
from googleapiclient.http import MediaIoBaseDownload

from ..auth.google_auth import materialize_google_request
from .async_ops import run_blocking


def max_download_bytes() -> int:
    raw = os.getenv("MCP_MAX_DOWNLOAD_BYTES", str(250 * 1024 * 1024))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("MCP_MAX_DOWNLOAD_BYTES must be an integer.") from exc
    if value < 1 or value > 10 * 1024 * 1024 * 1024:
        raise ValueError("MCP_MAX_DOWNLOAD_BYTES must be between 1 byte and 10 GiB.")
    return value


async def stream_google_download(
    request: Any,
    output_path: Path,
    *,
    overwrite: bool,
    progress_label: str,
    ctx: Context | None = None,
) -> int:
    """Stream a request to a same-directory temporary file and publish atomically."""
    if await run_blocking(output_path.exists) and not overwrite:
        raise FileExistsError(f"Output path already exists: {output_path}")
    await run_blocking(output_path.parent.mkdir, parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{token_hex(8)}.part")
    materialized = await run_blocking(materialize_google_request, request)
    handle = await run_blocking(temporary.open, "xb")
    max_bytes = max_download_bytes()
    try:
        downloader = MediaIoBaseDownload(handle, materialized)
        done = False
        while not done:
            status, done = await run_blocking(downloader.next_chunk)
            current_size = await run_blocking(handle.tell)
            if current_size > max_bytes:
                raise ValueError(
                    f"Download exceeded MCP_MAX_DOWNLOAD_BYTES ({max_bytes} bytes)."
                )
            if status is not None and ctx is not None:
                await ctx.report_progress(
                    int(status.progress() * 100), 100, progress_label
                )
        await run_blocking(handle.flush)
        await run_blocking(os.fsync, handle.fileno())
    except BaseException:
        await run_blocking(handle.close)
        await run_blocking(temporary.unlink, missing_ok=True)
        raise
    await run_blocking(handle.close)
    size = await run_blocking(lambda: temporary.stat().st_size)
    if overwrite:
        await run_blocking(temporary.replace, output_path)
    else:
        try:
            await run_blocking(os.link, temporary, output_path)
        except FileExistsError:
            await run_blocking(temporary.unlink, missing_ok=True)
            raise FileExistsError(f"Output path already exists: {output_path}") from None
        await run_blocking(temporary.unlink, missing_ok=True)
    return size
