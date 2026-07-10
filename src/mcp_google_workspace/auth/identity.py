"""Request-scoped authenticated identities for Google Workspace access."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from typing import Any

from fastmcp.server.dependencies import get_access_token


class PrincipalRequiredError(PermissionError):
    """Raised when Workspace data is requested without an authenticated user."""


@dataclass(frozen=True, slots=True)
class Principal:
    """Stable tenant identity derived from a verified MCP bearer token."""

    issuer: str
    subject: str
    client_id: str | None = None

    @property
    def storage_key(self) -> str:
        """Opaque filesystem-safe key; never expose the raw subject in a path."""
        return sha256(f"{self.issuer}\x00{self.subject}".encode("utf-8")).hexdigest()


def current_principal(*, require_authenticated: bool = True) -> Principal:
    """Return the verified bearer-token subject for the active MCP request.

    FastMCP verifies the bearer token before invoking a tool and makes its
    claims available through ``get_access_token``. Stdio uses a local identity;
    the SSE entrypoint installs bearer authentication before Workspace tools
    can be reached.
    """
    token = get_access_token()
    if token is not None:
        claims: dict[str, Any] = token.claims or {}
        subject = claims.get("sub")
        issuer = claims.get("iss")
        if isinstance(subject, str) and subject and isinstance(issuer, str) and issuer:
            return Principal(issuer=issuer, subject=subject, client_id=token.client_id)
        raise PrincipalRequiredError("Authenticated access token must contain non-empty iss and sub claims.")

    local_subject = os.getenv("MCP_LOCAL_PRINCIPAL", "local-user").strip()
    if local_subject:
        return Principal(issuer="local", subject=local_subject)
    if not require_authenticated:
        return Principal(issuer="local", subject="anonymous")
    raise PrincipalRequiredError(
        "An authenticated MCP bearer token is required to access Google Workspace data."
    )
