"""Gateway-hosted (Prefect Horizon) entrypoint configuration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_google_workspace.auth import identity
from mcp_google_workspace.hosted import CLIENT_JSON_ENV, prepare_gateway_environment

CLIENT = {"web": {"client_id": "id", "client_secret": "secret"}}


def test_prepare_derives_redirect_client_file_and_token_dir(tmp_path: Path) -> None:
    env = {
        "MCP_STATE_DIR": str(tmp_path),
        "MCP_HTTP_BASE_URL": "https://example.fastmcp.app/",
        CLIENT_JSON_ENV: json.dumps(CLIENT),
    }
    prepare_gateway_environment(env)

    assert env[identity.TRUSTED_GATEWAY_AUTH_ENV] == "true"
    assert env["MCP_GOOGLE_OAUTH_REDIRECT_URL"] == "https://example.fastmcp.app/google/oauth/callback"
    assert env["MCP_USER_TOKEN_DIR"] == str(tmp_path / "tokens")
    written = Path(env["MCP_CREDENTIALS_DIR"]) / "credentials.json"
    assert json.loads(written.read_text(encoding="utf-8")) == CLIENT


def test_prepare_keeps_explicit_settings(tmp_path: Path) -> None:
    env = {
        "MCP_STATE_DIR": str(tmp_path),
        "MCP_HTTP_BASE_URL": "https://example.fastmcp.app",
        "MCP_GOOGLE_OAUTH_REDIRECT_URL": "https://example.fastmcp.app/custom",
        "MCP_CREDENTIALS_DIR": "/secrets",
        "MCP_USER_TOKEN_DIR": "/data/tokens",
        identity.TRUSTED_GATEWAY_AUTH_ENV: "false",
        CLIENT_JSON_ENV: json.dumps(CLIENT),
    }
    before = dict(env)
    prepare_gateway_environment(env)
    assert env == before


@pytest.mark.parametrize("raw", ["not json", json.dumps({"other": {}})])
def test_prepare_rejects_invalid_client_json(tmp_path: Path, raw: str) -> None:
    with pytest.raises(SystemExit):
        prepare_gateway_environment({"MCP_STATE_DIR": str(tmp_path), CLIENT_JSON_ENV: raw})


def test_trusted_gateway_serves_single_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_GOOGLE_OAUTH_REDIRECT_URL", "https://example.fastmcp.app/google/oauth/callback")
    monkeypatch.delenv("MCP_HTTP_JWT_ISSUER", raising=False)
    monkeypatch.setenv("MCP_LOCAL_PRINCIPAL", "owner")
    monkeypatch.delenv(identity.TRUSTED_GATEWAY_AUTH_ENV, raising=False)
    with pytest.raises(identity.PrincipalRequiredError):
        identity.current_principal()

    monkeypatch.setenv(identity.TRUSTED_GATEWAY_AUTH_ENV, "true")
    assert not identity.remote_identity_required()
    assert identity.current_principal() == identity.Principal(issuer="local", subject="owner")


# --- pasted authorization response (gateway blocks the callback route) -------

from cryptography.fernet import Fernet  # noqa: E402

from mcp_google_workspace.auth import google_oauth  # noqa: E402
from mcp_google_workspace.auth.token_store import EncryptedTokenStore  # noqa: E402

OWNER = identity.Principal(issuer="local", subject="owner")


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> EncryptedTokenStore:
    token_store = EncryptedTokenStore(tmp_path, Fernet.generate_key().decode())
    monkeypatch.setattr(google_oauth, "get_token_store", lambda: token_store)
    return token_store


def _exchange(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    codes: list[str] = []

    class _Creds:
        def has_scopes(self, scopes: list[str]) -> bool:
            return True

        def to_json(self) -> str:
            return json.dumps({"token": "t", "refresh_token": "r"})

    class _Flow:
        credentials = _Creds()

        def fetch_token(self, *, code: str) -> None:
            codes.append(code)

    monkeypatch.setattr(google_oauth, "_flow", lambda **_kwargs: _Flow())
    monkeypatch.setattr(
        google_oauth,
        "google_connection_status",
        lambda: {"state": "connected", "connected": True, "granted_capabilities": ["gmail"]},
    )
    return codes


def test_pasted_callback_url_completes_the_callers_consent(
    monkeypatch: pytest.MonkeyPatch, store: EncryptedTokenStore
) -> None:
    codes = _exchange(monkeypatch)
    pending = store.create_oauth_state(OWNER, "v" * 64, scopes=["s"])
    url = f"https://example.fastmcp.app/google/oauth/callback?state={pending.state}&code=abc&iss=https%3A%2F%2Faccounts.google.com"

    result = google_oauth.complete_pasted_authorization(url, OWNER)

    assert result["connected"] is True and result["state"] == "connected"
    assert codes == ["abc"]
    assert store.load_credentials_json(OWNER) is not None
    # One-time: the same response cannot be replayed.
    assert google_oauth.complete_pasted_authorization(url, OWNER)["state"] == "invalid_state"


def test_pasted_callback_accepts_a_bare_query_string(
    monkeypatch: pytest.MonkeyPatch, store: EncryptedTokenStore
) -> None:
    _exchange(monkeypatch)
    pending = store.create_oauth_state(OWNER, "v" * 64, scopes=["s"])
    assert google_oauth.complete_pasted_authorization(f"?code=abc&state={pending.state}", OWNER)["connected"] is True


def test_pasted_callback_for_another_principal_is_refused(
    monkeypatch: pytest.MonkeyPatch, store: EncryptedTokenStore
) -> None:
    codes = _exchange(monkeypatch)
    victim = identity.Principal(issuer="local", subject="someone-else")
    pending = store.create_oauth_state(victim, "v" * 64, scopes=["s"])

    result = google_oauth.complete_pasted_authorization(f"?state={pending.state}&code=abc", OWNER)

    assert result == {"state": "invalid_state", "connected": False, "message": google_oauth._CALLBACK_MESSAGES["invalid_state"]}
    assert codes == []
    assert store.load_credentials_json(victim) is None


def test_pasted_denied_consent_reports_not_completed(store: EncryptedTokenStore) -> None:
    pending = store.create_oauth_state(OWNER, "v" * 64, scopes=["s"])
    result = google_oauth.complete_pasted_authorization(f"?state={pending.state}&error=access_denied", OWNER)
    assert result["state"] == "not_completed" and result["connected"] is False
