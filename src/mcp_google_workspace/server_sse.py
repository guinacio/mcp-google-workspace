"""SSE entrypoint for mcp-google-workspace."""

from __future__ import annotations

import os

from .server import workspace_mcp


def main() -> None:
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8000"))
    workspace_mcp.run(transport="sse", host=host, port=port)


if __name__ == "__main__":
    main()
