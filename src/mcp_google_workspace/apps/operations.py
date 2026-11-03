"""Dashboard operation manifest: which tools this view may call, right now.

The manifest is derived from facts, never from guessed names:

* the tools actually registered in the composition that serves the dashboard
  (``FastMCP.get_tool`` on the composing server, which also sees tools the
  BM25 discovery transform hides from ``tools/list``);
* whether each tool is callable by an app (``_meta.ui.visibility`` includes
  ``"app"`` or is absent, the spec default);
* whether the calling principal holds the Google capability the operation
  needs. Remote principals are limited to their granted capabilities, exactly
  like ``CapabilityCatalogMiddleware``; the trusted local user obtains consent
  for the whole enabled catalog on first use, so no capability is withheld.

The manifest is a UI hint. Server-side authorization (principal-bound view
handles, Google grants, confirmation gates) stays authoritative for every call.
"""

from __future__ import annotations

import logging
import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from fastmcp.server.dependencies import get_access_token

from .view_models import DashboardOperation, build_operation_manifest

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.tools.base import Tool

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class OperationSpec:
    """Where an operation lives and which Google capability it needs."""

    namespace: str
    """``apps`` for the dashboard's own tools, else the integration namespace."""
    tool: str
    """Tool name inside its own server."""
    capability: str | None
    """Google capability required (``calendar``/``gmail``) or None."""


# Operation ids are the view's vocabulary (see apps/ui/src/operations.ts).
DASHBOARD_OPERATIONS: Final[dict[str, OperationSpec]] = {
    # Reads.
    "getDashboard": OperationSpec("apps", "get_dashboard", None),
    "getWeeklyCalendar": OperationSpec("apps", "get_weekly_calendar_view", "calendar"),
    "getEventDetail": OperationSpec("apps", "get_event_detail", "calendar"),
    "getEmailDetail": OperationSpec("apps", "get_email_detail", "gmail"),
    "getEmailAttachment": OperationSpec("apps", "get_email_attachment", "gmail"),
    "listCalendars": OperationSpec("calendar", "list_calendars", "calendar"),
    # Dashboard view-state writes (no Google scope; the view handle authorizes them).
    "patchState": OperationSpec("apps", "patch_state", None),
    "nextRange": OperationSpec("apps", "next_range", None),
    "prevRange": OperationSpec("apps", "prev_range", None),
    "today": OperationSpec("apps", "today", None),
    # Google writes.
    "respondToEvent": OperationSpec("calendar", "respond_to_event", "calendar"),
    "createEvent": OperationSpec("calendar", "create_event", "calendar"),
    "updateEvent": OperationSpec("calendar", "update_event", "calendar"),
    "deleteEvent": OperationSpec("calendar", "delete_event", "calendar"),
    "markEmailRead": OperationSpec("gmail", "mark_as_read", "gmail"),
    "markEmailUnread": OperationSpec("gmail", "mark_as_unread", "gmail"),
    "moveEmail": OperationSpec("gmail", "move_email", "gmail"),
    "deleteEmail": OperationSpec("gmail", "delete_email", "gmail"),
    "untrashEmail": OperationSpec("gmail", "untrash_email", "gmail"),
    "markEmailSpam": OperationSpec("gmail", "mark_as_spam", "gmail"),
    "markEmailNotSpam": OperationSpec("gmail", "mark_as_not_spam", "gmail"),
}


def app_callable(tool: Tool) -> bool:
    """Whether an MCP Apps host lets a view call ``tool`` (spec default: yes)."""
    ui = (tool.meta or {}).get("ui")
    if not isinstance(ui, dict) or "visibility" not in ui:
        return True
    visibility = ui.get("visibility")
    return isinstance(visibility, list) and "app" in visibility


def mutates(tool: Tool) -> bool:
    annotations = tool.annotations
    return not (annotations is not None and annotations.read_only_hint is True)


def granted_capabilities() -> frozenset[str] | None:
    """Google capabilities of the calling principal; None means "not restricted".

    Mirrors ``CapabilityCatalogMiddleware``: only authenticated remote callers
    are limited to their stored grant.
    """
    if get_access_token() is None:
        return None
    try:
        from ..auth.google_oauth import google_connection_status

        return frozenset(google_connection_status().get("granted_capabilities", []))
    except Exception:  # noqa: BLE001 - fail closed: no Google writes advertised
        LOGGER.warning("Could not read Google grants for the operation manifest.", exc_info=True)
        return frozenset()


class DashboardOperationCatalog:
    """Resolves operation ids to tools of the composition serving one dashboard server.

    A dashboard server instance serves one composition: on its own (tool names
    are its local names, and no Google integration tools exist), or mounted by
    :func:`mcp_google_workspace.apps.server.mount_apps_dashboard`, which
    attaches the composing root and the dashboard's namespace.
    """

    def __init__(self, server: FastMCP) -> None:
        self._server_ref = weakref.ref(server)
        self._root_ref: weakref.ReferenceType[FastMCP] | None = None
        self._namespace: str | None = None

    def attach(self, root: FastMCP, namespace: str | None) -> None:
        self._root_ref = weakref.ref(root)
        self._namespace = namespace or None

    def _composition(self) -> tuple[FastMCP | None, bool]:
        if self._root_ref is not None:
            root = self._root_ref()
            if root is not None:
                return root, True
        return self._server_ref(), False

    def _tool_name(self, spec: OperationSpec, composed: bool) -> str | None:
        if spec.namespace == "apps":
            if composed and self._namespace:
                return f"{self._namespace}_{spec.tool}"
            return spec.tool
        if not composed:
            return None  # Integration tools only exist in a composed server.
        return f"{spec.namespace}_{spec.tool}"

    async def manifest(self) -> dict[str, Any]:
        server, composed = self._composition()
        operations: dict[str, DashboardOperation] = {}
        if server is None:
            return build_operation_manifest(operations)
        granted = granted_capabilities()
        for operation_id, spec in DASHBOARD_OPERATIONS.items():
            if spec.capability is not None and granted is not None and spec.capability not in granted:
                continue
            name = self._tool_name(spec, composed)
            if name is None:
                continue
            tool = await server.get_tool(name)
            if tool is None or not app_callable(tool):
                continue
            operations[operation_id] = DashboardOperation(tool=name, mutates=mutates(tool))
        return build_operation_manifest(operations)


_CATALOGS: weakref.WeakKeyDictionary[FastMCP, DashboardOperationCatalog] = (
    weakref.WeakKeyDictionary()
)


def operation_catalog(server: FastMCP) -> DashboardOperationCatalog:
    """The catalog bound to one dashboard server instance (created on first use)."""
    catalog = _CATALOGS.get(server)
    if catalog is None:
        catalog = DashboardOperationCatalog(server)
        _CATALOGS[server] = catalog
    return catalog
