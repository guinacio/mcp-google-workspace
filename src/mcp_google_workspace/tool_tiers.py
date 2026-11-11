"""Tool tiers: how much of the catalog a deployment publishes.

``MCP_TOOL_TIER`` selects one of three nested tiers:

* ``core`` (default): the everyday tools of the core services. Raw Google
  ``batchUpdate`` passthroughs, Gmail account settings, shared-drive
  visibility and the MCP Apps diagnostics are left out.
* ``extended``: core plus those tools.
* ``complete``: extended plus every optional integration (Apps dashboard,
  Chat, Keep, Meet, and Gemini when ``GEMINI_API_KEY`` is set) whose
  ``ENABLE_*`` flag is not explicitly false.

Tools outside the active tier are not registered for clients at all: they are
absent from ``tools/list`` and a call to one is an unknown tool.

A tier can also gate one value of a core tool's parameter (``format="raw"`` on
the Sheets, Slides and Forms reads, the read side of the raw ``batchUpdate``
tools). A published schema is the same in every tier, so the value stays in it
and :func:`require_extended_tier` refuses it in the tool body, before any
Google request, with ``invalid_input`` naming ``MCP_TOOL_TIER``.
"""

from __future__ import annotations

import os
from typing import Final, Literal, cast

from .common.errors import RecoverableToolError

ToolTier = Literal["core", "extended", "complete"]

TOOL_TIERS: Final[tuple[ToolTier, ...]] = ("core", "extended", "complete")

#: Published names of the tools the core tier leaves out.
EXTENDED_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "docs_batch_update_document",
        "drive_update_drive",
        "forms_batch_update_form",
        "get_mcp_apps_diagnostics",
        "gmail_manage_filters",
        "gmail_manage_forwarding",
        "gmail_manage_vacation",
        "sheets_batch_update_spreadsheet",
        "slides_batch_update_presentation",
    }
)


def tool_tier() -> ToolTier:
    """The active tier from ``MCP_TOOL_TIER`` (default ``core``)."""
    value = os.getenv("MCP_TOOL_TIER", "core").strip().lower() or "core"
    if value not in TOOL_TIERS:
        raise ValueError(f"MCP_TOOL_TIER must be one of {', '.join(TOOL_TIERS)}; got {value!r}.")
    return cast(ToolTier, value)


def includes_extended_tools() -> bool:
    return tool_tier() != "core"


def optional_namespaces_default_on() -> bool:
    """Whether optional integrations are on unless their flag says otherwise."""
    return tool_tier() == "complete"


def hidden_tools() -> frozenset[str]:
    """Published tool names the active tier leaves out."""
    return frozenset() if includes_extended_tools() else EXTENDED_TOOLS


def require_extended_tier(field: str, value: str, *, instead: str) -> None:
    """Refuse ``field=value`` below the extended tier; ``instead`` says what works in core."""
    if includes_extended_tools():
        return
    message = (
        f"{field} '{value}' needs the extended tool tier and this server runs MCP_TOOL_TIER={tool_tier()}; "
        f"the deployment can set MCP_TOOL_TIER=extended (or complete). {instead}"
    )
    raise RecoverableToolError(
        "invalid_input",
        message,
        required_action={"action": "correct_arguments", "field_errors": [{"field": field, "message": message}]},
        details={"required_tier": "extended", "tool_tier": tool_tier()},
    )
