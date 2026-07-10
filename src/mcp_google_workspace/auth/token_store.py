"""Encrypted, per-principal persistence for Google OAuth credentials."""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .identity import Principal

_LOCK = Lock()


class TokenStoreError(RuntimeError):
    """Raised for unreadable, undecryptable, or insecure token storage."""


@dataclass(frozen=True, slots=True)
class PendingOAuthAuthorization:
    """One-time PKCE state bound to exactly one authenticated principal."""

    state: str
    principal: Principal
    code_verifier: str
    expires_at: int


class EncryptedTokenStore:
    """Filesystem store with atomic writes and Fernet-encrypted payloads."""

    def __init__(self, directory: Path, encryption_key: str) -> None:
        try:
            self._fernet = Fernet(encryption_key.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise TokenStoreError(
                "MCP_TOKEN_ENCRYPTION_KEY must be a valid Fernet key. Generate one with "
                "cryptography.fernet.Fernet.generate_key().decode()."
            ) from exc
        self._directory = directory
        self._tokens = directory / "tokens"
        self._states = directory / "oauth-state"

    def _ensure_directories(self) -> None:
        for path in (self._tokens, self._states):
            path.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                path.chmod(0o700)

    def _write_encrypted_json(self, path: Path, value: dict[str, Any]) -> None:
        self._ensure_directories()
        ciphertext = self._fernet.encrypt(json.dumps(value, separators=(",", ":")).encode("utf-8"))
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(ciphertext)
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(path)

    def _read_encrypted_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            payload = self._fernet.decrypt(path.read_bytes())
            value = json.loads(payload)
        except (InvalidToken, OSError, json.JSONDecodeError) as exc:
            raise TokenStoreError(f"Unable to decrypt token state at {path}.") from exc
        if not isinstance(value, dict):
            raise TokenStoreError(f"Token state at {path} has an invalid shape.")
        return value

    def load_credentials_json(self, principal: Principal) -> str | None:
        with _LOCK:
            value = self._read_encrypted_json(self._tokens / f"{principal.storage_key}.token")
        if value is None:
            return None
        token_json = value.get("credentials_json")
        if not isinstance(token_json, str):
            raise TokenStoreError("Stored credentials are missing credentials_json.")
        return token_json

    def save_credentials_json(self, principal: Principal, credentials_json: str) -> None:
        with _LOCK:
            self._write_encrypted_json(
                self._tokens / f"{principal.storage_key}.token",
                {"credentials_json": credentials_json, "updated_at": int(time.time())},
            )

    def delete_credentials(self, principal: Principal) -> bool:
        path = self._tokens / f"{principal.storage_key}.token"
        with _LOCK:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise TokenStoreError("Unable to remove credentials for the current user.") from exc
        return True

    def create_oauth_state(self, principal: Principal, code_verifier: str, *, ttl_seconds: int = 600) -> PendingOAuthAuthorization:
        state = secrets.token_urlsafe(32)
        pending = PendingOAuthAuthorization(
            state=state,
            principal=principal,
            code_verifier=code_verifier,
            expires_at=int(time.time()) + ttl_seconds,
        )
        with _LOCK:
            self._write_encrypted_json(
                self._states / f"{state}.state",
                {
                    "issuer": principal.issuer,
                    "subject": principal.subject,
                    "client_id": principal.client_id,
                    "code_verifier": pending.code_verifier,
                    "expires_at": pending.expires_at,
                },
            )
        return pending

    def consume_oauth_state(self, state: str) -> PendingOAuthAuthorization | None:
        if not state or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in state):
            return None
        path = self._states / f"{state}.state"
        with _LOCK:
            value = self._read_encrypted_json(path)
            if value is None:
                return None
            try:
                path.unlink()
            except OSError as exc:
                raise TokenStoreError("Unable to consume OAuth state.") from exc
        expires_at = value.get("expires_at")
        issuer = value.get("issuer")
        subject = value.get("subject")
        client_id = value.get("client_id")
        code_verifier = value.get("code_verifier")
        if (
            not isinstance(expires_at, int)
            or not isinstance(issuer, str)
            or not isinstance(subject, str)
            or not isinstance(code_verifier, str)
            or (client_id is not None and not isinstance(client_id, str))
        ):
            raise TokenStoreError("Stored OAuth state has an invalid shape.")
        if expires_at < int(time.time()):
            return None
        return PendingOAuthAuthorization(
            state=state,
            principal=Principal(issuer=issuer, subject=subject, client_id=client_id),
            code_verifier=code_verifier,
            expires_at=expires_at,
        )
