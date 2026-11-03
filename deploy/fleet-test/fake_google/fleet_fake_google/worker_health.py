"""Worker container healthcheck (TEST ONLY): this host's Docket worker is
registered and heartbeating on the shared queue."""

from __future__ import annotations

import asyncio
import os
import socket

from docket import Docket


async def _registered() -> bool:
    url = os.environ.get("FASTMCP_DOCKET_URL") or os.environ["MCP_REDIS_URL"]
    name = os.environ.get("FASTMCP_DOCKET_NAME", "mcp-google-workspace")
    prefix = f"{socket.gethostname()}#"
    async with Docket(name=name, url=url) as docket:
        return any(worker.name.startswith(prefix) for worker in await docket.workers())


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(_registered()) else 1)
