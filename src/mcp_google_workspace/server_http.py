"""Authenticated Streamable HTTP entrypoint for mcp-google-workspace."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from fastmcp.server.auth.providers.jwt import JWTVerifier
import fastmcp
from starlette.middleware import Middleware as ASGIMiddleware

from .auth.google_oauth import register_oauth_callback_route
from .common.production import RequestSizeLimitMiddleware
from .runtime import configure_logging, get_remote_security_settings
from .server import workspace_mcp
from .tool_discovery import configure_tool_search


def configure_remote_tool_search() -> None:
    """Backward-compatible entrypoint for HTTP progressive discovery."""
    configure_tool_search(workspace_mcp)


def build_http_auth() -> JWTVerifier:
    settings = get_remote_security_settings()
    return JWTVerifier(
        jwks_uri=settings.jwt_jwks_uri,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        base_url=settings.base_url,
    )


def main() -> None:
    configure_logging()
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8000"))
    configure_remote_tool_search()
    redis_url = os.getenv("MCP_REDIS_URL", "").strip()
    if redis_url and not os.getenv("FASTMCP_DOCKET_URL", "").strip():
        fastmcp.settings.docket.url = redis_url
    security = get_remote_security_settings()
    workspace_mcp.auth = build_http_auth()
    register_oauth_callback_route(workspace_mcp)
    configured_hosts = [
        value.strip() for value in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if value.strip()
    ]
    allowed_hosts = configured_hosts or [urlparse(security.base_url).netloc]
    allowed_origins = [
        value.strip()
        for value in os.getenv("MCP_ALLOWED_ORIGINS", security.base_url).split(",")
        if value.strip()
    ]
    max_request_bytes = int(os.getenv("MCP_MAX_REQUEST_BYTES", str(30 * 1024 * 1024)))
    workspace_mcp.run(
        transport="http",
        host=host,
        port=port,
        stateless_http=False,
        json_response=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
        middleware=[ASGIMiddleware(RequestSizeLimitMiddleware, max_bytes=max_request_bytes)],
    )


if __name__ == "__main__":
    main()
