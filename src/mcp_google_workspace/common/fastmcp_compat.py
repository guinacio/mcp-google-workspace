"""The only module allowed to read FastMCP private registry state.

FastMCP 4.0.10 exposes public *registration* APIs (``FastMCP.add_tool``,
``FastMCP.local_provider.add_tool``/``remove_tool``, ``FastMCPApp.add_tool``,
``FastMCP.add_extension``) and asynchronous, transform-applying *listing* APIs
(``await provider.list_tools()``). It has no public synchronous way to enumerate
the component objects a server or app registered locally, and no public read
accessor for registered server extensions.

This application decorates its tools at import time (titles, tags, schema
bounds, annotations, pagination envelopes) before any event loop exists, and
installs the Tasks extension idempotently. Those two reads are isolated here,
each checked against the installed FastMCP layout so an upgrade that moves the
internals fails loudly at import instead of silently skipping decoration.
``tests/test_fastmcp_compat.py`` pins the behavior against the public async
listing APIs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastmcp import FastMCP
from fastmcp.apps import FastMCPApp
from fastmcp.server.providers import LocalProvider
from fastmcp.tools import Tool

_LAYOUT_ERROR = (
    "Installed FastMCP no longer exposes the registry layout this adapter was "
    "validated against (fastmcp 4.0.x). Re-validate "
    "mcp_google_workspace.common.fastmcp_compat before upgrading."
)


def _local_provider(owner: FastMCP | FastMCPApp) -> LocalProvider:
    if isinstance(owner, FastMCP):
        provider: object = owner.local_provider
    elif isinstance(owner, FastMCPApp):
        provider = getattr(owner, "_local", None)
    else:
        raise TypeError(f"Unsupported component owner: {type(owner).__name__}")
    if not isinstance(provider, LocalProvider):
        raise RuntimeError(_LAYOUT_ERROR)
    return provider


def local_tools(owner: FastMCP | FastMCPApp) -> list[Tool]:
    """Return the tool objects registered directly on *owner*, in order.

    The returned objects are the live registered components (not copies), so
    public-field updates apply everywhere the tool is served, including
    hashed app-callback dispatch, which bypasses provider transforms.
    """
    components = getattr(_local_provider(owner), "_components", None)
    if not isinstance(components, dict):
        raise RuntimeError(_LAYOUT_ERROR)
    return [component for component in components.values() if isinstance(component, Tool)]


def registered_extensions(server: FastMCP) -> Mapping[str, Any]:
    """Return a read-only view of the server extensions registered on *server*."""
    extensions = getattr(server, "_extensions", None)
    if not isinstance(extensions, dict):
        raise RuntimeError(_LAYOUT_ERROR)
    return dict(extensions)


async def resolve_called_tool(server: FastMCP, name: str) -> Tool | None:
    """Resolve a ``tools/call`` name the way FastMCP 4.0.10 dispatches it.

    Display names resolve through ``get_tool``; MCP Apps callbacks addressed
    by their hashed backend name (``<digest>_<local name>``) resolve through
    ``get_tool_by_hash``, mirroring ``FastMCP.call_tool``. Returns ``None`` for
    unknown names so the framework reports them itself.
    """
    from fastmcp.server.providers.addressing import parse_hashed_backend_name

    tool = await server.get_tool(name)
    if tool is not None:
        return tool
    hashed = parse_hashed_backend_name(name)
    if hashed is None:
        return None
    digest, local_name = hashed
    return await server.get_tool_by_hash(digest, local_name)
