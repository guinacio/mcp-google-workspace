"""Browser OAuth connection flow for one authenticated MCP principal at a time."""

from __future__ import annotations

import secrets
from typing import Any

from fastmcp import FastMCP
from google_auth_oauthlib.flow import Flow
from starlette.requests import Request
from starlette.responses import HTMLResponse

from ..runtime import get_sse_security_settings
from .google_auth import get_google_scopes, get_token_store, resolve_client_credentials_path
from .identity import current_principal

_REGISTERED_CALLBACK_SERVERS: set[int] = set()


def _flow(*, state: str | None = None, code_verifier: str | None = None) -> Flow:
    settings = get_sse_security_settings()
    credentials_path = resolve_client_credentials_path()
    if not credentials_path.exists():
        raise FileNotFoundError(
            "Google OAuth client credentials.json not found. Configure MCP_CREDENTIALS_DIR on the server."
        )
    flow = Flow.from_client_secrets_file(
        str(credentials_path),
        scopes=get_google_scopes(),
        state=state,
        redirect_uri=settings.google_oauth_redirect_url,
        autogenerate_code_verifier=False,
    )
    flow.code_verifier = code_verifier
    return flow


def create_google_authorization() -> dict[str, Any]:
    """Create a one-time, PKCE-protected Google consent URL for the MCP caller."""
    principal = current_principal()
    # RFC 7636 permits 43–128 characters. token_urlsafe(72) is about 96.
    code_verifier = secrets.token_urlsafe(72)
    pending = get_token_store().create_oauth_state(principal, code_verifier)
    flow = _flow(state=pending.state, code_verifier=code_verifier)
    authorization_url, returned_state = flow.authorization_url(
        state=pending.state,
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    if returned_state != pending.state:  # pragma: no cover - defensive provider invariant
        raise RuntimeError("Google OAuth returned an unexpected state value.")
    return {
        "authorization_url": authorization_url,
        "expires_at": pending.expires_at,
        "action": "Open authorization_url in a browser and complete Google consent.",
    }


def google_connection_status() -> dict[str, Any]:
    principal = current_principal()
    connected = get_token_store().load_credentials_json(principal) is not None
    return {"connected": connected, "principal_key": principal.storage_key}


def disconnect_google_account(*, confirm: bool) -> dict[str, Any]:
    if not confirm:
        return {"status": "confirmation_required", "action": "Call again with confirm=true."}
    principal = current_principal()
    get_token_store().delete_credentials(principal)
    return {"status": "disconnected"}


def register_connection_tools(server: FastMCP) -> None:
    """Register OAuth connection tools on the composed server exactly once."""

    @server.tool(name="connect_google_workspace")
    async def connect_google_workspace() -> dict[str, Any]:
        """Return a secure Google OAuth URL bound to the authenticated MCP user."""
        return create_google_authorization()

    @server.tool(name="get_google_connection_status")
    async def get_google_connection_status() -> dict[str, Any]:
        """Report whether the authenticated MCP user has connected Google Workspace."""
        return google_connection_status()

    @server.tool(name="disconnect_google_workspace")
    async def disconnect_google_workspace_tool(confirm: bool = False) -> dict[str, Any]:
        """Remove only the authenticated user's encrypted Google OAuth credentials."""
        return disconnect_google_account(confirm=confirm)


def register_oauth_callback_route(server: FastMCP) -> None:
    """Register the unprotected callback; opaque one-time state authenticates it."""
    server_id = id(server)
    if server_id in _REGISTERED_CALLBACK_SERVERS:
        return
    _REGISTERED_CALLBACK_SERVERS.add(server_id)

    @server.custom_route("/google/oauth/callback", methods=["GET"], include_in_schema=False)
    async def google_oauth_callback(request: Request) -> HTMLResponse:
        state = request.query_params.get("state", "")
        pending = get_token_store().consume_oauth_state(state)
        if pending is None:
            return HTMLResponse("Invalid or expired OAuth state. Start the connection again from MCP.", status_code=400)
        if request.query_params.get("error"):
            return HTMLResponse("Google authorization was not completed. You may close this window and try again.", status_code=400)
        try:
            flow = _flow(state=state, code_verifier=pending.code_verifier)
            flow.fetch_token(authorization_response=str(request.url))
            get_token_store().save_credentials_json(pending.principal, flow.credentials.to_json())
        except Exception:  # pragma: no cover - provider error shape varies
            return HTMLResponse("Google authorization could not be completed. Return to MCP and try again.", status_code=400)
        return HTMLResponse(
            "<html><body><h2>Google Workspace connected</h2>"
            "<p>You can close this window and return to your MCP client.</p></body></html>"
        )
