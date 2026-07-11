"""Bundle-aware stdio entrypoint for MCPB hosts."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# A desktop bundle is an offline, host-managed stdio child process. Disable
# framework update checks before FastMCP is imported: they add network/cache I/O
# to startup and can leave Claude waiting on a process that never establishes MCP.
os.environ.setdefault("FASTMCP_CHECK_FOR_UPDATES", "off")
os.environ.setdefault("MCP_RUNTIME_MODE", "bundle")

if __package__ in {None, ""}:
    script_dir = Path(__file__).resolve().parent
    src_dir = script_dir.parent
    if sys.path and Path(sys.path[0]).resolve() == script_dir:
        sys.path.pop(0)
    sys.path.insert(0, str(src_dir))
    from mcp_google_workspace.runtime import configure_logging
    from mcp_google_workspace.server import workspace_mcp
else:
    from .runtime import configure_logging
    from .server import workspace_mcp


LOGGER = logging.getLogger("mcp_google_workspace.bundle")


def main() -> None:
    try:
        settings = configure_logging()
    except ValueError as exc:
        print(
            f"Invalid MCP Google Workspace runtime configuration: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    LOGGER.info(
        "Starting google-workspace-mcp over stdio (timeout=%ss, retries=%s).",
        settings.http_timeout_seconds,
        settings.http_retries,
    )

    try:
        workspace_mcp.run(transport="stdio", show_banner=False)
    except KeyboardInterrupt:
        LOGGER.info("Received keyboard interrupt, shutting down MCP server.")
    except Exception:
        LOGGER.exception("Fatal error while running google-workspace-mcp.")
        raise


if __name__ == "__main__":
    main()
