from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace
from cryptography.fernet import Fernet
from fastmcp.server.auth import AccessToken
import pytest

from mcp_google_workspace.auth.identity import Principal, current_principal
from mcp_google_workspace.auth.token_store import EncryptedTokenStore, TokenStoreError
from mcp_google_workspace.runtime import get_remote_security_settings
from mcp_google_workspace.server_http import build_http_auth
import mcp_google_workspace.server_http as remote_entry


def _configure_http(monkeypatch: pytest.MonkeyPatch, tmp_path) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("MCP_HTTP_BASE_URL", "https://mcp.example.test")
    monkeypatch.setenv("MCP_GOOGLE_OAUTH_REDIRECT_URL", "https://mcp.example.test/google/oauth/callback")
    monkeypatch.setenv("MCP_HTTP_JWT_AUDIENCE", "workspace-mcp")
    monkeypatch.setenv("MCP_HTTP_JWT_ISSUER", "https://issuer.example.test")
    monkeypatch.setenv("MCP_HTTP_JWKS_URI", "https://issuer.example.test/.well-known/jwks.json")
    monkeypatch.setenv("MCP_USER_TOKEN_DIR", str(tmp_path / "tokens"))
    monkeypatch.setenv("MCP_TOKEN_ENCRYPTION_KEY", key)
    return key


def test_http_requires_complete_oidc_and_encrypted_store_configuration(monkeypatch, tmp_path) -> None:
    _configure_http(monkeypatch, tmp_path)

    settings = get_remote_security_settings()
    auth = build_http_auth()

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


def test_stale_credential_generation_cannot_delete_a_newer_refresh(tmp_path) -> None:
    store = EncryptedTokenStore(tmp_path, Fernet.generate_key().decode())
    principal = Principal(issuer="https://issuer.example", subject="alice")
    old = '{"refresh_token":"old"}'
    store.save_credentials_json(principal, old)
    old_fingerprint = sha256(old.encode()).hexdigest()
    store.save_credentials_json(principal, '{"refresh_token":"new"}')

    assert not store.delete_credentials_if_fingerprint(principal, old_fingerprint)
    assert store.load_credentials_json(principal) == '{"refresh_token":"new"}'


def test_local_oauth_requests_all_enabled_scopes_once(monkeypatch, tmp_path) -> None:
    from mcp_google_workspace.auth import google_auth

    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}")
    captured: list[list[str]] = []

    class EmptyStore:
        def load_credentials_json(self, principal):
            return None

    sentinel = object()
    monkeypatch.delenv("MCP_GOOGLE_OAUTH_REDIRECT_URL", raising=False)
    monkeypatch.setattr(google_auth, "resolve_client_credentials_path", lambda: credentials_path)
    monkeypatch.setattr(google_auth, "get_token_store", EmptyStore)
    monkeypatch.setattr(
        google_auth,
        "_run_local_oauth",
        lambda principal, path, scopes: captured.append(scopes) or sentinel,
    )

    result = google_auth._get_credentials_unlocked(google_auth.GMAIL_SCOPES)

    assert result is sentinel
    assert captured == [google_auth.get_google_scopes()]
    assert set(google_auth.CALENDAR_SCOPES).issubset(captured[0])


def test_remote_incremental_auth_explicitly_preserves_existing_scopes(monkeypatch) -> None:
    from mcp_google_workspace.auth import google_auth, google_oauth

    existing_scopes = google_auth.get_google_scopes(["gmail"])
    stored = SimpleNamespace(
        load_credentials_json=lambda principal: __import__("json").dumps(
            {
                "token": "access",
                "refresh_token": "refresh",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "client",
                "client_secret": "secret",
                "scopes": existing_scopes,
            }
        )
    )
    monkeypatch.setattr(google_oauth, "get_token_store", lambda: stored)

    cumulative = google_oauth._cumulative_authorization_scopes(
        Principal(issuer="https://issuer.example", subject="alice"),
        google_auth.get_google_scopes(["drive"]),
    )

    assert set(existing_scopes).issubset(cumulative)
    assert set(google_auth.DRIVE_SCOPES).issubset(cumulative)
    assert set(google_auth.ACCOUNT_TIMEZONE_SCOPES).issubset(cumulative)


def test_oauth_state_is_one_time_and_bound_to_the_originating_principal(tmp_path) -> None:
    store = EncryptedTokenStore(tmp_path, Fernet.generate_key().decode())
    principal = Principal(issuer="https://issuer.example", subject="alice")

    pending = store.create_oauth_state(principal, "a" * 64)
    consumed = store.consume_oauth_state(pending.state)

    assert consumed is not None
    assert consumed.principal == principal
    assert consumed.code_verifier == "a" * 64
    assert store.consume_oauth_state(pending.state) is None


def test_oauth_state_creation_is_bounded_per_principal(tmp_path) -> None:
    store = EncryptedTokenStore(tmp_path, Fernet.generate_key().decode())
    principal = Principal(issuer="https://issuer.example", subject="alice")
    for _ in range(10):
        store.create_oauth_state(principal, "a" * 64)
    with pytest.raises(TokenStoreError, match="Too many outstanding"):
        store.create_oauth_state(principal, "b" * 64)


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


def test_remote_entrypoint_uses_stateless_streamable_http(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "8123")
    monkeypatch.setattr(remote_entry, "configure_logging", lambda: None)
    monkeypatch.setattr(remote_entry, "configure_remote_tool_search", lambda: None)
    monkeypatch.setattr(remote_entry, "build_http_auth", lambda: object())
    monkeypatch.setattr(
        remote_entry,
        "get_remote_security_settings",
        lambda: SimpleNamespace(base_url="https://mcp.example.test"),
    )
    monkeypatch.setattr(remote_entry, "register_oauth_callback_route", lambda server: None)
    monkeypatch.setattr(
        remote_entry.workspace_mcp,
        "run",
        lambda **kwargs: captured.update(kwargs),
    )

    remote_entry.main()

    assert captured["transport"] == "http"
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8123
    assert captured["stateless_http"] is False
    assert captured["json_response"] is True
    assert captured["allowed_hosts"] == ["mcp.example.test"]
    assert captured["allowed_origins"] == ["https://mcp.example.test"]
    assert len(captured["middleware"]) == 1
