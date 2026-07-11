"""Versioned encryption keys with mounted-secret support and online rotation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


@dataclass(frozen=True, slots=True)
class DecryptionResult:
    plaintext: bytes
    key_id: str
    needs_rotation: bool


class FernetKeyring:
    """Encrypt with one active key and decrypt with active or retained keys."""

    def __init__(self, keys: dict[str, str], active_key_id: str) -> None:
        if active_key_id not in keys:
            raise ValueError("The active encryption key ID is not present in the key ring.")
        if not keys:
            raise ValueError("At least one encryption key is required.")
        self.active_key_id = active_key_id
        try:
            self._fernets = {key_id: Fernet(value.encode("ascii")) for key_id, value in keys.items()}
        except (TypeError, ValueError) as exc:
            raise ValueError("Every encryption key must be a valid Fernet key.") from exc

    @classmethod
    def single(cls, key: str) -> "FernetKeyring":
        return cls({"legacy": key}, "legacy")

    @classmethod
    def from_environment(cls) -> "FernetKeyring":
        payload: dict[str, object] = {}
        secret_file = os.getenv("MCP_SECRET_FILE", "").strip()
        if secret_file:
            path = Path(secret_file).expanduser().resolve()
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("MCP_SECRET_FILE must contain a readable JSON object.") from exc
            if not isinstance(loaded, dict):
                raise ValueError("MCP_SECRET_FILE must contain a JSON object.")
            payload = loaded

        raw_ring = payload.get("token_encryption_keys") or os.getenv("MCP_TOKEN_ENCRYPTION_KEYS", "")
        active = payload.get("active_token_encryption_key_id") or os.getenv(
            "MCP_ACTIVE_TOKEN_ENCRYPTION_KEY_ID", ""
        )
        if isinstance(raw_ring, dict):
            parsed = raw_ring
        elif isinstance(raw_ring, str) and raw_ring.strip():
            try:
                parsed = json.loads(raw_ring)
            except json.JSONDecodeError as exc:
                raise ValueError("MCP_TOKEN_ENCRYPTION_KEYS must be a JSON object.") from exc
        else:
            parsed = None
        if parsed is not None:
            if not isinstance(parsed, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
            ):
                raise ValueError("MCP_TOKEN_ENCRYPTION_KEYS must map key IDs to Fernet keys.")
            keys = parsed
            active_id = str(active or next(iter(keys), ""))
            return cls(keys, active_id)

        legacy = payload.get("token_encryption_key") or os.getenv("MCP_TOKEN_ENCRYPTION_KEY", "")
        if not isinstance(legacy, str) or not legacy.strip():
            raise ValueError(
                "Configure token encryption through MCP_SECRET_FILE, "
                "MCP_TOKEN_ENCRYPTION_KEYS, or MCP_TOKEN_ENCRYPTION_KEY."
            )
        return cls.single(legacy.strip())

    def encrypt(self, plaintext: bytes) -> bytes:
        token = self._fernets[self.active_key_id].encrypt(plaintext)
        return self.active_key_id.encode("utf-8") + b"." + token

    def decrypt(self, ciphertext: bytes) -> DecryptionResult:
        key_id: str | None = None
        token = ciphertext
        prefix, separator, remainder = ciphertext.partition(b".")
        if separator:
            try:
                candidate = prefix.decode("utf-8")
            except UnicodeDecodeError:
                candidate = ""
            if candidate in self._fernets:
                key_id, token = candidate, remainder

        candidates = (
            [(key_id, self._fernets[key_id])] if key_id else list(self._fernets.items())
        )
        for candidate_id, fernet in candidates:
            try:
                plaintext = fernet.decrypt(token)
            except InvalidToken:
                continue
            return DecryptionResult(
                plaintext=plaintext,
                key_id=candidate_id,
                needs_rotation=candidate_id != self.active_key_id or key_id is None,
            )
        raise InvalidToken
