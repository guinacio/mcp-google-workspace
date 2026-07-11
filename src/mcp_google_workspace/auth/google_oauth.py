"""Browser OAuth connection flow for one authenticated MCP principal at a time."""

from __future__ import annotations

import secrets
import json
import requests
from typing import Any

from fastmcp import Context, FastMCP
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from starlette.requests import Request
from starlette.responses import HTMLResponse

from ..runtime import get_remote_security_settings
from ..common.async_ops import run_blocking
from .google_auth import (
    CAPABILITY_SCOPES,
    get_credentials,
    get_google_scopes,
    get_token_store,
    is_remote_oauth_mode,
    resolve_client_credentials_path,
)
from .identity import Principal, current_principal

_REGISTERED_CALLBACK_SERVERS: set[int] = set()


def _flow(
    *,
    state: str | None = None,
    code_verifier: str | None = None,
    scopes: list[str] | None = None,
) -> Flow:
    settings = get_remote_security_settings()
    credentials_path = resolve_client_credentials_path()
    if not credentials_path.exists():
        raise FileNotFoundError(
            "Google OAuth client credentials.json not found. Configure MCP_CREDENTIALS_DIR on the server."
        )
    flow = Flow.from_client_secrets_file(
        str(credentials_path),
        scopes=scopes or get_google_scopes(["gmail"]),
        state=state,
        redirect_uri=settings.google_oauth_redirect_url,
        autogenerate_code_verifier=False,
    )
    flow.code_verifier = code_verifier
    return flow


def create_google_authorization(capabilities: list[str] | None = None) -> dict[str, Any]:
    """Create a one-time, PKCE-protected Google consent URL for the MCP caller."""
    if not is_remote_oauth_mode():
        raise RuntimeError("Remote Google authorization requires the HTTP OAuth callback mode.")
    principal = current_principal()
    # RFC 7636 permits 43–128 characters. token_urlsafe(72) is about 96.
    code_verifier = secrets.token_urlsafe(72)
    selected_capabilities = capabilities or ["gmail"]
    newly_requested_scopes = get_google_scopes(selected_capabilities)
    scopes = _cumulative_authorization_scopes(principal, newly_requested_scopes)
    pending = get_token_store().create_oauth_state(
        principal, code_verifier, scopes=scopes
    )
    flow = _flow(state=pending.state, code_verifier=code_verifier, scopes=scopes)
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
        "after_consent": {"tool": "refresh_workspace_catalog", "arguments": {}},
        "capabilities": selected_capabilities,
        "requested_scopes": scopes,
        "newly_requested_scopes": newly_requested_scopes,
    }


def connect_local_google_account(capabilities: list[str] | None = None) -> dict[str, Any]:
    """Complete or verify the single-user loopback OAuth grant used by MCPB/stdio."""
    selected_capabilities = capabilities or ["gmail"]
    # Validate caller-supplied names, while intentionally granting the complete
    # enabled local catalog once so cross-service tools never churn credentials.
    get_google_scopes(selected_capabilities)
    get_credentials(get_google_scopes())
    status = google_connection_status()
    return {
        "state": status["state"],
        "connected": status["connected"],
        "mode": "local_stdio",
        "granted_capabilities": status.get("granted_capabilities", []),
        "granted_scopes": status.get("granted_scopes", []),
        "requested_capabilities": selected_capabilities,
        "action": None if status["connected"] else status.get("action"),
    }


def _cumulative_authorization_scopes(
    principal: Principal, newly_requested_scopes: list[str]
) -> list[str]:
    """Explicitly retain prior grants instead of relying on provider-side union behavior."""
    existing_json = get_token_store().load_credentials_json(principal)
    if existing_json is None:
        return sorted(set(newly_requested_scopes))
    try:
        existing = Credentials.from_authorized_user_info(json.loads(existing_json))
    except (TypeError, ValueError, json.JSONDecodeError):
        return sorted(set(newly_requested_scopes))
    return sorted(set(newly_requested_scopes) | set(existing.scopes or []))


