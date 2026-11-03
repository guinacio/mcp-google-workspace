"""Bounded JWKS refetch in ``WorkspaceJWTVerifier`` on FastMCP's monotonic cache clock.

FastMCP starts ``_jwks_cache_time`` at 0 on a ``time.monotonic()`` clock, so on a
host up for less than the cache TTL (a fresh CI runner or container host) an
empty, never-filled cache would look fresh. The first fetch must not use up the
one forced refetch that lets a rotated signing key through.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import anyio
import fastmcp.server.auth.providers.jwt as fastmcp_jwt
import pytest

from mcp_google_workspace.auth import remote_auth
from mcp_google_workspace.auth.remote_auth import WorkspaceJWTVerifier


def _verifier(monkeypatch: pytest.MonkeyPatch, published: set[str]) -> tuple[WorkspaceJWTVerifier, list[str | None]]:
    verifier = WorkspaceJWTVerifier(jwks_uri="https://issuer.test/jwks.json", issuer="https://issuer.test")
    fetches: list[str | None] = []

    async def refresh(kid: str | None) -> str:
        fetches.append(kid)
        verifier._jwks_cache = {name: f"key-{name}" for name in published}
        verifier._jwks_cache_time = remote_auth.time.monotonic()
        if kid not in verifier._jwks_cache:
            raise ValueError("unknown kid")
        return verifier._jwks_cache[kid]

    monkeypatch.setattr(verifier, "_refresh_jwks_key", refresh)
    return verifier, fetches


def test_first_fetch_on_a_recently_booted_host_does_not_spend_the_rotation_refetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [120.0]  # seconds since boot: well inside the one-hour cache TTL
    # Only the verifier's clock: the event loop keeps the real one.
    fake_time = SimpleNamespace(monotonic=lambda: clock[0], time=time.time)
    monkeypatch.setattr(remote_auth, "time", fake_time)
    monkeypatch.setattr(fastmcp_jwt, "time", fake_time)
    published = {"k1"}
    verifier, fetches = _verifier(monkeypatch, published)

    assert anyio.run(verifier._get_jwks_key, "k1") == "key-k1"
    clock[0] += 1
    published.add("k2")
    assert anyio.run(verifier._get_jwks_key, "k2") == "key-k2"
    assert fetches == ["k1", "k2"]

    clock[0] += 1
    published.add("k3")
    with pytest.raises(ValueError, match="rate limited"):
        anyio.run(verifier._get_jwks_key, "k3")
    assert fetches == ["k1", "k2"]
    assert anyio.run(verifier._get_jwks_key, "k1") == "key-k1"

    clock[0] += 31  # past the refresh interval: the next rotation is allowed again
    assert anyio.run(verifier._get_jwks_key, "k3") == "key-k3"
    assert fetches == ["k1", "k2", "k3"]
