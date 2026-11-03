"""MCP authorization for the remote HTTP server (resource-server side).

The server is an OAuth 2.1 *protected resource*: an external authorization
server (the OIDC issuer) issues the bearer tokens, this server verifies them
and publishes RFC 9728 protected-resource metadata so clients can discover
that issuer from a ``401`` challenge. It implements no authorization server,
token endpoint or dynamic client registration.

* :class:`WorkspaceJWTVerifier` is FastMCP's ``JWTVerifier`` (signature via
  JWKS, issuer, audience, expiry, optional scopes) with a stricter claim
  policy: ``exp``, ``iss`` and ``sub`` are mandatory, ``nbf``/``iat`` in the
  future are refused, and a token naming an unknown key id can force a JWKS
  refetch at most once per ``min_refresh_interval`` (key rotation still
  works; random ``kid`` values cannot turn every request into an IdP fetch).
* :func:`build_remote_auth` wraps it in FastMCP's ``RemoteAuthProvider`` so
  ``/.well-known/oauth-protected-resource<resource path>`` is served and the
  ``WWW-Authenticate`` challenge's ``resource_metadata`` points at it,
  including when ``MCP_HTTP_BASE_URL`` carries a reverse-proxy path prefix.

Incoming MCP tokens are used only to identify the principal. They are never
forwarded to Google: Google access uses the separately consented, encrypted
per-principal Google grant (``auth.google_auth``).
"""

from __future__ import annotations

import os
import time
from typing import Any, Final

from fastmcp.server.auth import AccessToken, RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from pydantic import AnyHttpUrl

from ..runtime import RemoteSecuritySettings

DEFAULT_MIN_JWKS_REFRESH_SECONDS: Final[float] = 30.0
CLOCK_SKEW_SECONDS: Final[int] = 60
MCP_PATH: Final[str] = "/mcp"


class WorkspaceJWTVerifier(JWTVerifier):
    """``JWTVerifier`` with mandatory identity/expiry claims and bounded JWKS refetch."""

    def __init__(
        self,
        *,
        min_refresh_interval: float = DEFAULT_MIN_JWKS_REFRESH_SECONDS,
        jwks_cache_ttl: int | None = None,
        **kwargs: Any,
    ) -> None:
        interval = max(0.0, min_refresh_interval)
        # FastMCP >= 4.1 bounds every JWKS fetch attempt (failures included)
        # by ``jwks_refresh_interval``; use the same bound for both paths.
        kwargs.setdefault("jwks_refresh_interval", interval)
        super().__init__(**kwargs)
        self._min_refresh_interval = interval
        self._last_forced_refresh = float("-inf")
        if jwks_cache_ttl is not None:
            self._cache_ttl = int(jwks_cache_ttl)

    async def _get_jwks_key(self, kid: str | None) -> str:
        # A token naming a key id missing from a fresh cache is how key rotation
        # shows up. FastMCP's own bound also counts the routine fetch that filled
        # the cache, so on its own a rotation seen within the interval after any
        # fetch would be refused. Allow one such forced refetch per interval,
        # independent of routine fetches, and refuse the rest without contacting
        # the IdP. ``_jwks_cache_time`` is on FastMCP's monotonic clock; an empty
        # cache was never filled and is not fresh.
        now = time.monotonic()
        cache_fresh = bool(self._jwks_cache) and now - self._jwks_cache_time < self._cache_ttl
        if cache_fresh and kid and kid not in self._jwks_cache:
            if now - self._last_forced_refresh < self._min_refresh_interval:
                raise ValueError("Unknown JWT key id; JWKS refresh is rate limited.")
            self._last_forced_refresh = now
            # Exempt this one budgeted refetch from FastMCP's own bound.
            self._jwks_last_refresh_attempt = None
        return await super()._get_jwks_key(kid)

    async def load_access_token(self, token: str) -> AccessToken | None:
        access = await super().load_access_token(token)
        if access is None:
            return None
        claims = access.claims or {}
        now = time.time()
        exp, issuer, subject = claims.get("exp"), claims.get("iss"), claims.get("sub")
        if not isinstance(exp, (int, float)) or isinstance(exp, bool):
            self.logger.info("Bearer token rejected: missing exp claim")
            return None
        if not (isinstance(issuer, str) and issuer and isinstance(subject, str) and subject):
            self.logger.info("Bearer token rejected: missing iss or sub claim")
            return None
        for name in ("nbf", "iat"):
            value = claims.get(name)
            if value is None:
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value > now + CLOCK_SKEW_SECONDS:
                self.logger.info("Bearer token rejected: %s claim is invalid or in the future", name)
                return None
        return access


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def build_jwt_verifier(settings: RemoteSecuritySettings, **overrides: Any) -> WorkspaceJWTVerifier:
    audiences = _csv(settings.jwt_audience)
    required_scopes = _csv(os.getenv("MCP_HTTP_REQUIRED_SCOPES", ""))
    options: dict[str, Any] = {
        "jwks_uri": settings.jwt_jwks_uri,
        "issuer": settings.jwt_issuer,
        "audience": audiences if len(audiences) > 1 else settings.jwt_audience,
        "algorithm": os.getenv("MCP_HTTP_JWT_ALGORITHM", "").strip() or None,
        "required_scopes": required_scopes or None,
        "base_url": settings.base_url,
    }
    options.update(overrides)
    return WorkspaceJWTVerifier(**options)


def build_remote_auth(
    settings: RemoteSecuritySettings,
    *,
    verifier: JWTVerifier | None = None,
) -> RemoteAuthProvider:
    """Bearer verification plus RFC 9728 protected-resource discovery."""
    token_verifier = verifier or build_jwt_verifier(settings)
    return RemoteAuthProvider(
        token_verifier=token_verifier,
        authorization_servers=[AnyHttpUrl(settings.jwt_issuer)],
        base_url=settings.base_url,
        resource_name="Google Workspace MCP",
    )


def resource_url(settings: RemoteSecuritySettings) -> str:
    """The protected resource identifier clients see (``<base>/mcp``)."""
    return f"{settings.base_url.rstrip('/')}{MCP_PATH}"


__all__ = [
    "CLOCK_SKEW_SECONDS",
    "MCP_PATH",
    "WorkspaceJWTVerifier",
    "build_jwt_verifier",
    "build_remote_auth",
    "resource_url",
]