def google_connection_status(capability: str | None = None) -> dict[str, Any]:
    principal = current_principal()
    credentials_json = get_token_store().load_credentials_json(principal)
    if credentials_json is None:
        return {
            "state": "not_connected",
            "connected": False,
            "principal_key": principal.storage_key,
            "granted_scopes": [],
        }
    required_scopes = get_google_scopes([capability]) if capability else []
    try:
        credentials = Credentials.from_authorized_user_info(json.loads(credentials_json))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {
            "state": "error",
            "connected": False,
            "principal_key": principal.storage_key,
            "granted_scopes": [],
            "action": "Reconnect Google Workspace.",
        }
    granted_scopes = sorted(credentials.scopes or [])
    granted_capabilities = sorted(
        name
        for name in CAPABILITY_SCOPES
        if credentials.has_scopes(get_google_scopes([name]))
    )
    if required_scopes and not credentials.has_scopes(required_scopes):
        state = "missing_scopes"
        connected = False
        action = "Reconnect Google Workspace to grant the missing capabilities."
    elif credentials.valid:
        state = "connected"
        connected = True
        action = None
    elif credentials.expired and credentials.refresh_token:
        state = "refresh_required"
        connected = True
        action = "The next Workspace request will refresh the access token."
    else:
        state = "reauth_required"
        connected = False
        action = "Reconnect Google Workspace."
    return {
        "state": state,
        "connected": connected,
        "principal_key": principal.storage_key,
        "granted_scopes": granted_scopes,
        "granted_capabilities": granted_capabilities,
        "checked_capability": capability,
        "required_scopes": required_scopes,
        "expires_at": credentials.expiry.isoformat() if credentials.expiry else None,
        "action": action,
    }


def disconnect_google_account(*, confirm: bool) -> dict[str, Any]:
    if not confirm:
        return {"status": "confirmation_required", "action": "Call again with confirm=true."}
    principal = current_principal()
    store = get_token_store()
    credentials_json = store.load_credentials_json(principal)
    revoked = False
    revocation_error: str | None = None
    if credentials_json is not None:
        payload = json.loads(credentials_json)
        token = payload.get("refresh_token") or payload.get("token")
        if isinstance(token, str) and token:
            try:
                response = requests.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": token},
                    headers={"content-type": "application/x-www-form-urlencoded"},
                    timeout=15,
                )
                revoked = response.status_code in {200, 400}
                if not revoked:
                    revocation_error = f"Google revocation returned HTTP {response.status_code}."
            except requests.RequestException as exc:
                revocation_error = f"Google revocation failed: {exc}"
    store.delete_credentials(principal)
    return {
        "status": "disconnected",
        "grant_revoked": revoked,
        "revocation_error": revocation_error,
    }


def register_connection_tools(server: FastMCP) -> None:
    """Register OAuth connection tools on the composed server exactly once."""

    @server.tool(name="connect_google_workspace")
    async def connect_google_workspace(
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        """Connect locally with loopback OAuth or return a remote incremental-consent URL."""
        if not is_remote_oauth_mode():
            return await run_blocking(connect_local_google_account, capabilities)
        return create_google_authorization(capabilities)

    @server.tool(name="get_google_connection_status")
    async def get_google_connection_status(
        capability: str | None = None,
    ) -> dict[str, Any]:
        """Report whether the authenticated MCP user has connected Google Workspace."""
        return google_connection_status(capability)

    @server.tool(name="disconnect_google_workspace")
    async def disconnect_google_workspace_tool(confirm: bool = False) -> dict[str, Any]:
        """Remove only the authenticated user's encrypted Google OAuth credentials."""
        return await run_blocking(disconnect_google_account, confirm=confirm)

    @server.tool(name="refresh_workspace_catalog")
    async def refresh_workspace_catalog(ctx: Context) -> dict[str, Any]:
        """Refresh capability-aware tools after Google consent or disconnection."""
        await ctx.reset_visibility()
        status = google_connection_status()
        return {
            "status": "catalog_refreshed",
            "granted_capabilities": status.get("granted_capabilities", []),
            "notification_sent": "tools/list_changed",
        }


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
            previous_json = get_token_store().load_credentials_json(pending.principal)
            flow = _flow(
                state=state,
                code_verifier=pending.code_verifier,
                scopes=list(pending.scopes),
            )
            flow.fetch_token(authorization_response=str(request.url))
            if not flow.credentials.has_scopes(list(pending.scopes)):
                raise RuntimeError("Google did not preserve all previously granted scopes.")
            payload = json.loads(flow.credentials.to_json())
            if not payload.get("refresh_token") and previous_json is not None:
                previous = json.loads(previous_json)
                if previous.get("refresh_token"):
                    payload["refresh_token"] = previous["refresh_token"]
            get_token_store().save_credentials_json(
                pending.principal, json.dumps(payload, separators=(",", ":"))
            )
        except Exception:  # pragma: no cover - provider error shape varies
            return HTMLResponse("Google authorization could not be completed. Return to MCP and try again.", status_code=400)
        return HTMLResponse(
            "<html><body><h2>Google Workspace connected</h2>"
            "<p>You can close this window and return to your MCP client.</p></body></html>"
        )
