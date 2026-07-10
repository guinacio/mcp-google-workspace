"""SSE entrypoint for mcp-google-workspace."""

from __future__ import annotations

import os

from fastmcp.server.auth.providers.jwt import JWTVerifier

from .auth.google_oauth import register_oauth_callback_route
from .runtime import configure_logging, get_sse_security_settings
from .server import workspace_mcp


def build_sse_auth() -> JWTVerifier:
    """Create the mandatory OIDC verifier for the multi-user SSE surface."""
    settings = get_sse_security_settings()
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
    workspace_mcp.auth = build_sse_auth()
    register_oauth_callback_route(workspace_mcp)
    workspace_mcp.run(transport="sse", host=host, port=port)


if __name__ == "__main__":
    main()
