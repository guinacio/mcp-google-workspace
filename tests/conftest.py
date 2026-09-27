"""Suite-wide configuration for the FastMCP 4 / MCP SDK 2 test run.

1. Disable FastMCP's camelCase read shims *before* FastMCP is imported, so any
   remaining SDK 1 style attribute read (``tool.inputSchema``,
   ``annotations.readOnlyHint``) fails the suite instead of passing with a
   deprecation warning. CI exports the same variable.
2. Register the Tasks extension on every namespace subserver that declares
   ``task=True`` tools. Production registers exactly one extension, on the
   root ``workspace_mcp`` (see ``common/task_backend.py``); mounted children
   defer to it. Tests that connect a client directly to a subserver make that
   subserver the runtime root, and FastMCP 4 refuses to serve task tools
   without an extension, so those runnable test roots need their own
   (in-memory) one.
"""

from __future__ import annotations

import os

os.environ["FASTMCP_MCP_CAMELCASE_COMPAT"] = "false"

import fastmcp  # noqa: E402

from mcp_google_workspace.apps import apps_mcp  # noqa: E402
from mcp_google_workspace.calendar import calendar_mcp  # noqa: E402
from mcp_google_workspace.chat import chat_mcp  # noqa: E402
from mcp_google_workspace.common.fastmcp_compat import local_tools  # noqa: E402
from mcp_google_workspace.common.task_backend import install_tasks_extension  # noqa: E402
from mcp_google_workspace.docs import docs_mcp  # noqa: E402
from mcp_google_workspace.drive import drive_mcp  # noqa: E402
from mcp_google_workspace.forms import forms_mcp  # noqa: E402
from mcp_google_workspace.gemini import gemini_mcp  # noqa: E402
from mcp_google_workspace.gmail import gmail_mcp  # noqa: E402
from mcp_google_workspace.keep import keep_mcp  # noqa: E402
from mcp_google_workspace.meet import meet_mcp  # noqa: E402
from mcp_google_workspace.people import people_mcp  # noqa: E402
from mcp_google_workspace.sheets import sheets_mcp  # noqa: E402
from mcp_google_workspace.slides import slides_mcp  # noqa: E402
from mcp_google_workspace.tasks import tasks_mcp  # noqa: E402

assert fastmcp.settings.mcp_camelcase_compat is False

SUBSERVERS = (
    apps_mcp,
    calendar_mcp,
    chat_mcp,
    docs_mcp,
    drive_mcp,
    forms_mcp,
    gemini_mcp,
    gmail_mcp,
    keep_mcp,
    meet_mcp,
    people_mcp,
    sheets_mcp,
    slides_mcp,
    tasks_mcp,
)

for _subserver in SUBSERVERS:
    if any(tool.task_config.supports_tasks() for tool in local_tools(_subserver)):
        install_tasks_extension(_subserver)


def pytest_configure(config) -> None:  # type: ignore[no-untyped-def]
    config.addinivalue_line(
        "markers",
        "fleet: Docker fleet qualification (deploy/fleet-test); skipped unless MCP_FLEET_TEST=1",
    )
