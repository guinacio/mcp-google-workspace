"""Bundle-aware stdio entrypoint for MCPB hosts."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

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
        workspace_mcp.run(transport="stdio")
    except KeyboardInterrupt:
        LOGGER.info("Received keyboard interrupt, shutting down MCP server.")
    except Exception:
        LOGGER.exception("Fatal error while running google-workspace-mcp.")
        raise


if __name__ == "__main__":
    main()
