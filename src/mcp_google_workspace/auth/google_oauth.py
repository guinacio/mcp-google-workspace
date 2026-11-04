"""Browser OAuth connection flow for one authenticated MCP principal at a time."""

from __future__ import annotations

import json
import logging
import secrets

import requests
from typing import Annotated, Any, Literal, get_args

from fastmcp import Context, FastMCP
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from starlette.requests import Request
from starlette.responses import HTMLResponse

from ..runtime import get_remote_security_settings
from ..common.async_ops import run_blocking
from ..tool_tiers import TOOL_TIERS, tool_tier
from .google_auth import (
    CAPABILITY_SCOPES,
    get_credentials,
    get_google_scopes,
    get_token_store,
    is_apps_dashboard_enabled,
    is_chat_enabled,
    is_gemini_enabled,
    is_keep_enabled,
    is_meet_enabled,
    is_remote_oauth_mode,
    resolve_client_credentials_path,
)
from .identity import Principal, current_principal

#: Capability names accepted by the connection tools (the keys of CAPABILITY_SCOPES).
CapabilityName = Literal[
    "calendar", "chat", "docs", "drive", "forms", "gmail",
    "keep", "meet", "people", "sheets", "slides", "tasks",
]
if set(get_args(CapabilityName)) != set(CAPABILITY_SCOPES):  # pragma: no cover - import-time guard
    raise RuntimeError("CapabilityName is out of sync with CAPABILITY_SCOPES.")

LOGGER = logging.getLogger("mcp_google_workspace.oauth")
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


def create_google_authorization(
    capabilities: list[str] | None = None, *, principal: Principal | None = None
) -> dict[str, Any]:
    """Create a one-time, PKCE-protected Google consent URL for the MCP caller."""
    if not is_remote_oauth_mode():
        raise RuntimeError("Remote Google authorization requires the HTTP OAuth callback mode.")
    principal = principal or current_principal()
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
        "after_consent": {"tool": "get_google_connection_status", "arguments": {}},
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


def google_connection_status(
    capability: str | None = None, *, principal: Principal | None = None
) -> dict[str, Any]:
    principal = principal or current_principal()
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


def disconnect_google_account() -> dict[str, Any]:
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


_CORE_NAMESPACES = ("calendar", "docs", "drive", "forms", "gmail", "people", "sheets", "slides", "tasks")


