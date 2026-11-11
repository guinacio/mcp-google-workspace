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
