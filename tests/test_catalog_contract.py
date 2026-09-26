"""W0 contract test: the composed MCP catalog must match its committed snapshot.

Regenerates the tool/resource/resource-template/prompt catalog in-process,
via ``tests/contracts/catalog_snapshot.py`` -- the exact same code that wrote
``tests/contracts/catalog_default.json`` and
``tests/contracts/catalog_all_optional.json`` -- and asserts byte-for-byte
equality. On a mismatch it prints a unified diff of the two renderings so a
reviewer can see precisely what changed (a new/removed/renamed tool, a
schema edit, a changed annotation, etc.) without having to diff the raw
files by hand.

This test is deterministic, offline, and does not require Google
credentials, Redis, or network access: it only calls FastMCP's
``*/list`` protocol methods against an in-memory client, never a tool that
performs I/O. See the module docstring of ``tests/contracts/catalog_snapshot.py``
for why the resulting catalog is stable across machines and runs.

To intentionally update the committed snapshots after a deliberate,
reviewed catalog change, either run:

    UPDATE_CATALOG_SNAPSHOTS=1 uv run pytest tests/test_catalog_contract.py

or regenerate them directly with:

    uv run python scripts/snapshot_catalog.py

Both paths go through the same ``catalog_snapshot.render``/``build_catalog``
helpers, so they always produce identical output.
"""

from __future__ import annotations

import difflib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from contracts.catalog_snapshot import CONFIGS, SNAPSHOT_DIR, build_catalog, render  # noqa: E402


@pytest.mark.parametrize("config_name", sorted(CONFIGS))
def test_catalog_matches_committed_snapshot(config_name: str) -> None:
    actual = render(build_catalog(config_name))
    path = SNAPSHOT_DIR / config_name

    if os.getenv("UPDATE_CATALOG_SNAPSHOTS", "").strip() == "1":
        path.write_text(actual, encoding="utf-8")
        pytest.skip(f"Regenerated {path} because UPDATE_CATALOG_SNAPSHOTS=1.")

    expected = path.read_text(encoding="utf-8")
    if actual == expected:
        return

    diff = "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=f"{path.name} (committed)",
            tofile=f"{path.name} (regenerated)",
        )
    )
    pytest.fail(
        f"The live MCP catalog for {config_name!r} no longer matches the "
        "committed contract snapshot. If this change is intentional and "
        "reviewed, regenerate it with "
        "`UPDATE_CATALOG_SNAPSHOTS=1 uv run pytest tests/test_catalog_contract.py` "
        f"or `uv run python scripts/snapshot_catalog.py`.\n\n{diff}"
    )
