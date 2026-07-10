from __future__ import annotations

from cryptography.fernet import Fernet
from fastmcp.server.auth import AccessToken
import pytest

from mcp_google_workspace.auth.identity import Principal, current_principal
from mcp_google_workspace.auth.token_store import EncryptedTokenStore
from mcp_google_workspace.runtime import get_sse_security_settings
from mcp_google_workspace.server_sse import build_sse_auth


def _configure_sse(monkeypatch: pytest.MonkeyPatch, tmp_path) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("MCP_SSE_BASE_URL", "https://mcp.example.test")
    monkeypatch.setenv("MCP_GOOGLE_OAUTH_REDIRECT_URL", "https://mcp.example.test/google/oauth/callback")
    monkeypatch.setenv("MCP_SSE_JWT_AUDIENCE", "workspace-mcp")
    monkeypatch.setenv("MCP_SSE_JWT_ISSUER", "https://issuer.example.test")
    monkeypatch.setenv("MCP_SSE_JWKS_URI", "https://issuer.example.test/.well-known/jwks.json")
    monkeypatch.setenv("MCP_USER_TOKEN_DIR", str(tmp_path / "tokens"))
    monkeypatch.setenv("MCP_TOKEN_ENCRYPTION_KEY", key)
    return key


def test_sse_requires_complete_oidc_and_encrypted_store_configuration(monkeypatch, tmp_path) -> None:
    _configure_sse(monkeypatch, tmp_path)

    settings = get_sse_security_settings()
    auth = build_sse_auth()

    assert settings.user_token_dir == (tmp_path / "tokens").resolve()
    assert auth.issuer == "https://issuer.example.test"
    assert auth.audience == "workspace-mcp"


def test_per_principal_credentials_are_encrypted_and_isolated(tmp_path) -> None:
    store = EncryptedTokenStore(tmp_path, Fernet.generate_key().decode())
    alice = Principal(issuer="https://issuer.example", subject="alice")
    bob = Principal(issuer="https://issuer.example", subject="bob")

    store.save_credentials_json(alice, '{"refresh_token":"alice-secret"}')
    store.save_credentials_json(bob, '{"refresh_token":"bob-secret"}')

    assert store.load_credentials_json(alice) == '{"refresh_token":"alice-secret"}'
    assert store.load_credentials_json(bob) == '{"refresh_token":"bob-secret"}'
    assert b"alice-secret" not in (tmp_path / "tokens" / f"{alice.storage_key}.token").read_bytes()

    store.delete_credentials(alice)
    assert store.load_credentials_json(alice) is None
    assert store.load_credentials_json(bob) == '{"refresh_token":"bob-secret"}'


def test_oauth_state_is_one_time_and_bound_to_the_originating_principal(tmp_path) -> None:
    store = EncryptedTokenStore(tmp_path, Fernet.generate_key().decode())
    principal = Principal(issuer="https://issuer.example", subject="alice")

    pending = store.create_oauth_state(principal, "a" * 64)
    consumed = store.consume_oauth_state(pending.state)

    assert consumed is not None
    assert consumed.principal == principal
    assert consumed.code_verifier == "a" * 64
    assert store.consume_oauth_state(pending.state) is None


def test_principal_is_derived_from_verified_fastmcp_claims(monkeypatch) -> None:
    token = AccessToken(
        token="verified",
        client_id="client-1",
        scopes=[],
        claims={"iss": "https://issuer.example", "sub": "user-123"},
    )
    monkeypatch.setattr("mcp_google_workspace.auth.identity.get_access_token", lambda: token)

    principal = current_principal()

    assert principal.issuer == "https://issuer.example"
    assert principal.subject == "user-123"
