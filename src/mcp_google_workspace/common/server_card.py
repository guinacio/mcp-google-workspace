"""MCP Server Card (SEP-2127, experimental extension ``ext-server-card`` v1).

A public, read-only description of this server for discovery: identity,
links and the Streamable HTTP endpoint with its protocol versions. It holds no
tools, user data or secrets. Served at ``GET <mcp endpoint>/server-card``
(the path the extension reserves), with open CORS, ``Cache-Control`` and an
ETag so clients revalidate with ``If-None-Match``.

The card mirrors ``serverInfo`` (name aside: the card requires a reverse-DNS
``namespace/name``), so the two stay consistent as the extension asks.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Final

from mcp_types.version import SUPPORTED_PROTOCOL_VERSIONS
from starlette.requests import Request
from starlette.responses import Response

SCHEMA_URL: Final = "https://static.modelcontextprotocol.io/schemas/v1/server-card.schema.json"
MEDIA_TYPE: Final = "application/mcp-server-card+json"
CARD_NAME: Final = "io.github.guinacio/mcp-google-workspace"
# The card's description is capped at 100 characters (serverInfo's is longer).
CARD_DESCRIPTION: Final = "Gmail, Calendar, Drive, Docs, Sheets, Slides, Forms, Tasks and Contacts for AI agents."
CARD_PATH_SUFFIX: Final = "/server-card"

_HEADERS: Final = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET",
    "Access-Control-Allow-Headers": "Content-Type, If-None-Match",
    "Access-Control-Expose-Headers": "ETag",
    "Cache-Control": "public, max-age=3600",
}


def build_server_card(
    *,
    endpoint: str,
    version: str,
    title: str,
    website_url: str,
    icons: list[dict[str, Any]],
) -> dict[str, Any]:
    """The card for the MCP endpoint ``endpoint`` (an absolute http(s) URL)."""
    return {
        "$schema": SCHEMA_URL,
        "name": CARD_NAME,
        "version": version,
        "title": title,
        "description": CARD_DESCRIPTION,
        "websiteUrl": website_url,
        "repository": {"url": website_url, "source": "github"},
        "icons": icons,
        "remotes": [
            {
                "type": "streamable-http",
                "url": endpoint,
                "supportedProtocolVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            }
        ],
    }


def _endpoint(request: Request, mcp_path: str) -> str:
    base = os.getenv("MCP_HTTP_BASE_URL", "").strip().rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
    return f"{base}{mcp_path}"


def register_server_card_route(server: Any, *, mcp_path: str = "/mcp", **identity: Any) -> None:
    """Serve the card at ``<mcp_path>/server-card`` on ``server`` (custom routes are unauthenticated)."""

    @server.custom_route(f"{mcp_path}{CARD_PATH_SUFFIX}", methods=["GET"], include_in_schema=False)
    async def server_card(request: Request) -> Response:
        card = build_server_card(endpoint=_endpoint(request, mcp_path), **identity)
        body = json.dumps(card, separators=(",", ":"), sort_keys=True).encode("utf-8")
        etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
        headers = {**_HEADERS, "ETag": etag}
        if etag in {tag.strip() for tag in request.headers.get("if-none-match", "").split(",")}:
            return Response(status_code=304, headers=headers)
        return Response(body, media_type=MEDIA_TYPE, headers=headers)
