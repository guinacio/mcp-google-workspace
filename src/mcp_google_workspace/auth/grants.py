"""Current Google capability grants of the calling principal.

The catalog (``tools/list``) and execution (``tools/call``, background task
workers) both authorize against the grant *as stored now*, read on every
request: MCP 2026-07-28 lets a catalog vary with the request's authorization,
but never with earlier requests on a connection, so nothing here depends on
connection or visibility state.

Parsing stored credentials into capability names is the only cached step. The
cache key is ``(principal storage key, grant revision)`` where the revision is
a digest of the stored, encrypted-at-rest credentials document: any consent,
disconnection, refresh or rotation produces a new revision (or none at all),
so a stale entry can never authorize anything. Entries of different principals
never share a key.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
import json
import threading
from typing import Final

from google.oauth2.credentials import Credentials

from .identity import Principal, current_principal

#: Namespaces whose tools need no Google capability (local infrastructure).
INFRASTRUCTURE_NAMESPACES: Final[frozenset[str]] = frozenset({"files", "apps"})

#: Root tools that are always listed and callable regardless of grants.
ALWAYS_AVAILABLE_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "connect_google_workspace",
        "get_google_connection_status",
        "disconnect_google_workspace",
        "refresh_workspace_catalog",
        "get_workspace_capabilities",
        "get_mcp_apps_diagnostics",
        "search_workspace",
        "resolve_workspace_resource",
        "search_tools",
        "call_tool",
    }
)

_CACHE_LIMIT: Final[int] = 4096


@dataclass(frozen=True, slots=True)
class GrantSnapshot:
    """The caller's Google grant at the moment it was read."""

    principal_key: str
    revision: str | None
    capabilities: frozenset[str]

    @property
    def connected(self) -> bool:
        return self.revision is not None


_CACHE: OrderedDict[tuple[str, str], frozenset[str]] = OrderedDict()
_CACHE_LOCK = threading.Lock()


def grant_revision(credentials_json: str) -> str:
    """Opaque revision of one stored credentials document."""
    return sha256(credentials_json.encode("utf-8")).hexdigest()[:32]


def _parse_capabilities(credentials_json: str) -> frozenset[str]:
    from .google_auth import CAPABILITY_SCOPES, get_google_scopes

    try:
        credentials = Credentials.from_authorized_user_info(json.loads(credentials_json))
    except (TypeError, ValueError, json.JSONDecodeError):
        return frozenset()
    return frozenset(
        name for name in CAPABILITY_SCOPES if credentials.has_scopes(get_google_scopes([name]))
    )


def read_grant(principal: Principal | None = None) -> GrantSnapshot:
    """Read the principal's stored grant now (blocking storage I/O)."""
    from .google_auth import get_token_store

    principal = principal or current_principal()
    credentials_json = get_token_store().load_credentials_json(principal)
    if credentials_json is None:
        return GrantSnapshot(principal.storage_key, None, frozenset())
    revision = grant_revision(credentials_json)
    key = (principal.storage_key, revision)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return GrantSnapshot(principal.storage_key, revision, cached)
    capabilities = _parse_capabilities(credentials_json)
    with _CACHE_LOCK:
        _CACHE[key] = capabilities
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)
    return GrantSnapshot(principal.storage_key, revision, capabilities)


async def read_grant_async(principal: Principal | None = None) -> GrantSnapshot:
    """:func:`read_grant` off the event loop."""
    from ..common.async_ops import run_blocking

    principal = principal or current_principal()
    return await run_blocking(read_grant, principal)


def clear_grant_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def required_capability(tool_name: str) -> str | None:
    """Google capability a (root-qualified) tool name needs, or ``None``."""
    from .google_auth import CAPABILITY_SCOPES

    if tool_name in ALWAYS_AVAILABLE_TOOLS:
        return None
    namespace = tool_name.split("_", 1)[0]
    if namespace in INFRASTRUCTURE_NAMESPACES:
        return None
    return namespace if namespace in CAPABILITY_SCOPES else None


def is_tool_granted(tool_name: str, grant: GrantSnapshot) -> bool:
    """Whether *grant* authorizes the tool: capability-free tools always are."""
    capability = required_capability(tool_name)
    return capability is None or capability in grant.capabilities


__all__ = [
    "ALWAYS_AVAILABLE_TOOLS",
    "GrantSnapshot",
    "INFRASTRUCTURE_NAMESPACES",
    "clear_grant_cache",
    "grant_revision",
    "is_tool_granted",
    "read_grant",
    "read_grant_async",
    "required_capability",
]
