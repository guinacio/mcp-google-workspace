#!/usr/bin/env python3
"""Regenerate the committed MCP catalog contract snapshots (W0 baseline).

Rebuilds ``mcp_google_workspace.server.workspace_mcp`` under the default and
all-optional-integrations configurations, lists its tools/resources/resource
templates/prompts through an in-memory FastMCP client (the same way a real
MCP client sees them), and overwrites:

    tests/contracts/catalog_default.json
    tests/contracts/catalog_all_optional.json

Run from the project root, with no Google credentials or network access
required:

    uv run python scripts/snapshot_catalog.py

``tests/test_catalog_contract.py`` asserts the live catalog still matches
these files on every test run; regenerate them here (or via that test's
``UPDATE_CATALOG_SNAPSHOTS=1`` mode) only after a deliberate, reviewed
catalog change.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from contracts.catalog_snapshot import CONFIGS, build_catalog, write_snapshot  # noqa: E402


def main() -> None:
    for config_name in CONFIGS:
        data = build_catalog(config_name)
        path = write_snapshot(config_name, data)
        counts = data["counts"]
        print(
            f"{config_name}: tools={counts['tools']} resources={counts['resources']} "
            f"resource_templates={counts['resource_templates']} "
            f"prompts={counts['prompts']} "
            f"discovery_view={len(data['discovery_view'])} -> {path}"
        )


if __name__ == "__main__":
    main()
