"""Prove the Prefab picker resource is built once per process, not per request.

FastMCP 4.0.10 rebuilds the whole picker ``TextResource`` — including a fresh
read of the bundled Prefab renderer HTML (~6.6 MB with
``PREFAB_BUNDLED_RENDERER=1``) — on every ``resources/list``/``resources/read``
(see ``mcp_google_workspace.common.prefab_render_cache`` for the mechanism and
why no supported FastMCP hook exists to avoid it). This pins that the adapter
patched in by ``file_uploads.configure_prefab_renderer()`` actually caches
across independent client calls.
"""

from __future__ import annotations

import anyio
import pytest
from fastmcp import Client
from fastmcp.server.providers import prefab_synthesis

from mcp_google_workspace.common import prefab_render_cache
from mcp_google_workspace.server import workspace_mcp


async def _list_and_read_twice() -> tuple[list, list]:
    async with Client(workspace_mcp) as client:
        tools = await client.list_tools()
        picker = next(tool for tool in tools if tool.name == "files_file_manager")
        uri = picker.meta["ui"]["resourceUri"]

        await client.list_resources()
        stats_after_first_list = prefab_render_cache.cache_stats()

        await client.list_resources()
        await client.read_resource(uri)
        await client.read_resource(uri)
        stats_after_more = prefab_render_cache.cache_stats()
    return stats_after_first_list, stats_after_more


def test_cache_is_installed_by_importing_file_uploads() -> None:
    # Importing mcp_google_workspace.server pulls in file_uploads, which
    # configures the renderer and installs the cache at import time.
    assert prefab_render_cache.is_installed()


def test_repeated_list_and_read_hit_the_cache_instead_of_rebuilding() -> None:
    prefab_render_cache.reset_cache()
    first, more = anyio.run(_list_and_read_twice)

    # Exactly one real build for the one prefab tool (files_file_manager),
    # produced by the first resources/list call.
    assert first["builds"] == 1
    assert first["hits"] == 0

    # Two more list/read calls after that must all be cache hits: no new
    # builds, only accumulated hits.
    assert more["builds"] == 1
    assert more["hits"] > first["hits"]


def test_cache_key_changes_when_renderer_env_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    prefab_render_cache.reset_cache()

    async def _list_once() -> None:
        async with Client(workspace_mcp) as client:
            await client.list_resources()

    anyio.run(_list_once)
    stats = prefab_render_cache.cache_stats()
    assert stats["builds"] == 1

    # Simulating a different resolved renderer configuration must produce a
    # cache miss (a different key), not a stale cached HTML payload.
    monkeypatch.setenv("PREFAB_BUNDLED_RENDERER", "")
    monkeypatch.setenv("PREFAB_RENDERER_URL", "http://localhost:4173")
    anyio.run(_list_once)
    stats = prefab_render_cache.cache_stats()
    assert stats["builds"] == 2


def test_install_is_idempotent_and_pins_fastmcp_layout() -> None:
    # Calling install twice must not double-patch or raise.
    prefab_render_cache.install_prefab_resource_cache()
    prefab_render_cache.install_prefab_resource_cache()
    assert callable(prefab_synthesis._build_resource_for_tool)


def test_missing_synthesis_hook_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pin the layout-drift guard the same way common/fastmcp_compat.py does:
    # if a future FastMCP release removes this private symbol, installing the
    # cache must fail loudly instead of silently no-op caching.
    monkeypatch.setattr(prefab_render_cache, "_patched", False)
    monkeypatch.setattr(prefab_synthesis, "_build_resource_for_tool", None, raising=False)
    with pytest.raises(RuntimeError, match="Re-validate"):
        prefab_render_cache.install_prefab_resource_cache()
