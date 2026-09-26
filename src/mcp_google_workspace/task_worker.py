"""Out-of-process worker for MCP Tasks extension background tasks.

FastMCP 4 removed the ``fastmcp tasks`` CLI subcommand; ``fastmcp-tasks``
ships ``fastmcp_tasks.worker_cli`` instead, which loads a server from a file
path. This module is that file-loadable target (absolute imports only), so
the worker serves exactly the root ``workspace_mcp`` composition and its
single Tasks extension, configured by the same ``install_tasks_extension``
factory as HTTP and stdio (``MCP_REDIS_URL`` / ``FASTMCP_DOCKET_*``).

Run with a shared Redis queue (the in-memory backend is rejected)::

    mcp-google-workspace-worker
    # or: python -m fastmcp_tasks.worker_cli worker src/mcp_google_workspace/task_worker.py:workspace_mcp
"""

from __future__ import annotations

from pathlib import Path

from mcp_google_workspace.server import workspace_mcp

__all__ = ["main", "workspace_mcp"]


def main() -> None:
    from fastmcp_tasks.worker_cli import tasks_app

    tasks_app(["worker", f"{Path(__file__).resolve()}:workspace_mcp"])


if __name__ == "__main__":
    main()
