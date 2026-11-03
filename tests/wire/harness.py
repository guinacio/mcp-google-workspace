"""Shared helpers for golden wire-contract fixtures.

``tests/wire/test_modern_wire.py`` and ``tests/wire/test_legacy_wire.py``
capture small, curated views of real raw JSON-RPC exchanges -- over raw HTTP
(``starlette.testclient.TestClient``, exactly like ``tests/test_http_wire.py``)
and over the in-memory FastMCP transport that stands in for stdio (see its
module docstring for why: ``mcp.shared.memory`` speaks the same
``SessionMessage``/JSON-RPC wire format FastMCP uses for a real stdio
subprocess, just without a subprocess -- and the MCPB bundle test
(``tests/test_bundle_runtime.py``) already exercises a real stdio
subprocess) -- and diff them against committed JSON fixtures under
``tests/wire/fixtures/``.

Each captured payload is curated on purpose: per the migration plan
(``docs/MIGRATION_FASTMCP4_MCP_2026-07-28.md`` W7), these fixtures must stay
small and must not embed the full ~140-tool catalog (``tests/test_catalog_contract.py``
already owns exact catalog contents byte-for-byte). Where a scenario touches a
listing, the fixture keeps the envelope shape (``resultType``, ``ttlMs``,
``cacheScope``, pagination cursor, tool/resource counts) plus one or two named
entries, not the whole array.

Nondeterministic fields (server-minted ids, timestamps, sealed continuation
state, view/upload handles) are replaced with stable placeholders by
``normalize()`` before comparison, exactly as ``tests/test_catalog_contract.py``
normalizes nothing (the catalog has none) and
``tests/contracts/catalog_snapshot.py`` documents why. Regenerate every
fixture deliberately with::

    UPDATE_WIRE_FIXTURES=1 uv run pytest tests/wire
"""

from __future__ import annotations

import difflib
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

MODERN = "2026-07-28"
ACCEPT = "application/json, text/event-stream"

_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
)

# Field names whose *values* are server-minted per run/process and must be
# masked regardless of where they appear in the payload.
_VOLATILE_KEYS: dict[str, str] = {
    "requestState": "<SEALED_STATE>",
    "request_state": "<SEALED_STATE>",
    "operationId": "<OPERATION_ID>",
    "operation_id": "<OPERATION_ID>",
    "generated_at_utc": "<TIMESTAMP>",
    "fetched_at": "<TIMESTAMP>",
    "uploaded_at": "<TIMESTAMP>",
    "handle": "<VIEW_HANDLE>",
}
_VOLATILE_HEADER_KEYS = {"mcp-session-id"}


def normalize(value: Any) -> Any:
    """Recursively replace nondeterministic fields with stable placeholders."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in _VOLATILE_KEYS and item is not None:
                out[key] = _VOLATILE_KEYS[key]
            else:
                out[key] = normalize(item)
        return out
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, str) and _TIMESTAMP_RE.match(value):
        return "<TIMESTAMP>"
    return value


def normalize_headers(headers: dict[str, str]) -> dict[str, str]:
    """Mask nondeterministic response headers (e.g. the legacy session id)."""
    out: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in _VOLATILE_HEADER_KEYS and value:
            out[key] = "<SESSION_ID>"
        else:
            out[key] = value
    return out


def modern_request(
    client: Any,
    request_id: int,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    name: str | None = None,
    capabilities: dict[str, Any] | None = None,
    version: str | None = MODERN,
) -> Any:
    """POST one raw modern (2026-07-28) JSON-RPC request. Mirrors ``test_http_wire.py``.

    ``version=None`` omits ``MCP-Protocol-Version`` entirely (the "missing
    version" scenario); any other string is sent verbatim, including an
    unsupported one.
    """
    headers = {"Accept": ACCEPT, "Content-Type": "application/json"}
    if version is not None:
        headers["MCP-Protocol-Version"] = version
    headers["Mcp-Method"] = method
    if name is not None:
        headers["Mcp-Name"] = name
    body_params = dict(params or {})
    if version is not None:
        body_params["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": version,
            "io.modelcontextprotocol/clientInfo": {"name": "wire-test", "version": "0"},
            "io.modelcontextprotocol/clientCapabilities": capabilities or {},
        }
    return client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": body_params},
    )


def assert_matches_fixture(name: str, actual: dict[str, Any]) -> None:
    """Diff *actual* (already curated/normalized) against a committed fixture.

    Supports ``UPDATE_WIRE_FIXTURES=1``, exactly like
    ``UPDATE_CATALOG_SNAPSHOTS=1`` for ``tests/test_catalog_contract.py``.
    """
    path = FIXTURE_DIR / f"{name}.json"
    rendered = json.dumps(actual, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    if os.getenv("UPDATE_WIRE_FIXTURES", "").strip() == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8", newline="\n")
        pytest.skip(f"Regenerated {path} because UPDATE_WIRE_FIXTURES=1.")

    if not path.exists():
        raise AssertionError(
            f"Missing wire fixture {path}. Generate it with "
            "`UPDATE_WIRE_FIXTURES=1 uv run pytest tests/wire`."
        )
    expected = path.read_text(encoding="utf-8")
    if rendered == expected:
        return

    diff = "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=f"{name}.json (committed)",
            tofile=f"{name}.json (actual)",
        )
    )
    raise AssertionError(
        f"Wire fixture {name!r} no longer matches the committed contract. If "
        "this is an intentional, reviewed protocol/behavior change, "
        "regenerate it with `UPDATE_WIRE_FIXTURES=1 uv run pytest tests/wire`."
        f"\n\n{diff}"
    )