def register_connection_tools(server: FastMCP) -> None:
    """Register OAuth connection tools on the composed server exactly once."""

    @server.tool(name="connect_google_workspace")
    async def connect_google_workspace(
        capabilities: Annotated[
            list[CapabilityName] | None,
            (
                "Services to request access for; omit for ['gmail']. In remote mode these "
                "scopes are added to the ones already granted; in local mode the names are only "
                "validated and every enabled service is requested."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Connect the user's Google account, or add services to an existing connection, so Workspace tools can run.
        In local mode (no remote OAuth callback configured) it runs Google's browser consent flow on the server machine
        and waits up to 180 seconds by default, unless the stored grant already covers every enabled service. In remote
        mode it returns a one-time authorization_url, valid for 10 minutes, for the user to open. Returns state,
        connected and granted_capabilities locally, or authorization_url, expires_at and requested_scopes remotely;
        after remote consent call get_google_connection_status."""
        if not is_remote_oauth_mode():
            return await run_blocking(connect_local_google_account, capabilities)
        principal = current_principal()
        return await run_blocking(create_google_authorization, capabilities, principal=principal)

    @server.tool(name="get_google_connection_status")
    async def get_google_connection_status(
        capability: Annotated[
            CapabilityName | None,
            (
                "One service to verify, e.g. 'calendar'; if the grant lacks its scopes, state is "
                "missing_scopes. Omit to report the overall connection only."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Check whether the calling user has a stored Google connection and which services it grants, and what this
        server offers, without calling Google. Use it before connect_google_workspace, after a consent finishes, or to
        learn the active tool tier; for remote users tools/list already reflects the current grants, so list tools
        again after consent. Returns state (connected, refresh_required, missing_scopes, reauth_required, not_connected
        or error), connected, granted_capabilities, granted_scopes, expires_at, an action hint when the user must
        reconnect, and server (tool_tier, enabled_namespaces, optional_namespaces, oauth_capabilities, file_inputs, tool_search)."""
        from ..tool_discovery import tool_search_enabled  # imports auth; resolved at call time

        status = await run_blocking(google_connection_status, capability)
        optional = {
            "apps": is_apps_dashboard_enabled(),
            "chat": is_chat_enabled(),
            "gemini": is_gemini_enabled(),
            "keep": is_keep_enabled(),
            "meet": is_meet_enabled(),
        }
        expires_at = status.get("expires_at")
        action = status.get("action")
        return {
            "state": str(status.get("state")),
            "connected": bool(status.get("connected")),
            "principal_key": str(status.get("principal_key")),
            "granted_capabilities": list(status.get("granted_capabilities", [])),
            "granted_scopes": list(status.get("granted_scopes", [])),
            "checked_capability": str(capability) if capability is not None else None,
            "required_scopes": list(status.get("required_scopes", [])),
            "expires_at": str(expires_at) if expires_at is not None else None,
            "action": str(action) if action is not None else None,
            # What this deployment offers, from its configuration alone.
            "server": {
                "tool_tier": str(tool_tier()),
                "tool_tiers": list(TOOL_TIERS),
                "enabled_namespaces": sorted([*_CORE_NAMESPACES, *(name for name, on in optional.items() if on)]),
                "optional_namespaces": {
                    "apps": bool(optional["apps"]),
                    "chat": bool(optional["chat"]),
                    "gemini": bool(optional["gemini"]),
                    "keep": bool(optional["keep"]),
                    "meet": bool(optional["meet"]),
                },
                "oauth_capabilities": sorted(CAPABILITY_SCOPES),
                "file_inputs": {
                    "remote": "Call uploads_file_manager, then pass the upload's uploaded_file handle.",
                    "local": "Trusted stdio clients may pass local filesystem paths.",
                    "drive": "Gemini media tools also accept drive_file_id.",
                },
                "tool_search": bool(tool_search_enabled()),
            },
        }

    @server.tool(name="disconnect_google_workspace")
    async def disconnect_google_workspace_tool(ctx: Context | None = None) -> dict[str, Any]:
        """Disconnect the calling user's Google account: revoke the grant at Google and delete this user's stored
        credentials; other users' connections are untouched. The user is asked to confirm first, and the call returns
        status 'cancelled' if declined. Returns status 'disconnected' with grant_revoked and revocation_error; the
        stored credentials are deleted even when revocation fails. Reconnect later with connect_google_workspace."""
        return await run_blocking(disconnect_google_account)


#: Authorization-response issuers accepted from Google (RFC 9207). Google is
#: the only authorization server this callback ever redirects to, so a
#: different ``iss`` indicates a mix-up or forged response and is refused.
GOOGLE_AUTHORIZATION_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})

_CALLBACK_MESSAGES = {
    "invalid_state": "Invalid or expired OAuth state. Start the connection again from MCP.",
    "not_completed": "Google authorization was not completed. You may close this window and try again.",
    "issuer_mismatch": "The authorization response came from an unexpected issuer. Start the connection again from MCP.",
    "failed": "Google authorization could not be completed. Return to MCP and try again.",
}


def complete_google_authorization(
    state: str, code: str | None, error: str | None, issuer: str | None
) -> str:
    """Finish one Google authorization response (blocking I/O; run off the loop).

    Returns ``"connected"`` or one of the ``_CALLBACK_MESSAGES`` keys. The
    opaque one-time state is consumed first, whatever the outcome, so a
    response can never be replayed. The code is exchanged directly (with the
    PKCE verifier bound to that state) rather than by re-parsing the callback
    URL, whose scheme and host are the proxy-internal ones behind TLS
    termination. The exchange result is stored only under the principal that
    created the state; the MCP bearer token is never involved.
    """
    store = get_token_store()
    pending = store.consume_oauth_state(state) if state else None
    if pending is None:
        return "invalid_state"
    if error or not code:
        return "not_completed"
    if issuer is not None and issuer not in GOOGLE_AUTHORIZATION_ISSUERS:
        LOGGER.warning("Google OAuth callback rejected: unexpected authorization issuer.")
        return "issuer_mismatch"
    try:
        previous_json = store.load_credentials_json(pending.principal)
        flow = _flow(
            state=state,
            code_verifier=pending.code_verifier,
            scopes=list(pending.scopes),
        )
        flow.fetch_token(code=code)
        if not flow.credentials.has_scopes(list(pending.scopes)):
            raise RuntimeError("Google did not preserve all previously granted scopes.")
        payload = json.loads(flow.credentials.to_json())
        if not payload.get("refresh_token") and previous_json is not None:
            previous = json.loads(previous_json)
            if previous.get("refresh_token"):
                payload["refresh_token"] = previous["refresh_token"]
        store.save_credentials_json(pending.principal, json.dumps(payload, separators=(",", ":")))
    except Exception as exc:  # noqa: BLE001 - provider error shape varies
        LOGGER.warning("Google OAuth code exchange failed (%s).", type(exc).__name__)
        return "failed"
    return "connected"


def register_oauth_callback_route(server: FastMCP) -> None:
    """Register the unprotected callback; opaque one-time state authenticates it."""
    server_id = id(server)
    if server_id in _REGISTERED_CALLBACK_SERVERS:
        return
    _REGISTERED_CALLBACK_SERVERS.add(server_id)

    @server.custom_route("/google/oauth/callback", methods=["GET"], include_in_schema=False)
    async def google_oauth_callback(request: Request) -> HTMLResponse:
        params = request.query_params
        outcome = await run_blocking(
            complete_google_authorization,
            params.get("state", ""),
            params.get("code"),
            params.get("error"),
            params.get("iss"),
        )
        if outcome != "connected":
            return HTMLResponse(_CALLBACK_MESSAGES[outcome], status_code=400)
        return HTMLResponse(
            "<html><body><h2>Google Workspace connected</h2>"
            "<p>You can close this window and return to your MCP client.</p></body></html>"
        )
