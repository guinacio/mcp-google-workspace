"""The Apps dashboard calls only published tools with published argument names.

The view (``apps/ui/src``) names its calls by operation id; the server's
operation manifest (``apps/operations.py``) maps each id to a tool, and
``OPERATION_ARGUMENTS`` in ``apps/ui/src/operations.ts`` lists every argument
the view sends to it (the TypeScript call sites are typed against that table,
so ``npm run typecheck`` rejects any other name). This test checks the table and
the view's read fallbacks against the committed catalog snapshots, which
``tests/test_catalog_contract.py`` keeps equal to the live catalog: a retired
tool or a removed or renamed argument fails here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from contracts.catalog_snapshot import SNAPSHOT_DIR
from contracts.retired_tools import RETIRED_TOOLS
from mcp_google_workspace.apps.operations import DASHBOARD_OPERATIONS

UI_OPERATIONS = (
    Path(__file__).resolve().parents[1] / "src/mcp_google_workspace/apps/ui/src/operations.ts"
)


def _ts_table(source: str, start: str, end: str) -> dict[str, list[str]]:
    """``{key: [string, ...]}`` entries of one ``const`` object literal in operations.ts."""
    match = re.search(re.escape(start) + r"(.*?)" + re.escape(end), source, re.DOTALL)
    assert match, f"{start!r} not found in {UI_OPERATIONS}"
    entries = re.findall(r"(\w+):\s*\[(.*?)\]", match.group(1), re.DOTALL)
    return {key: re.findall(r'"([^"]+)"', values) for key, values in entries}


def _catalog(name: str) -> dict[str, dict[str, Any]]:
    data = json.loads((SNAPSHOT_DIR / name).read_text(encoding="utf-8"))
    return {tool["name"]: tool for tool in data["tools"]}


SOURCE = UI_OPERATIONS.read_text(encoding="utf-8")
ARGUMENTS = _ts_table(SOURCE, "export const OPERATION_ARGUMENTS = {", "} as const")
READ_FALLBACKS = _ts_table(SOURCE, "const READ_FALLBACKS: Record<ReadOperation, readonly string[]> = {", "};")
COMPLETE = _catalog("catalog_all_optional.json")
CORE = _catalog("catalog_default.json")


def _published(operation: str) -> str:
    spec = DASHBOARD_OPERATIONS[operation]
    return f"{spec.namespace}_{spec.tool}"


def test_view_and_server_name_the_same_operations() -> None:
    assert set(ARGUMENTS) == set(DASHBOARD_OPERATIONS)
    assert set(READ_FALLBACKS) <= set(DASHBOARD_OPERATIONS)
    assert READ_FALLBACKS, "READ_FALLBACKS could not be parsed"


@pytest.mark.parametrize("operation", sorted(DASHBOARD_OPERATIONS))
def test_operation_tool_is_published_and_accepts_every_argument_the_view_sends(operation: str) -> None:
    name = _published(operation)
    assert name not in RETIRED_TOOLS, f"{operation} targets retired tool {name}"
    assert name in COMPLETE, f"{operation} targets {name}, which is not published"
    if DASHBOARD_OPERATIONS[operation].namespace != "apps":
        # The dashboard can run beside the core tier only: its Google tools must be core.
        assert name in CORE, f"{operation} targets {name}, which the core tier does not publish"
    schema = COMPLETE[name]["inputSchema"]
    accepted = set(schema.get("properties", {}))
    sent = set(ARGUMENTS[operation])
    assert sent <= accepted, f"{operation} -> {name}: unknown arguments {sorted(sent - accepted)}"
    missing = set(schema.get("required", [])) - sent
    assert not missing, f"{operation} -> {name}: never sends required {sorted(missing)}"


@pytest.mark.parametrize("operation", sorted(READ_FALLBACKS))
def test_read_fallback_names_are_the_composed_then_the_standalone_name(operation: str) -> None:
    spec = DASHBOARD_OPERATIONS[operation]
    names = READ_FALLBACKS[operation]
    assert names[0] == _published(operation)
    for name in names[1:]:
        # A dashboard served on its own registers its tools under their local names.
        assert spec.namespace == "apps" and name == spec.tool
