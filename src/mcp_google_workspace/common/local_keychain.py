"""Token-encryption key for the local stdio runtime, kept in the OS keychain.

The desktop bundle (and ``python -m mcp_google_workspace``) runs as one local
user, so it does not ask that user to generate and paste a Fernet key. On
first use it generates one and stores it in the operating system's credential
store through ``keyring``: Windows Credential Manager (DPAPI), macOS Keychain,
or the freedesktop Secret Service on Linux. Only the key lives there; the
encrypted Google tokens stay on disk (Windows limits a credential blob to
about 2.5 KB, which a token with many scopes can approach).

The key is never written to a file. When no secure backend is available
(for example headless Linux without a Secret Service), startup fails with
instructions to configure ``MCP_TOKEN_ENCRYPTION_KEY`` explicitly instead of
silently storing the key beside the ciphertext it protects.

Losing the keychain entry (new machine, OS reinstall) only means the saved
Google grants can no longer be decrypted: the user reconnects Google once.

HTTP deployments never use this module; they receive key rings from the
operator's secret management (``MCP_SECRET_FILE`` / ``MCP_TOKEN_ENCRYPTION_KEYS``).
"""

from __future__ import annotations

import logging
import threading
from typing import Final

from cryptography.fernet import Fernet

LOGGER = logging.getLogger("mcp_google_workspace.local_keychain")

KEYCHAIN_SERVICE: Final[str] = "mcp-google-workspace"
KEYCHAIN_ACCOUNT: Final[str] = "token-encryption-key"

# Backends that do not protect secrets: the "fail" and "null" placeholders and
# the file-based ``keyrings.alt`` implementations (plaintext or obfuscated files).
_INSECURE_BACKEND_MODULES: Final[tuple[str, ...]] = (
    "keyring.backends.fail",
    "keyring.backends.null",
    "keyrings.alt",
)

_UNAVAILABLE = (
    "No secure OS keychain is available to hold the token encryption key "
    "({reason}). Set MCP_TOKEN_ENCRYPTION_KEY (generate one with "
    "cryptography.fernet.Fernet.generate_key().decode()) or MCP_SECRET_FILE."
)

_lock = threading.Lock()
_cached: str | None = None


class LocalKeychainUnavailable(ValueError):
    """The OS keychain cannot hold the local token encryption key."""


def _secure_backend() -> object:
    import keyring
    from keyring.backends.chainer import ChainerBackend

    backend = keyring.get_keyring()
    members = list(getattr(backend, "backends", ())) if isinstance(backend, ChainerBackend) else [backend]
    if not members:
        raise LocalKeychainUnavailable(_UNAVAILABLE.format(reason="no keyring backend"))
    for member in members:
        module = type(member).__module__
        if module.startswith(_INSECURE_BACKEND_MODULES):
            raise LocalKeychainUnavailable(
                _UNAVAILABLE.format(reason=f"only the insecure backend {module} is configured")
            )
    return backend


def _valid(key: str | None) -> bool:
    if not key:
        return False
    try:
        Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError):
        return False
    return True


def load_or_create_local_key() -> str:
    """Return the local token encryption key, creating it in the keychain once."""
    global _cached
    with _lock:
        if _cached is not None:
            return _cached
        from keyring.errors import KeyringError

        backend = _secure_backend()
        try:
            stored = backend.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)  # type: ignore[attr-defined]
            if stored is not None and not _valid(stored):
                raise LocalKeychainUnavailable(
                    f"The OS keychain entry {KEYCHAIN_SERVICE}/{KEYCHAIN_ACCOUNT} is not a "
                    "valid Fernet key. Delete it to generate a new one (saved Google "
                    "connections will need to be reconnected)."
                )
            if stored is None:
                backend.set_password(  # type: ignore[attr-defined]
                    KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, Fernet.generate_key().decode("ascii")
                )
                # Read back: some backends accept a write they cannot persist,
                # and a concurrent first start may have written its own key.
                stored = backend.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)  # type: ignore[attr-defined]
                if not _valid(stored):
                    raise LocalKeychainUnavailable(
                        _UNAVAILABLE.format(reason="the keychain did not persist the key")
                    )
                LOGGER.info("Created the token encryption key in the OS keychain.")
        except KeyringError as exc:
            raise LocalKeychainUnavailable(
                _UNAVAILABLE.format(reason=type(exc).__name__)
            ) from exc
        assert stored is not None
        _cached = stored
        return stored


def reset_local_key_cache() -> None:
    """Forget the in-process copy (tests)."""
    global _cached
    with _lock:
        _cached = None


__all__ = [
    "KEYCHAIN_ACCOUNT",
    "KEYCHAIN_SERVICE",
    "LocalKeychainUnavailable",
    "load_or_create_local_key",
    "reset_local_key_cache",
]
