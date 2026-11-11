"""Gateway-hosted entrypoint (Prefect Horizon / FastMCP Cloud).

Managed FastMCP hosts import a server object (``fastmcp run file.py:obj``)
and serve it behind their own authenticating gateway. They forward no
verifiable caller identity, so this mode serves ONE owner: the platform's
access control (Horizon "Authentication") is the only thing standing between
the public URL and that owner's Google account. Keep it enabled.

Configuration (environment variables on the host):

* ``MCP_HTTP_BASE_URL`` (required): the public origin, for example
  ``https://googleworkspace.fastmcp.app``. The Google OAuth redirect defaults
  to ``<base>/google/oauth/callback``; register that exact URL on the Google
  OAuth client (type "Web application").
* ``GOOGLE_OAUTH_CLIENT_JSON`` (or ``MCP_CREDENTIALS_DIR``): the Google OAuth
  client JSON downloaded from Google Cloud Console.
* ``MCP_TOKEN_ENCRYPTION_KEY`` (or ``MCP_TOKEN_ENCRYPTION_KEYS`` /
  ``MCP_SECRET_FILE``): Fernet key encrypting the stored Google grant.
* ``MCP_REDIS_URL`` (optional): keeps the grant across restarts and
  redeploys; without it the grant lives on the instance's local disk and the
  owner reconnects after a redeploy.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import MutableMapping
from pathlib import Path
import tempfile
from typing import Any

from .auth.identity import TRUSTED_GATEWAY_AUTH_ENV

LOGGER = logging.getLogger("mcp_google_workspace.hosted")

CLIENT_JSON_ENV = "GOOGLE_OAUTH_CLIENT_JSON"
CALLBACK_PATH = "/google/oauth/callback"


def _state_dir(environ: MutableMapping[str, str]) -> Path:
    raw = environ.get("MCP_STATE_DIR", "").strip()
    return Path(raw) if raw else Path(tempfile.gettempdir()) / "mcp-google-workspace"


def _write_client_json(raw: str, directory: Path) -> Path:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{CLIENT_JSON_ENV} is not valid JSON: {exc.msg}") from None
    if not isinstance(payload, dict) or not ({"web", "installed"} & payload.keys()):
        raise SystemExit(
            f"{CLIENT_JSON_ENV} must be the Google OAuth client JSON "
            "(a top-level 'web' or 'installed' object)."
        )
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "credentials.json"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return target


def prepare_gateway_environment(environ: MutableMapping[str, str] | None = None) -> None:
    """Derive the runtime configuration the composed server reads lazily."""
    env = os.environ if environ is None else environ
    env.setdefault(TRUSTED_GATEWAY_AUTH_ENV, "true")
    state_dir = _state_dir(env)

    client_json = env.get(CLIENT_JSON_ENV, "").strip()
    if client_json and not env.get("MCP_CREDENTIALS_DIR", "").strip():
        client_dir = state_dir / "client"
        _write_client_json(client_json, client_dir)
        env["MCP_CREDENTIALS_DIR"] = str(client_dir)

    if not env.get("MCP_USER_TOKEN_DIR", "").strip():
        env["MCP_USER_TOKEN_DIR"] = str(state_dir / "tokens")

    base_url = env.get("MCP_HTTP_BASE_URL", "").strip().rstrip("/")
    if base_url and not env.get("MCP_GOOGLE_OAUTH_REDIRECT_URL", "").strip():
        env["MCP_GOOGLE_OAUTH_REDIRECT_URL"] = f"{base_url}{CALLBACK_PATH}"


def build_gateway_hosted_server() -> Any:
    """Return the composed server configured for a managed FastMCP host."""
    from .common.deployment_guard import reject_test_only_settings

    reject_test_only_settings()
    prepare_gateway_environment()

    from .auth.google_auth import is_remote_oauth_mode
    from .auth.google_oauth import register_oauth_callback_route
    from .common.production import validate_operation_lease
    from .runtime import configure_logging, get_google_oauth_redirect_url
    from .server import workspace_mcp
    from .tool_discovery import configure_tool_search

    configure_logging()
    validate_operation_lease()
    configure_tool_search(workspace_mcp)
    if is_remote_oauth_mode():
        get_google_oauth_redirect_url()  # fail fast on a non-HTTPS redirect
        register_oauth_callback_route(workspace_mcp)
    else:
        LOGGER.warning(
            "MCP_HTTP_BASE_URL is not set: Google OAuth cannot complete on this host. "
            "Set it to the server's public origin (for example https://<name>.fastmcp.app)."
        )
    return workspace_mcp
