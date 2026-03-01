"""STDIO entrypoint for mcp-google-workspace."""

from __future__ import annotations

from .server import workspace_mcp


def main() -> None:
    workspace_mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
