"""Out-of-process worker for MCP Tasks extension background tasks.

The worker serves exactly the root ``workspace_mcp`` composition and its
single Tasks extension, configured by the same ``install_tasks_extension``
factory as HTTP and stdio (``MCP_REDIS_URL`` / ``FASTMCP_DOCKET_*``); the
in-memory backend is rejected.

Run with a shared Redis queue::

    mcp-google-workspace-worker
    # equivalent upstream CLI (no graceful SIGTERM, prints the backend URL):
    # python -m fastmcp_tasks.worker_cli worker src/mcp_google_workspace/task_worker.py:workspace_mcp

It does what ``fastmcp_tasks.worker_cli worker`` does (enter the server
lifespan, which starts the Docket worker, and wait) with two differences the
fleet qualification (W7a) showed are needed in a container:

* **Graceful SIGTERM.** The upstream CLI only stops on ``KeyboardInterrupt``.
  As PID 1 in a container an unhandled SIGTERM is ignored, so every
  ``docker stop`` / pod termination waited for the grace period and then
  SIGKILLed the worker mid-task. Here SIGTERM/SIGINT leave the lifespan:
  Docket stops taking work and finishes the tasks already running (each is
  bounded by its tool deadline), then the process exits 0.
* **No secrets in logs.** The upstream banner prints the backend URL,
  including a Redis password; this one prints it redacted.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from mcp_google_workspace.server import workspace_mcp

__all__ = ["main", "redact_url", "serve", "workspace_mcp"]

LOGGER = logging.getLogger("mcp_google_workspace.task_worker")


def redact_url(url: str) -> str:
    """``url`` without its password (``rediss://user:***@host:6379/0``)."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    user = parts.username or ""
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{user}:***@{host}" + (f":{parts.port}" if parts.port is not None else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


async def serve(server: Any, stop: asyncio.Event | None = None) -> None:
    """Run *server*'s lifespan (and so its Docket worker) until *stop* or a signal."""
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop.set)
            installed.append(signum)
        except (NotImplementedError, RuntimeError, ValueError):  # pragma: no cover - Windows / non-main thread
            pass
    try:
        # Same private entry point the upstream worker CLI uses: it runs the
        # production lifespan and the Tasks extension's Docket worker.
        async with server._lifespan_manager():
            LOGGER.info("Task worker started for %s.", server.name)
            await stop.wait()
            LOGGER.info("Task worker stopping: finishing tasks already running.")
    finally:
        for signum in installed:
            loop.remove_signal_handler(signum)
    LOGGER.info("Task worker stopped.")


def main() -> None:
    from fastmcp_tasks.worker_cli import check_distributed_backend, resolve_docket_settings

    from mcp_google_workspace.common.deployment_guard import reject_test_only_settings
    from mcp_google_workspace.common.production import validate_operation_lease
    from mcp_google_workspace.runtime import configure_logging

    reject_test_only_settings()
    configure_logging()
    # Task executions are bounded by the tool deadlines; they must stay below
    # the W4b operation lease (fail fast, like the HTTP entrypoint).
    validate_operation_lease()
    settings = resolve_docket_settings(workspace_mcp)
    check_distributed_backend(settings)
    LOGGER.info(
        "Task worker: queue=%s backend=%s concurrency=%s",
        settings.name,
        redact_url(settings.url),
        settings.concurrency,
    )
    asyncio.run(serve(workspace_mcp))


if __name__ == "__main__":
    main()
