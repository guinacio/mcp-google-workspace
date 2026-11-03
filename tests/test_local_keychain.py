"""The local stdio runtime keeps its token encryption key in the OS keychain."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import keyring
import pytest
from cryptography.fernet import Fernet
from keyring.backend import KeyringBackend
from keyring.backends import fail

from mcp_google_workspace.auth.identity import Principal
from mcp_google_workspace.auth.token_store import EncryptedTokenStore
from mcp_google_workspace.common.crypto import LOCAL_KEYCHAIN_KEY_ID, FernetKeyring
from mcp_google_workspace.common.local_keychain import (
    KEYCHAIN_ACCOUNT,
    KEYCHAIN_SERVICE,
    LocalKeychainUnavailable,
    reset_local_key_cache,
)

ROOT = Path(__file__).resolve().parents[1]
LOCAL = Principal(issuer="local", subject="local-user")


class MemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.secrets: dict[tuple[str, str], str] = {}
        self.writes = 0

    def get_password(self, service: str, username: str) -> str | None:
        return self.secrets.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.writes += 1
        self.secrets[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.secrets.pop((service, username), None)


class DroppingKeyring(MemoryKeyring):
    """Accepts writes it never persists (a misconfigured backend)."""

    def set_password(self, service: str, username: str, password: str) -> None:
        self.writes += 1


@pytest.fixture
def bundle_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in ("MCP_SECRET_FILE", "MCP_TOKEN_ENCRYPTION_KEYS", "MCP_TOKEN_ENCRYPTION_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MCP_RUNTIME_MODE", "bundle")
    previous = keyring.get_keyring()
    reset_local_key_cache()
    yield
    reset_local_key_cache()
    keyring.set_keyring(previous)


def test_first_run_creates_one_key_and_later_runs_reuse_it(bundle_env: None, tmp_path: Path) -> None:
    backend = MemoryKeyring()
    keyring.set_keyring(backend)

    first = FernetKeyring.from_environment()
    assert first.active_key_id == LOCAL_KEYCHAIN_KEY_ID
    stored = backend.secrets[(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)]
    Fernet(stored.encode())  # a valid Fernet key
    store = EncryptedTokenStore(tmp_path, first)
    store.save_credentials_json(LOCAL, json.dumps({"refresh_token": "r"}))

    # A new process: nothing cached, same keychain entry, no second write.
    reset_local_key_cache()
    second = FernetKeyring.from_environment()
    assert backend.writes == 1
    assert json.loads(EncryptedTokenStore(tmp_path, second).load_credentials_json(LOCAL) or "{}") == {
        "refresh_token": "r"
    }
    # The key never reaches the token directory.
    assert all(stored not in path.read_text(errors="ignore") for path in tmp_path.rglob("*") if path.is_file())


def test_explicit_key_wins_and_keychain_is_not_touched(bundle_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    monkeypatch.setenv("MCP_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert FernetKeyring.from_environment().active_key_id == "legacy"
    assert backend.secrets == {} and backend.writes == 0


@pytest.mark.parametrize("backend", [fail.Keyring(), DroppingKeyring()])
def test_no_secure_keychain_fails_with_instructions_and_writes_no_file(
    bundle_env: None, backend: KeyringBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keyring.set_keyring(backend)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(LocalKeychainUnavailable, match="MCP_TOKEN_ENCRYPTION_KEY"):
        FernetKeyring.from_environment()
    assert list(tmp_path.iterdir()) == []


def test_insecure_file_backend_is_refused(bundle_env: None) -> None:
    class PlaintextKeyring(MemoryKeyring):
        pass

    PlaintextKeyring.__module__ = "keyrings.alt.file"
    keyring.set_keyring(PlaintextKeyring())
    with pytest.raises(LocalKeychainUnavailable, match="insecure backend keyrings.alt.file"):
        FernetKeyring.from_environment()


def test_corrupt_keychain_entry_is_reported_not_overwritten(bundle_env: None) -> None:
    backend = MemoryKeyring()
    backend.secrets[(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)] = "not-a-fernet-key"
    keyring.set_keyring(backend)
    with pytest.raises(LocalKeychainUnavailable, match="not a valid Fernet key"):
        FernetKeyring.from_environment()
    assert backend.secrets[(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)] == "not-a-fernet-key"


def test_http_runtime_never_falls_back_to_the_keychain(bundle_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    monkeypatch.delenv("MCP_RUNTIME_MODE")
    with pytest.raises(ValueError, match="Configure token encryption"):
        FernetKeyring.from_environment()
    assert backend.writes == 0


def test_bundle_manifest_no_longer_asks_for_a_key() -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert "token_encryption_key" not in manifest["user_config"]
    assert "MCP_TOKEN_ENCRYPTION_KEY" not in manifest["server"]["mcp_config"]["env"]
    assert manifest["user_config"]["gemini_api_key"]["sensitive"] is True
