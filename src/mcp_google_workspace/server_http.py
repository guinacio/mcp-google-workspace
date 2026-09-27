"""Authenticated Streamable HTTP entrypoint for mcp-google-workspace.

HTTP policy (applied identically by ``main`` and by :func:`build_http_app`):

* **Modern (2026-07-28) requests** are self-contained POSTs; no session, no
  affinity. The response is ``application/json`` unless the tool emits a
  request-scoped notification (progress) or runs past the SDK's keepalive
  window, in which case the same response becomes a request-scoped SSE stream
  (``MCP_HTTP_RESPONSE_MODE=sse``, the default). Closing that stream cancels
  the tool. No long-lived stream exists: ``subscriptions/listen`` is not
  advertised (``listChanged: false``) and answers ``-32601``.
  ``MCP_HTTP_RESPONSE_MODE=json`` forces JSON bodies (no progress, and a
  disconnect no longer cancels the tool) for intermediaries that cannot pass
  SSE.
* **Legacy (handshake-era) clients** keep FastMCP's stateful compatibility
  transport (``Mcp-Session-Id``, GET stream); behind several replicas they need
  load-balancer affinity. A hash of that header cannot provide it (initialize
  has none yet); ``docs/DEPLOYMENT_FLEET.md`` has the qualified proxy rule.
* ``MCP-Protocol-Version`` / ``Mcp-Method`` / ``Mcp-Name`` are validated by the
  SDK before dispatch (``-32020`` / ``-32022``, HTTP 400).
* Host and Origin are always validated (strict mode): the default bind is
  loopback, the allowed host is ``MCP_HTTP_BASE_URL``'s authority unless
  ``MCP_ALLOWED_HOSTS`` is set, and browser origins default to that base URL
  (``MCP_ALLOWED_ORIGINS``). Requests without ``Origin`` (non-browser clients)
  are allowed.
* Request bodies are bounded while streaming (``MCP_MAX_REQUEST_BYTES``).
* Bearer tokens are verified against the configured issuer/audience/JWKS and
  protected-resource metadata is served for discovery (``auth.remote_auth``).
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from typing import Any, Literal
from urllib.parse import urlparse

from fastmcp import FastMCP
from starlette.middleware import Middleware as ASGIMiddleware

from .auth.google_oauth import register_oauth_callback_route
from .auth.remote_auth import MCP_PATH, build_remote_auth
from .common.confirmation import REQUEST_STATE_KEYS_ENV, shared_request_state_keys_configured
from .common.deployment_guard import reject_test_only_settings
from .common.production import (
    RequestSizeLimitMiddleware,
    shutdown_grace_seconds,
    validate_operation_lease,
)
from .runtime import RemoteSecuritySettings, configure_logging, get_remote_security_settings
from .server import workspace_mcp
from .tool_discovery import configure_tool_search

LOGGER = logging.getLogger("mcp_google_workspace.http")

DEFAULT_MAX_REQUEST_BYTES = 30 * 1024 * 1024
ResponseMode = Literal["sse", "json"]


def configure_remote_tool_search() -> None:
    """Backward-compatible entrypoint for HTTP progressive discovery."""
    configure_tool_search(workspace_mcp)


def build_http_auth(settings: RemoteSecuritySettings | None = None) -> Any:
    """``RemoteAuthProvider`` around the JWT verifier (protected-resource discovery)."""
    return build_remote_auth(settings or get_remote_security_settings())


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True, slots=True)
class HttpServingPolicy:
    allowed_hosts: list[str]
    allowed_origins: list[str]
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    response_mode: ResponseMode = "sse"
    path: str = MCP_PATH

    @classmethod
    def from_environment(cls, base_url: str) -> HttpServingPolicy:
        parsed = urlparse(base_url)
        hosts = _csv(os.getenv("MCP_ALLOWED_HOSTS", "")) or [parsed.netloc]
        # An Origin never carries a path: a reverse-proxy prefix in the base URL
        # must not end up in the default allowed origin.
        origins = _csv(os.getenv("MCP_ALLOWED_ORIGINS", "")) or [f"{parsed.scheme}://{parsed.netloc}"]
        max_bytes = int(os.getenv("MCP_MAX_REQUEST_BYTES", str(DEFAULT_MAX_REQUEST_BYTES)))
        if max_bytes < 1:
            raise ValueError("MCP_MAX_REQUEST_BYTES must be positive.")
        mode = os.getenv("MCP_HTTP_RESPONSE_MODE", "sse").strip().lower() or "sse"
        if mode not in {"sse", "json"}:
            raise ValueError("MCP_HTTP_RESPONSE_MODE must be sse or json.")
        return cls(
            allowed_hosts=hosts,
            allowed_origins=origins,
            max_request_bytes=max_bytes,
            response_mode="json" if mode == "json" else "sse",
        )

    def app_options(self) -> dict[str, Any]:
        """Keyword arguments for ``FastMCP.http_app`` / ``FastMCP.run``."""
        return {
            "path": self.path,
            # Legacy clients keep their stateful compatibility sessions; modern
            # requests never create or need one.
            "stateless_http": False,
            "json_response": self.response_mode == "json",
            "host_origin_protection": True,
            "allowed_hosts": list(self.allowed_hosts),
            "allowed_origins": list(self.allowed_origins),
            "middleware": [
                ASGIMiddleware(RequestSizeLimitMiddleware, max_bytes=self.max_request_bytes)
            ],
        }


def build_http_app(server: FastMCP, policy: HttpServingPolicy) -> Any:
    """The production Starlette app for *server* (auth must already be set)."""
    return server.http_app(transport="http", **policy.app_options())


def uvicorn_options() -> dict[str, Any]:
    """uvicorn settings for :func:`main`.

    On SIGTERM uvicorn stops accepting connections at once (the replica drops
    out of the load balancer), then waits for in-flight requests. FastMCP's
    ``run`` defaults that wait to 2 s and then cancels whatever is still
    running, so a normal tool call was cut off mid-flight on every rolling
    restart (found by the W7a fleet drain test). The wait is the configured
    drain window instead.
    """
    return {"timeout_graceful_shutdown": shutdown_grace_seconds()}


def main() -> None:
    reject_test_only_settings()
    configure_logging()
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8000"))
    configure_remote_tool_search()
    if not shared_request_state_keys_configured():
        LOGGER.warning(
            "%s is not set: confirmation continuations are sealed with an ephemeral "
            "per-process key and will not resume on another replica or after a restart.",
            REQUEST_STATE_KEYS_ENV,
        )
    # The Tasks queue (MCP_REDIS_URL / FASTMCP_DOCKET_URL) is configured once by
    # install_tasks_extension() when server.py composes workspace_mcp.
    # Deadlines must stay below the operation lease (W4b), or a second request
    # could re-run an operation whose first execution is still in flight.
    validate_operation_lease()
    security = get_remote_security_settings()
    workspace_mcp.auth = build_http_auth(security)
    register_oauth_callback_route(workspace_mcp)
    policy = HttpServingPolicy.from_environment(security.base_url)
    workspace_mcp.run(
        transport="http",
        host=host,
        port=port,
        uvicorn_config=uvicorn_options(),
        **policy.app_options(),
    )


if __name__ == "__main__":
    main()
