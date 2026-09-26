"""Single bootstrap for the MCP Tasks extension (``io.modelcontextprotocol/tasks``).

FastMCP 4 moved background tasks out of the core server into the
``fastmcp-tasks`` package. A ``task=True`` tool only runs as a task when a
``TasksExtension`` is registered on the server that serves the wire, and a
server that declares task tools without one refuses to start. Every runnable
composition (the root ``workspace_mcp`` served over HTTP or stdio, and an
out-of-process ``fastmcp_tasks.worker_cli`` worker that imports it) therefore
goes through :func:`install_tasks_extension` so the queue configuration is
decided in exactly one place.

Queue selection, highest precedence first:

1. ``FASTMCP_DOCKET_URL`` (environment or the FastMCP ``.env`` file) — the
   framework's own documented override, honored unchanged from FastMCP 3.
2. ``MCP_REDIS_URL`` — the application's shared Redis, except in the local
   stdio bundle (``MCP_RUNTIME_MODE=bundle``). The bundle never joined a shared
   queue under FastMCP 3 and must not start consuming a remote fleet's tasks
   merely because the variable is present in the desktop environment.
3. ``memory://`` — the in-process backend used by stdio and tests; it needs no
   Redis and is single-process only.

The queue name defaults to :data:`DEFAULT_TASK_QUEUE_NAME` unless
``FASTMCP_DOCKET_NAME`` is set. It intentionally differs from FastMCP's generic
``"fastmcp"`` default so FastMCP 4 workers never consume a FastMCP 3 queue that
shares the same Redis during a rollout. Worker concurrency and timing use the
documented ``FASTMCP_DOCKET_*`` settings.

``FASTMCP_TASKS_ENCRYPTION_KEY`` is read by ``fastmcp-tasks`` itself and
encrypts the caller-context snapshot (access token and HTTP headers) that is
written to the queue. It does not encrypt tool arguments or results. Every
server and worker sharing a queue must use the same value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
import os
from typing import Any

from fastmcp_tasks import TasksExtension
from fastmcp_tasks.settings import DocketSettings, TasksSettings

LOGGER = logging.getLogger("mcp_google_workspace.tasks")

TASKS_EXTENSION_ID = TasksExtension.identifier
DEFAULT_TASK_QUEUE_NAME = "mcp-google-workspace"
MEMORY_BACKEND_URL = "memory://"


@dataclass(frozen=True, slots=True)
class TaskBackendConfig:
    """Resolved queue configuration for the Tasks extension."""

    url: str
    name: str
    url_source: str
    snapshot_encryption: bool

    @property
    def distributed(self) -> bool:
        return not self.url.startswith(MEMORY_BACKEND_URL)

    def diagnostics(self) -> dict[str, Any]:
        """Secret-free description suitable for logs and diagnostics."""
        return {
            "backend": "redis" if self.distributed else "memory",
            "queue_name": self.name,
            "url_source": self.url_source,
            "snapshot_encryption": self.snapshot_encryption,
        }


def resolve_task_backend_config(
    environ: Mapping[str, str] | None = None,
) -> TaskBackendConfig:
    """Resolve the Tasks queue from framework and application settings."""
    env = os.environ if environ is None else environ
    framework = DocketSettings()
    explicit = framework.model_fields_set

    if "url" in explicit:
        url, source = framework.url, "FASTMCP_DOCKET_URL"
    else:
        redis_url = env.get("MCP_REDIS_URL", "").strip()
        bundle = env.get("MCP_RUNTIME_MODE", "").strip().lower() == "bundle"
        if redis_url and not bundle:
            url, source = redis_url, "MCP_REDIS_URL"
        else:
            url, source = MEMORY_BACKEND_URL, "default"

    name = framework.name if "name" in explicit else DEFAULT_TASK_QUEUE_NAME
    return TaskBackendConfig(
        url=url,
        name=name,
        url_source=source,
        snapshot_encryption=TasksSettings().encryption_key is not None,
    )


def build_tasks_extension(config: TaskBackendConfig | None = None) -> TasksExtension:
    """Construct the Tasks extension for one runnable server composition."""
    resolved = config or resolve_task_backend_config()
    if resolved.distributed and not resolved.snapshot_encryption:
        LOGGER.warning(
            "Background tasks use a shared queue without FASTMCP_TASKS_ENCRYPTION_KEY; "
            "caller credential snapshots are stored unencrypted in the task backend."
        )
    # Concurrency, worker name, and timing intentionally fall through to the
    # documented FASTMCP_DOCKET_* environment defaults.
    return TasksExtension(url=resolved.url, name=resolved.name)


def install_tasks_extension(
    server: Any, config: TaskBackendConfig | None = None
) -> TasksExtension:
    """Register the Tasks extension on *server* once and return it.

    Registration must happen before the server's lifespan starts. A server that
    already has a tasks extension keeps it, which makes repeated imports and
    test fixtures idempotent.
    """
    existing = server_tasks_extension(server)
    if existing is not None:
        return existing
    extension = build_tasks_extension(config)
    server.add_extension(extension)
    return extension


def server_tasks_extension(server: Any) -> TasksExtension | None:
    """Return the Tasks extension registered on *server*, if any."""
    from .fastmcp_compat import registered_extensions

    extension = registered_extensions(server).get(TASKS_EXTENSION_ID)
    return extension if isinstance(extension, TasksExtension) else None
