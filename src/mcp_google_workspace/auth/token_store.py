"""Encrypted, per-principal persistence for Google OAuth credentials."""

from __future__ import annotations

import json
import os
import secrets
from contextlib import contextmanager
from hashlib import sha256
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, RLock
from typing import Any, ContextManager, Iterator, Protocol
from weakref import WeakValueDictionary

from cryptography.fernet import InvalidToken
import redis
from redis.exceptions import LockError, WatchError

from ..common.crypto import FernetKeyring
from .identity import Principal

_LOCK = RLock()
_PRINCIPAL_LOCKS_GUARD = Lock()
_PRINCIPAL_LOCKS: WeakValueDictionary[str, RLock] = WeakValueDictionary()
_REDIS_CLIENTS_GUARD = Lock()
_REDIS_CLIENTS: dict[str, redis.Redis] = {}


def _principal_credential_lock(storage_key: str) -> RLock:
    """Per-principal refresh lock so one slow refresh cannot stall the others."""
    with _PRINCIPAL_LOCKS_GUARD:
        return _PRINCIPAL_LOCKS.setdefault(storage_key, RLock())


def _shared_redis_client(url: str) -> redis.Redis:
    """Reuse one connection pool per Redis URL for the process lifetime."""
    with _REDIS_CLIENTS_GUARD:
        client = _REDIS_CLIENTS.get(url)
        if client is None:
            client = redis.Redis.from_url(url, decode_responses=False)
            _REDIS_CLIENTS[url] = client
        return client


class TokenStoreError(RuntimeError):
    """Raised for unreadable, undecryptable, or insecure token storage."""


class TokenStore(Protocol):
    """Credential and one-time OAuth state contract for local or distributed use."""

    backend_name: str

    def load_credentials_json(self, principal: Principal) -> str | None: ...
    def save_credentials_json(self, principal: Principal, credentials_json: str) -> None: ...
    def delete_credentials(self, principal: Principal) -> bool: ...
    def delete_credentials_if_fingerprint(
        self, principal: Principal, expected_fingerprint: str
    ) -> bool: ...
    def create_oauth_state(
        self,
        principal: Principal,
        code_verifier: str,
        *,
        scopes: list[str] | None = None,
        ttl_seconds: int = 600,
    ) -> PendingOAuthAuthorization: ...
    def consume_oauth_state(self, state: str) -> PendingOAuthAuthorization | None: ...
    def credential_lock(self, principal: Principal) -> ContextManager[None]: ...
    def ping(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class PendingOAuthAuthorization:
    """One-time PKCE state bound to exactly one authenticated principal."""

    state: str
    principal: Principal
    code_verifier: str
    expires_at: int
    scopes: tuple[str, ...] = ()


class EncryptedTokenStore:
    """Filesystem store with atomic writes and Fernet-encrypted payloads."""

    backend_name = "encrypted-filesystem"

    def __init__(self, directory: Path, encryption_key: str | FernetKeyring) -> None:
        try:
            self._keyring = (
                encryption_key
                if isinstance(encryption_key, FernetKeyring)
                else FernetKeyring.single(encryption_key)
            )
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
        ciphertext = self._keyring.encrypt(json.dumps(value, separators=(",", ":")).encode("utf-8"))
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(ciphertext)
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(path)

    def _read_encrypted_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            result = self._keyring.decrypt(path.read_bytes())
            value = json.loads(result.plaintext)
        except (InvalidToken, OSError, json.JSONDecodeError) as exc:
            raise TokenStoreError(f"Unable to decrypt token state at {path}.") from exc
        if not isinstance(value, dict):
            raise TokenStoreError(f"Token state at {path} has an invalid shape.")
        if result.needs_rotation:
            self._write_encrypted_json(path, value)
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

    def delete_credentials_if_fingerprint(
        self,
        principal: Principal,
        expected_fingerprint: str,
    ) -> bool:
        """Delete only when the stored credential generation still matches."""
        path = self._tokens / f"{principal.storage_key}.token"
        with _LOCK:
            value = self._read_encrypted_json(path)
            if value is None:
                return False
            credentials_json = value.get("credentials_json")
            if not isinstance(credentials_json, str):
                raise TokenStoreError("Stored credentials are missing credentials_json.")
            actual = sha256(credentials_json.encode("utf-8")).hexdigest()
            if not secrets.compare_digest(actual, expected_fingerprint):
                return False
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise TokenStoreError("Unable to remove credentials for the current user.") from exc
            return True

    def create_oauth_state(
        self,
        principal: Principal,
        code_verifier: str,
        *,
        scopes: list[str] | None = None,
        ttl_seconds: int = 600,
    ) -> PendingOAuthAuthorization:
        state = secrets.token_urlsafe(32)
        pending = PendingOAuthAuthorization(
            state=state,
            principal=principal,
            code_verifier=code_verifier,
            expires_at=int(time.time()) + ttl_seconds,
            scopes=tuple(sorted(set(scopes or []))),
        )
        with _LOCK:
            now = int(time.time())
            outstanding = self._prune_oauth_states_locked(now, principal=principal)
            if outstanding >= 10:
                raise TokenStoreError(
                    "Too many outstanding OAuth authorization attempts for this principal."
                )
            self._write_encrypted_json(
                self._states / f"{state}.state",
                {
                    "issuer": principal.issuer,
                    "subject": principal.subject,
                    "client_id": principal.client_id,
                    "code_verifier": pending.code_verifier,
                    "expires_at": pending.expires_at,
                    "scopes": list(pending.scopes),
                },
            )
        return pending

    def _prune_oauth_states_locked(
        self,
        now: int,
        *,
        principal: Principal | None = None,
    ) -> int:
        """Delete expired state and count live states for an optional principal."""
        if not self._states.exists():
            return 0
        outstanding = 0
        for path in self._states.glob("*.state"):
            try:
                value = self._read_encrypted_json(path)
            except TokenStoreError:
                path.unlink(missing_ok=True)
                continue
            if value is None:
                continue
            expires_at = value.get("expires_at")
            if not isinstance(expires_at, int) or expires_at < now:
                path.unlink(missing_ok=True)
                continue
            if (
                principal is not None
                and value.get("issuer") == principal.issuer
                and value.get("subject") == principal.subject
            ):
                outstanding += 1
        return outstanding

    def consume_oauth_state(self, state: str) -> PendingOAuthAuthorization | None:
        if not state or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in state):
            return None
        path = self._states / f"{state}.state"
        with _LOCK:
            self._prune_oauth_states_locked(int(time.time()))
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
        scopes = value.get("scopes", [])
        if (
            not isinstance(expires_at, int)
            or not isinstance(issuer, str)
            or not isinstance(subject, str)
            or not isinstance(code_verifier, str)
            or not isinstance(scopes, list)
            or not all(isinstance(scope, str) for scope in scopes)
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
            scopes=tuple(scopes),
        )

    @contextmanager
    def credential_lock(self, principal: Principal) -> Iterator[None]:
        """Serialize credential refresh and replacement per principal.

        The lock must be per principal: a refresh holds it across a Google
        network round trip, and one slow principal must not block the rest.
        """
        with _principal_credential_lock(principal.storage_key):
            yield

    def ping(self) -> bool:
        self._ensure_directories()
        return os.access(self._directory, os.W_OK)


