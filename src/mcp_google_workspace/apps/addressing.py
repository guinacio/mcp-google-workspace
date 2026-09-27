"""Dashboard UI resource addressing that holds in every composition.

The dashboard subserver declares its UI resource at :data:`DASHBOARD_UI_URI`
and its launch tools point ``_meta.ui.resourceUri`` at that same URI, so the
subserver resolves on its own. Mounting it with a namespace rewrites resource
URIs (``ui://dashboard-ui`` -> ``ui://apps/dashboard-ui``) but not tool
metadata; :class:`DashboardUiAddressing` applies the identical rewrite to the
launch tools' metadata so the composed declaration and resource agree.

There is exactly one address per composition. The flat ``ui/resourceUri``
key, the old ``ui://dashboard-ui``-as-alias registration and the plain
``text/html`` copy were removed in W6 (owner decision: no application-level
backward compatibility).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from fastmcp.server.transforms import GetToolNext, Transform

if TYPE_CHECKING:
    from fastmcp.tools.base import Tool
    from fastmcp.utilities.versions import VersionSpec

DASHBOARD_UI_URI = "ui://dashboard-ui"
DASHBOARD_UI_MIME = "text/html;profile=mcp-app"


def namespaced_ui_uri(uri: str, namespace: str | None) -> str:
    """The URI FastMCP's ``Namespace`` transform gives ``uri`` under ``namespace``."""
    if not namespace:
        return uri
    scheme, separator, path = uri.partition("://")
    if not separator:
        return uri
    return f"{scheme}://{namespace}/{path}"


def dashboard_ui_meta(uri: str = DASHBOARD_UI_URI) -> dict[str, Any]:
    """Canonical nested Apps metadata for a dashboard launch tool."""
    return {"ui": {"resourceUri": uri, "visibility": ["model", "app"]}}


class DashboardUiAddressing(Transform):
    """Point launch tools at the dashboard resource URI of this composition.

    Applied to the dashboard provider *inside* its mount namespace, so tool
    names are still the subserver's own while the URI is rewritten to the
    namespaced form the resource is listed and read under.
    """

    def __init__(self, namespace: str) -> None:
        self._namespace = namespace
        self._uri = namespaced_ui_uri(DASHBOARD_UI_URI, namespace)

    def __repr__(self) -> str:
        return f"DashboardUiAddressing({self._namespace!r})"

    @property
    def resource_uri(self) -> str:
        return self._uri

    def _rewrite(self, tool: Tool) -> Tool:
        meta = tool.meta or {}
        ui = meta.get("ui")
        if not isinstance(ui, dict) or ui.get("resourceUri") != DASHBOARD_UI_URI:
            return tool
        return tool.model_copy(
            update={"meta": {**meta, "ui": {**ui, "resourceUri": self._uri}}}
        )

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [self._rewrite(tool) for tool in tools]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        tool = await call_next(name, version=version)
        return self._rewrite(tool) if tool is not None else None
