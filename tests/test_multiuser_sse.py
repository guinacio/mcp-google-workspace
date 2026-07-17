from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace
from cryptography.fernet import Fernet
import anyio
from fastmcp import Client, FastMCP
from fastmcp.server.auth import AccessToken
import pytest

from mcp_google_workspace.auth.identity import Principal, current_principal
from mcp_google_workspace.auth.token_store import (
    EncryptedTokenStore,
    RedisEncryptedTokenStore,
    TokenStoreError,
)
from mcp_google_workspace.runtime import get_remote_security_settings
from mcp_google_workspace.server_http import build_http_auth
import mcp_google_workspace.server_http as remote_entry


def test_explicit_connect_tool_uses_local_loopback_flow_without_http_settings(
    monkeypatch,
) -> None:
    from mcp_google_workspace.auth import google_oauth

    server = FastMCP("local-connect-test")
    google_oauth.register_connection_tools(server)
    expected = {
        "state": "connected",
        "connected": True,
        "mode": "local_stdio",
        "granted_capabilities": ["gmail"],
        "granted_scopes": ["scope"],
        "requested_capabilities": ["gmail"],
        "action": None,
    }
    monkeypatch.delenv("MCP_GOOGLE_OAUTH_REDIRECT_URL", raising=False)
    monkeypatch.setattr(google_oauth, "connect_local_google_account", lambda capabilities: expected)

    async def call_connect():
        async with Client(server) as client:
            return await client.call_tool("connect_google_workspace")

    result = anyio.run(call_connect)
    assert result.structured_content == expected


def test_local_connect_grants_full_enabled_catalog_once(monkeypatch) -> None:
    from mcp_google_workspace.auth import google_auth, google_oauth

    requested: list[list[str]] = []
    monkeypatch.setattr(
        google_oauth,
        "get_credentials",
        lambda scopes: requested.append(scopes) or object(),
    )
    monkeypatch.setattr(
        google_oauth,
        "google_connection_status",
        lambda: {
            "state": "connected",
            "connected": True,
            "granted_capabilities": ["calendar", "gmail"],
            "granted_scopes": google_auth.get_google_scopes(),
            "action": None,
        },
    )

    result = google_oauth.connect_local_google_account(["gmail"])

    assert requested == [google_auth.get_google_scopes()]
    assert result["connected"] is True
    assert result["mode"] == "local_stdio"


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


class _FakeRedisLock:
    def acquire(self, blocking=True):
        return blocking

    def release(self):
        return None


class _FakeRedisTokenBackend:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.zsets: dict[str, dict[str, int]] = {}

    def set(self, key, value, **kwargs):
        if kwargs.get("nx") and key in self.values:
            return False
        self.values[str(key)] = value
        return True

    def get(self, key):
        return self.values.get(str(key))

    def delete(self, key):
        return int(self.values.pop(str(key), None) is not None)

    def zrem(self, key, value):
        return int(self.zsets.setdefault(str(key), {}).pop(str(value), None) is not None)

    def eval(self, script, number_of_keys, *args):
        assert number_of_keys in {1, 2}
        if "ZREMRANGEBYSCORE" in script:
            state_key, set_key, now, limit, payload, _ttl, expires_at, state = args
            entries = self.zsets.setdefault(str(set_key), {})
            for name, expiry in list(entries.items()):
                if expiry <= int(now):
                    entries.pop(name)
            if len(entries) >= int(limit) or str(state_key) in self.values:
                return 0
            self.values[str(state_key)] = payload
            entries[str(state)] = int(expires_at)
            return 1
        key = str(args[0])
        return self.values.pop(key, None)

    def lock(self, *args, **kwargs):
        return _FakeRedisLock()

    def ping(self):
        return True

    def pipeline(self):
        backend = self

        class Pipeline:
            delete_key: str | None = None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def watch(self, key):
                return None

            def get(self, key):
                return backend.get(key)

            def multi(self):
                return None

            def delete(self, key):
                self.delete_key = str(key)

            def execute(self):
                if self.delete_key is not None:
                    backend.delete(self.delete_key)
                return [True]

        return Pipeline()


def test_redis_token_store_is_shared_atomic_and_principal_bounded(monkeypatch) -> None:
    backend = _FakeRedisTokenBackend()
    monkeypatch.setattr(
        "mcp_google_workspace.auth.token_store.redis.Redis.from_url",
        lambda *args, **kwargs: backend,
    )
    key = Fernet.generate_key().decode()
    first = RedisEncryptedTokenStore("redis://example", key)
    second = RedisEncryptedTokenStore("redis://example", key)
    principal = Principal(issuer="https://issuer.example", subject="alice")

    first.save_credentials_json(principal, '{"refresh_token":"secret"}')
    assert second.load_credentials_json(principal) == '{"refresh_token":"secret"}'
    stale_fingerprint = sha256(b'{"refresh_token":"secret"}').hexdigest()
    second.save_credentials_json(principal, '{"refresh_token":"new"}')
    assert not first.delete_credentials_if_fingerprint(principal, stale_fingerprint)
    current_fingerprint = sha256(b'{"refresh_token":"new"}').hexdigest()
    assert first.delete_credentials_if_fingerprint(principal, current_fingerprint)
    second.save_credentials_json(principal, '{"refresh_token":"new"}')
    with second.credential_lock(principal):
        assert second.ping()

    pending = [
        first.create_oauth_state(principal, f"verifier-{index}")
        for index in range(10)
    ]
    with pytest.raises(TokenStoreError, match="Too many outstanding"):
        first.create_oauth_state(principal, "one-too-many")
    consumed = second.consume_oauth_state(pending[0].state)
    assert consumed is not None
    assert consumed.principal == principal
    assert second.consume_oauth_state(pending[0].state) is None
    assert first.create_oauth_state(principal, "replacement")


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
