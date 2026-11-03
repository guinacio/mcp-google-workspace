#!/usr/bin/env python3
"""Export real MCP Apps server metadata for the browser (Playwright) suite.

The sandbox-host tests must exercise what this server actually ships, not a
hand-written copy. This script composes ``workspace_mcp`` with the dashboard
enabled, lists it through an in-memory FastMCP client, and writes one JSON file:

* ``tools``: name -> ``_meta.ui.visibility`` for every tool (the test host
  rejects app calls to tools without ``"app"``, as the spec requires of hosts);
* ``dashboard``: the dashboard UI resource URI, MIME type and ``_meta.ui``;
* ``picker``: the Prefab file picker resource (URI, HTML, ``_meta.ui`` with its
  CSP) and a real ``files_file_manager`` tool result.

No Google credentials or network access are needed. Usage::

    uv run python scripts/export_apps_ui_fixtures.py <output.json>
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("FASTMCP_MCP_CAMELCASE_COMPAT", "false")
os.environ["ENABLE_APPS_DASHBOARD"] = "true"
os.environ.setdefault("MCP_TOOL_SEARCH", "off")

import anyio  # noqa: E402
from fastmcp import Client  # noqa: E402

from mcp_google_workspace.server import workspace_mcp  # noqa: E402


def _ui_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    ui = (meta or {}).get("ui")
    return ui if isinstance(ui, dict) else {}


async def _export() -> dict[str, Any]:
    async with Client(workspace_mcp) as client:
        tools = await client.list_tools()
        resources = {str(item.uri): item for item in await client.list_resources()}
        visibility = {
            tool.name: _ui_meta(tool.meta).get("visibility", ["model", "app"]) for tool in tools
        }
        launch = next(tool for tool in tools if tool.name == "apps_get_dashboard")
        dashboard_uri = _ui_meta(launch.meta)["resourceUri"]
        picker_tool = next(tool for tool in tools if tool.name == "files_file_manager")
        picker_uri = _ui_meta(picker_tool.meta)["resourceUri"]
        picker_html = (await client.read_resource(picker_uri))[0]
        picker_result = await client.call_tool("files_file_manager", {})
        return {
            "tools": visibility,
            "dashboard": {
                "uri": dashboard_uri,
                "mimeType": resources[dashboard_uri].mime_type,
                "ui": _ui_meta(resources[dashboard_uri].meta),
            },
            "picker": {
                "uri": picker_uri,
                "mimeType": resources[picker_uri].mime_type,
                "ui": _ui_meta(resources[picker_uri].meta),
                "html": getattr(picker_html, "text", ""),
                "toolResult": {
                    "content": [
                        item.model_dump(mode="json", by_alias=True, exclude_none=True)
                        for item in picker_result.content
                    ],
                    "structuredContent": picker_result.structured_content,
                    "isError": picker_result.is_error,
                },
            },
        }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: export_apps_ui_fixtures.py <output.json>")
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    data = anyio.run(_export)
    output.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    print(f"wrote {output} ({output.stat().st_size} bytes, {len(data['tools'])} tools)")


if __name__ == "__main__":
    main()
