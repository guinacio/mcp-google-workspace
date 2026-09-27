"""Process-wide cache for FastMCP's synthesized Prefab picker resource.

FastMCP 4.0.10's Prefab integration
(``fastmcp.server.providers.prefab_synthesis``) rebuilds the *entire* picker
``TextResource`` — including the Prefab renderer HTML this project's
``PREFAB_BUNDLED_RENDERER=1`` mode selects (``prefab_ui.renderer``'s bundled
``app.html``, ~6.6 MB) — from scratch on every single ``resources/list`` and
``resources/read`` call. ``_build_resource_for_tool`` calls
``prefab_ui.renderer.get_renderer_html()`` (which re-reads the bundled HTML
file from disk every time, see ``prefab_ui.renderer._get_bundled_html``),
merges CSP/permissions, and constructs a fresh resource object, unconditionally,
for every prefab-decorated tool on every listing/read.

FastMCP 4.0.10 has **no supported cache hook** for this: ``FastMCP.server()``
calls ``synthesize_prefab_resources``/``synthesize_prefab_resource_by_uri``
directly (see ``fastmcp/server/server.py``), with no setting, subclass point,
or provider-level cache argument to intercept it. The SDK's server-level
``cache_hints``/``ttlMs``/``cacheScope`` mechanism
(``fastmcp/server/caching.py``) is a *client-side* caching hint carried on the
wire; it does not change what the server itself computes on each request, so
it cannot avoid this server-side rebuild.

This module is the isolated, tested adapter the migration plan calls for
(``docs/MIGRATION_FASTMCP4_MCP_2026-07-28.md`` W7): it patches
``fastmcp.server.providers.prefab_synthesis._build_resource_for_tool`` in
place with a process-wide cache, keyed by everything that can change its
output — the tool identity, the installed ``prefab-ui`` version (the bundled
HTML ships inside that package), the resolved renderer mode /
``PREFAB_RENDERER_URL`` override, and the tool's CSP/permissions metadata.
Like ``common/fastmcp_compat.py``, this reads one FastMCP-private symbol; it
is isolated here and pinned by ``tests/test_prefab_render_cache.py`` so an
upgrade that moves it fails loudly instead of silently no-op caching.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastmcp.resources.base import Resource
    from fastmcp.tools.base import Tool

_LOGGER = logging.getLogger(__name__)

_LAYOUT_ERROR = (
    "Installed FastMCP no longer exposes "
    "fastmcp.server.providers.prefab_synthesis._build_resource_for_tool the way "
    "this cache adapter was validated against (fastmcp 4.0.x). Re-validate "
    "mcp_google_workspace.common.prefab_render_cache before upgrading."
)

_lock = threading.Lock()
_cache: dict[tuple[Any, ...], "Resource | None"] = {}
_builds = 0
_hits = 0
_patched = False
_original: Any = None


def _renderer_signature() -> tuple[str, str, str]:
    """Everything process-global that can change the rendered HTML/CSP."""
    try:
        from prefab_ui import __version__ as prefab_ui_version
    except ImportError:  # pragma: no cover - prefab_ui is a hard dependency here
        prefab_ui_version = "unknown"
    return (
        prefab_ui_version,
        os.environ.get("PREFAB_RENDERER_URL", ""),
        os.environ.get("PREFAB_BUNDLED_RENDERER", ""),
    )


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((key, _normalize(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_normalize(item) for item in value)
    return value


def _tool_meta_signature(tool: "Tool") -> Any:
    """The subset of tool meta that changes the resource's CSP/permissions."""
    meta = tool.meta or {}
    ui = meta.get("ui")
    ui = ui if isinstance(ui, dict) else {}
    return _normalize({"csp": ui.get("csp"), "permissions": ui.get("permissions")})


def cache_stats() -> dict[str, int]:
    """Return build/hit counters for tests and diagnostics."""
    with _lock:
        return {"builds": _builds, "hits": _hits, "entries": len(_cache)}


def reset_cache() -> None:
    """Test hook: drop cached resources (e.g. after mutating renderer env vars)."""
    global _builds, _hits
    with _lock:
        _cache.clear()
        _builds = 0
        _hits = 0


def install_prefab_resource_cache() -> None:
    """Patch FastMCP's per-tool Prefab resource synthesis with a process cache.

    Idempotent and safe to call more than once (e.g. from multiple entrypoints
    or tests); only the first call installs the patch.
    """
    global _patched, _original
    with _lock:
        if _patched:
            return
        from fastmcp.server.providers import prefab_synthesis

        original = getattr(prefab_synthesis, "_build_resource_for_tool", None)
        if not callable(original):
            raise RuntimeError(_LAYOUT_ERROR)
        _original = original

        def _cached_build(tool: "Tool") -> "Resource | None":
            global _builds, _hits
            key = (tool.name, _renderer_signature(), _tool_meta_signature(tool))
            with _lock:
                if key in _cache:
                    _hits += 1
                    return _cache[key]
            resource = original(tool)
            with _lock:
                _cache[key] = resource
                _builds += 1
            return resource

        prefab_synthesis._build_resource_for_tool = _cached_build
        _patched = True
        _LOGGER.debug("Installed process-wide Prefab picker resource cache.")


def is_installed() -> bool:
    return _patched
