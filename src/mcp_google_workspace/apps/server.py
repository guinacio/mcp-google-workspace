"""Apps FastMCP subserver."""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.providers.fastmcp_provider import FastMCPProvider

from ..common.component_annotations import apply_default_tool_annotations
from .addressing import DashboardUiAddressing
from .operations import operation_catalog
from .resources import register_resources
from .state import DashboardViewService
from .tools import register_tools


def create_apps_server(views: DashboardViewService | None = None) -> FastMCP:
    """Build the dashboard subserver over ``views`` (default: configured store).

    Every instance built over the same app-state backend sees the same views,
    which is how separate replicas share dashboard state.
    """
    server = FastMCP(
        name="apps-mcp",
        instructions=(
            "Workspace dashboard MCP app layer that composes "
            "calendar, inbox, and actionable scheduling workflows."
        ),
    )
    register_tools(server, views, operation_catalog(server))
    register_resources(server)
    apply_default_tool_annotations(server)
    return server


def mount_apps_dashboard(root: FastMCP, server: FastMCP, *, namespace: str = "apps") -> None:
    """Mount a dashboard server so its UI address and operation names compose.

    Equivalent to ``root.mount(server, namespace=namespace)`` plus:

    * the launch tools' ``_meta.ui.resourceUri`` is rewritten to the namespaced
      resource URI (``ui://<namespace>/dashboard-ui``) that the mount serves;
    * the server's operation manifest resolves tool names against ``root``.

    Use it instead of ``mount`` for the dashboard; a plain ``mount`` would
    advertise a resource URI the composition does not serve.
    """
    provider = FastMCPProvider(server).wrap_transform(DashboardUiAddressing(namespace))
    root.add_provider(provider, namespace=namespace)
    operation_catalog(server).attach(root, namespace)


apps_mcp = create_apps_server()
