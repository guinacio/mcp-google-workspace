"""Shared logic for the W0 MCP catalog contract snapshots.

Both ``scripts/snapshot_catalog.py`` (regenerates the committed JSON files)
and ``tests/test_catalog_contract.py`` (asserts the live catalog still
matches them) import this module, so there is exactly one code path that
decides what a client sees and how it is serialized.

The catalog is built the same way production does: by composing
``mcp_google_workspace.server.workspace_mcp`` (see ``server.py``) and then
listing tools/resources/resource templates/prompts through an in-memory
``fastmcp.Client`` -- i.e. what a real MCP client observes over the
protocol, not FastMCP's private in-process registries. No Google
credentials are required; see ``tests/test_annotations_and_startup.py`` for
the existing regression test that startup/listing never touches
``get_credentials``.

Two catalogs are captured, matching docs/MIGRATION_FASTMCP4_MCP_2026-07-28.md
section 2:

* ``catalog_default.json`` -- no optional integrations enabled.
* ``catalog_all_optional.json`` -- Apps dashboard, Chat, Gemini, Keep, and
  Meet all enabled (mirrors ``tests/test_composition.py``'s
  ``test_composition_mounts_optional_namespaces_when_enabled``).

FastMCP's BM25 ``search_tools``/``call_tool`` progressive-discovery
transform (``mcp_google_workspace.tool_discovery``) is never installed on
``workspace_mcp`` itself in ``server.py``; only the HTTP/stdio entrypoints
(``server_http.py``, ``bundle_entry.py``) call
``tool_discovery.configure_tool_search`` on top of it. So the plain
composed server already exposes the *complete* catalog. To additionally
record what a client sees once discovery is turned on, each catalog also
carries a ``discovery_view`` list: the sorted tool names visible after
explicitly forcing ``MCP_TOOL_SEARCH=on`` and installing the transform on a
second, otherwise-identical server instance. That is deliberately the
"discovery on" case rather than the default "auto" heuristic (which decides
by client model name) -- "auto" is a client-detection policy, not a
protocol contract, and pinning it to "on" keeps the snapshot deterministic
regardless of ``MCP_CLIENT_MODEL``.

Determinism notes (what would otherwise make two runs disagree, and why it
does not apply here):

* Tool/resource/prompt *definitions* (names, schemas, descriptions,
  annotations, ``_meta``) are static at decorator time; nothing in this
  repository interpolates a timestamp, random id, session id, or absolute
  filesystem path into a definition. Those only appear in *call results*
  (e.g. ``fetched_at`` pagination wrapping, upload ids), which this module
  never invokes -- it only calls the ``*/list`` protocol methods.
* FastMCP Apps/Prefab addressing (``hash_tool``/``hashed_resource_uri``) is
  a plain SHA-256 of two static strings, so hashed tool/resource addresses
  are stable across machines and runs.
* All entries are sorted (by name/uri/uriTemplate) and the JSON is dumped
  with ``sort_keys=True``, so incidental dict-ordering differences can never
  show up as a diff.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any

import anyio
import mcp.types
from fastmcp import Client, FastMCP

import mcp_google_workspace.server as server_module
from mcp_google_workspace import tool_discovery

SNAPSHOT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SNAPSHOT_DIR.parent.parent

# Every environment variable read (directly or transitively) while composing
# workspace_mcp and while deciding whether to enable progressive discovery.
# Cleared before each build so one configuration/run can never leak into the
# next through a variable left set by the surrounding shell or a prior test.
_ENV_VARS = [
    "ENABLE_APPS_DASHBOARD",
    "ENABLE_CHAT",
    "ENABLE_GEMINI",
    "ENABLE_KEEP",
    "ENABLE_MEET",
    "GEMINI_API_KEY",
    "MCP_TOOL_SEARCH",
    "MCP_CLIENT_MODEL",
]

# Filenames double as the config's identity; write_snapshot() writes each
# dict to SNAPSHOT_DIR / <filename>.
CONFIGS: dict[str, dict[str, str]] = {
    "catalog_default.json": {},
    "catalog_all_optional.json": {
        "ENABLE_APPS_DASHBOARD": "true",
        "ENABLE_CHAT": "true",
        "ENABLE_GEMINI": "true",
        "GEMINI_API_KEY": "test-key",
        "ENABLE_KEEP": "true",
        "ENABLE_MEET": "true",
    },
}


def _apply_env(env: dict[str, str]) -> dict[str, str | None]:
    """Clear the tracked variables, then set ``env``. Returns the prior values."""
    previous: dict[str, str | None] = {name: os.environ.get(name) for name in _ENV_VARS}
    for name in _ENV_VARS:
        os.environ.pop(name, None)
    for name, value in env.items():
        os.environ[name] = value
    return previous


def _restore_env(previous: dict[str, str | None]) -> None:
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _reload_workspace_server() -> FastMCP:
    """Rebuild ``workspace_mcp`` from the currently-set environment.

    ``server.py`` reads the optional-integration flags at import time (see
    ``is_apps_dashboard_enabled()`` etc. guarding each ``mount()`` call), so
    a fresh ``importlib.reload`` is required per configuration -- the same
    approach ``tests/test_composition.py``'s ``_reload_workspace`` helper
    already uses and that the existing suite exercises repeatedly per
    session.
    """
    importlib.reload(server_module)
    return server_module.workspace_mcp


def _dump_wire(model: object | None) -> dict[str, Any] | None:
    """Dump an SDK model in its wire (camelCase, ``by_alias``) form."""
    if model is None:
        return None
    assert isinstance(
        model,
        mcp.types.ToolAnnotations | mcp.types.Annotations,
    )
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def _tool_entry(tool: mcp.types.Tool) -> dict[str, Any]:
    # Python reads use SDK 2 snake_case attributes; snapshot keys stay in the
    # camelCase wire form so the contract diff is meaningful across SDKs.
    return {
        "name": tool.name,
        "title": tool.title,
        "description": tool.description,
        "inputSchema": tool.input_schema,
        "outputSchema": tool.output_schema,
        "annotations": _dump_wire(tool.annotations),
        "meta": tool.meta,
    }


def _resource_entry(resource: mcp.types.Resource) -> dict[str, Any]:
    return {
        "uri": str(resource.uri),
        "name": resource.name,
        "title": resource.title,
        "description": resource.description,
        "mimeType": resource.mime_type,
        "annotations": _dump_wire(resource.annotations),
        "meta": resource.meta,
    }


def _template_entry(template: mcp.types.ResourceTemplate) -> dict[str, Any]:
    return {
        "uriTemplate": template.uri_template,
        "name": template.name,
        "title": template.title,
        "description": template.description,
        "mimeType": template.mime_type,
        "annotations": _dump_wire(template.annotations),
        "meta": template.meta,
    }


def _prompt_entry(prompt: mcp.types.Prompt) -> dict[str, Any]:
    arguments = sorted(prompt.arguments or [], key=lambda argument: argument.name)
    return {
        "name": prompt.name,
        "title": prompt.title,
        "description": prompt.description,
        "arguments": [
            {
                "name": argument.name,
                "description": argument.description,
                "required": argument.required,
            }
            for argument in arguments
        ],
        "meta": prompt.meta,
    }


async def _collect_full_catalog(server: FastMCP) -> dict[str, Any]:
    async with Client(server) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        prompts = await client.list_prompts()

    return {
        "tools": sorted((_tool_entry(t) for t in tools), key=lambda e: e["name"]),
        "resources": sorted((_resource_entry(r) for r in resources), key=lambda e: e["uri"]),
        "resource_templates": sorted(
            (_template_entry(t) for t in templates), key=lambda e: e["uriTemplate"]
        ),
        "prompts": sorted((_prompt_entry(p) for p in prompts), key=lambda e: e["name"]),
        "counts": {
            "tools": len(tools),
            "resources": len(resources),
            "resource_templates": len(templates),
            "prompts": len(prompts),
        },
    }


async def _collect_discovery_view(server: FastMCP) -> list[str]:
    tool_discovery.configure_tool_search(server)
    async with Client(server) as client:
        tools = await client.list_tools()
    return sorted(tool.name for tool in tools)


def build_catalog(config_name: str) -> dict[str, Any]:
    """Build the full catalog dict for one named configuration in ``CONFIGS``.

    Two fresh ``workspace_mcp`` instances are built under the hood: one left
    untouched (the complete catalog) and one with progressive discovery
    forced on (to record ``discovery_view``). Each is built and torn down
    under its own environment scope so the two never interact and the
    ambient environment is restored afterwards.
    """
    env = CONFIGS[config_name]

    previous = _apply_env(env)
    try:
        catalog = anyio.run(_collect_full_catalog, _reload_workspace_server())
    finally:
        _restore_env(previous)

    previous = _apply_env({**env, "MCP_TOOL_SEARCH": "on"})
    try:
        catalog["discovery_view"] = anyio.run(
            _collect_discovery_view, _reload_workspace_server()
        )
    finally:
        _restore_env(previous)
        # Leave the shared module rebuilt from the ambient environment, without
        # the discovery transform, so later tests never inherit this state.
        _reload_workspace_server()

    return catalog


def render(data: dict[str, Any]) -> str:
    """Serialize a catalog dict the one canonical way (stable key order)."""
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def write_snapshot(config_name: str, data: dict[str, Any]) -> Path:
    path = SNAPSHOT_DIR / config_name
    path.write_text(render(data), encoding="utf-8", newline="\n")
    return path


def generate_all() -> dict[str, dict[str, Any]]:
    return {config_name: build_catalog(config_name) for config_name in CONFIGS}