class RedisEncryptedTokenStore:
    """Cross-replica encrypted credentials and atomic one-time OAuth state."""

    backend_name = "redis"
    _CONSUME_STATE = """
    local value = redis.call('GET', KEYS[1])
    if value then redis.call('DEL', KEYS[1]) end
    return value
    """
    _CREATE_STATE = """
    redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[1])
    if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[2]) then return 0 end
    if not redis.call('SET', KEYS[1], ARGV[3], 'EX', ARGV[4], 'NX') then return 0 end
    redis.call('ZADD', KEYS[2], ARGV[5], ARGV[6])
    redis.call('EXPIRE', KEYS[2], ARGV[4])
    return 1
    """

    def __init__(
        self,
        url: str,
        encryption_key: str | FernetKeyring,
        *,
        prefix: str = "mcp:google-oauth",
    ) -> None:
        try:
            self._keyring = (
                encryption_key
                if isinstance(encryption_key, FernetKeyring)
                else FernetKeyring.single(encryption_key)
            )
        except (ValueError, TypeError) as exc:
            raise TokenStoreError("OAuth token encryption keyring is invalid.") from exc
        self._redis = _shared_redis_client(url)
        self._prefix = prefix.rstrip(":")

    def _credential_key(self, principal: Principal) -> str:
        return f"{self._prefix}:credentials:{principal.storage_key}"

    def _state_key(self, state: str) -> str:
        return f"{self._prefix}:state:{state}"

    def _principal_states_key(self, principal: Principal) -> str:
        return f"{self._prefix}:states:{principal.storage_key}"

    def _encrypt(self, value: dict[str, Any]) -> bytes:
        return self._keyring.encrypt(
            json.dumps(value, separators=(",", ":")).encode("utf-8")
        )

    def _decrypt(self, value: bytes | str | None) -> dict[str, Any] | None:
        if value is None:
            return None
        raw = value.encode("utf-8") if isinstance(value, str) else value
        try:
            result = self._keyring.decrypt(raw)
            decoded = json.loads(result.plaintext)
        except (InvalidToken, json.JSONDecodeError, TypeError) as exc:
            raise TokenStoreError("Unable to decrypt distributed OAuth state.") from exc
        if not isinstance(decoded, dict):
            raise TokenStoreError("Distributed OAuth state has an invalid shape.")
        return decoded

    def load_credentials_json(self, principal: Principal) -> str | None:
        value = self._decrypt(self._redis.get(self._credential_key(principal)))
        if value is None:
            return None
        credentials_json = value.get("credentials_json")
        if not isinstance(credentials_json, str):
            raise TokenStoreError("Stored credentials are missing credentials_json.")
        return credentials_json

    def save_credentials_json(self, principal: Principal, credentials_json: str) -> None:
        self._redis.set(
            self._credential_key(principal),
            self._encrypt(
                {"credentials_json": credentials_json, "updated_at": int(time.time())}
            ),
        )

    def delete_credentials(self, principal: Principal) -> bool:
        return bool(self._redis.delete(self._credential_key(principal)))

    def delete_credentials_if_fingerprint(
        self,
        principal: Principal,
        expected_fingerprint: str,
    ) -> bool:
        key = self._credential_key(principal)
        while True:
            try:
                with self._redis.pipeline() as pipe:
                    pipe.watch(key)
                    value = self._decrypt(pipe.get(key))
                    if value is None:
                        return False
                    credentials_json = value.get("credentials_json")
                    if not isinstance(credentials_json, str):
                        raise TokenStoreError(
                            "Stored credentials are missing credentials_json."
                        )
                    actual = sha256(credentials_json.encode("utf-8")).hexdigest()
                    if not secrets.compare_digest(actual, expected_fingerprint):
                        return False
                    pipe.multi()
                    pipe.delete(key)
                    pipe.execute()
                    return True
            except WatchError:
                continue

    def create_oauth_state(
        self,
        principal: Principal,
        code_verifier: str,
        *,
        scopes: list[str] | None = None,
        ttl_seconds: int = 600,
    ) -> PendingOAuthAuthorization:
        state = secrets.token_urlsafe(32)
        pending = PendingOAuthAuthorization(
            state=state,
            principal=principal,
            code_verifier=code_verifier,
            expires_at=int(time.time()) + ttl_seconds,
            scopes=tuple(sorted(set(scopes or []))),
        )
        payload = self._encrypt(
            {
                "issuer": principal.issuer,
                "subject": principal.subject,
                "client_id": principal.client_id,
                "code_verifier": code_verifier,
                "expires_at": pending.expires_at,
                "scopes": list(pending.scopes),
            }
        )
        created = self._redis.eval(
            self._CREATE_STATE,
            2,
            self._state_key(state),
            self._principal_states_key(principal),
            int(time.time()),
            10,
            payload,
            ttl_seconds,
            pending.expires_at,
            state,
        )
        if int(created or 0) != 1:
            raise TokenStoreError(
                "Too many outstanding OAuth authorization attempts for this principal."
            )
        return pending

    def consume_oauth_state(self, state: str) -> PendingOAuthAuthorization | None:
        if not state or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for char in state
        ):
            return None
        raw = self._redis.eval(self._CONSUME_STATE, 1, self._state_key(state))
        value = self._decrypt(raw)
        if value is None:
            return None
        expires_at = value.get("expires_at")
        issuer = value.get("issuer")
        subject = value.get("subject")
        client_id = value.get("client_id")
        code_verifier = value.get("code_verifier")
        scopes = value.get("scopes", [])
        if (
            not isinstance(expires_at, int)
            or not isinstance(issuer, str)
            or not isinstance(subject, str)
            or not isinstance(code_verifier, str)
            or not isinstance(scopes, list)
            or not all(isinstance(scope, str) for scope in scopes)
            or (client_id is not None and not isinstance(client_id, str))
        ):
            raise TokenStoreError("Stored OAuth state has an invalid shape.")
        if expires_at < int(time.time()):
            return None
        principal = Principal(issuer=issuer, subject=subject, client_id=client_id)
        self._redis.zrem(self._principal_states_key(principal), state)
        return PendingOAuthAuthorization(
            state=state,
            principal=principal,
            code_verifier=code_verifier,
            expires_at=expires_at,
            scopes=tuple(scopes),
        )

    @contextmanager
    def credential_lock(self, principal: Principal) -> Iterator[None]:
        timeout = int(os.getenv("MCP_OAUTH_REFRESH_LOCK_SECONDS", "60"))
        if not 10 <= timeout <= 300:
            raise TokenStoreError(
                "MCP_OAUTH_REFRESH_LOCK_SECONDS must be between 10 and 300."
            )
        lock = self._redis.lock(
            f"{self._prefix}:refresh-lock:{principal.storage_key}",
            timeout=timeout,
            blocking_timeout=timeout,
        )
        if not lock.acquire(blocking=True):
            raise TokenStoreError("Timed out waiting for the distributed OAuth refresh lock.")
        try:
            yield
        finally:
            try:
                lock.release()
            except LockError as exc:
                raise TokenStoreError(
                    "Distributed OAuth refresh lock expired before it could be released."
                ) from exc

    def ping(self) -> bool:
        return bool(self._redis.ping())
