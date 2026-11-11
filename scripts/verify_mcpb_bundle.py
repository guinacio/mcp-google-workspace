#!/usr/bin/env python3
"""
Verify a built .mcpb bundle actually runs as a standalone artifact.

Builds (or reuses) the .mcpb archive, extracts it into an isolated
directory, and drives the extracted copy's stdio entrypoint through a real
MCP client -- proving the packaged artifact is self-sufficient (its own
pyproject.toml/uv.lock, no reliance on files ``.mcpbignore`` excludes) and
not just the repository checkout `tests/test_bundle_runtime.py` already
exercises `uv run src/mcp_google_workspace/bundle_entry.py` against.

Lifecycle covered (W7b, docs/MIGRATION_FASTMCP4_MCP_2026-07-28.md section 8
"Packaging"): start, list tools, call a safe tool, the Workspace Files
picker's store/list/read/delete callbacks across independent MCP 2026-07-28
requests, close the client, and reconnect to the same kept-alive subprocess.

Usage:
  uv run python scripts/verify_mcpb_bundle.py
  uv run python scripts/verify_mcpb_bundle.py --bundle path/to/existing.mcpb
  uv run python scripts/verify_mcpb_bundle.py --work-dir /tmp/mcpb-check
"""

from __future__ import annotations

import argparse
import base64
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import anyio
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.server.providers.addressing import hash_tool

ROOT = Path(__file__).resolve().parent.parent


def _log(message: str) -> None:
    print(f"[verify-mcpb] {message}", flush=True)


def _build_bundle() -> Path:
    _log("Building .mcpb from the current checkout (scripts/build_mcpb.py)...")
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_mcpb.py")],
        cwd=ROOT,
        check=True,
    )
    candidates = sorted((ROOT / "dist").glob("*.mcpb"))
    if not candidates:
        raise RuntimeError("scripts/build_mcpb.py did not produce a .mcpb archive.")
    return candidates[-1]


def _extract_bundle(bundle: Path, destination: Path) -> Path:
    _log(f"Extracting {bundle.name} into {destination} ...")
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle) as archive:
        archive.extractall(destination)
    entrypoint = destination / "src" / "mcp_google_workspace" / "bundle_entry.py"
    if not entrypoint.is_file():
        raise RuntimeError(f"Extracted bundle is missing {entrypoint}.")
    if not (destination / "pyproject.toml").is_file():
        raise RuntimeError("Extracted bundle is missing pyproject.toml; it cannot run standalone.")
    return destination


async def _run_lifecycle(bundle_dir: Path) -> None:
    transport = StdioTransport(
        command="uv",
        args=["run", "python", "-m", "mcp_google_workspace.bundle_entry"],
        cwd=str(bundle_dir),
        keep_alive=True,
    )
    store_tool = f"{hash_tool('Workspace Files', 'store_files')}_store_files"
    delete_tool = f"{hash_tool('Workspace Files', 'delete_file')}_delete_file"
    content = b"mcpb bundle lifecycle check"

    _log("Starting the extracted bundle over stdio and listing tools ...")
    async with Client(transport) as client:
        assert client.protocol_version == "2026-07-28", client.protocol_version
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        # Progressive (BM25) tool discovery is opt-in (MCP_TOOL_SEARCH=on), so
        # the bundle lists the complete catalog with no search_tools/call_tool
        # (matching tests/test_bundle_runtime.py). Hashed app-callback tools
        # like store_tool/delete_tool stay directly callable by name below.
        for required in ("get_google_connection_status", "gmail_search_emails", "uploads_file_manager"):
            assert required in names, f"missing tool {required!r} in extracted bundle catalog"
        for absent in ("search_tools", "call_tool"):
            assert absent not in names, f"unexpected tool-search tool {absent!r} in bundle catalog"

        _log("Calling a safe tool (resolve_workspace_resource, parse only) ...")
        safe = await client.call_tool(
            "resolve_workspace_resource", {"uri": "gcal-event:///bundle-check"}, raise_on_error=False
        )
        assert safe.is_error is False, safe.content

        _log("Exercising the picker store/list/read/delete lifecycle ...")
        stored = await client.call_tool(
            store_tool,
            {
                "files": [
                    {
                        "name": "bundle-check.txt",
                        "size": len(content),
                        "type": "text/plain",
                        "data": base64.b64encode(content).decode("ascii"),
                    }
                ]
            },
        )
        assert stored.is_error is False, stored.content
        upload_id = stored.structured_content["result"][0]["upload_id"]

        listed = await client.call_tool("uploads_list_files", {})
        assert [item["upload_id"] for item in listed.structured_content["files"]] == [upload_id]

        read = await client.call_tool("uploads_read_file", {"name": upload_id})
        assert "bundle lifecycle check" in str(read.structured_content)

        deleted = await client.call_tool(delete_tool, {"name": upload_id})
        assert deleted.structured_content == {"status": "deleted", "name": upload_id}

        after = await client.call_tool("uploads_list_files", {})
        assert after.structured_content["files"] == []

    _log("Client closed. Reconnecting to the same kept-alive subprocess ...")
    async with Client(transport) as client:
        assert client.protocol_version == "2026-07-28", client.protocol_version
        again = await client.call_tool(
            "resolve_workspace_resource", {"uri": "gcal-event:///bundle-check"}, raise_on_error=False
        )
        assert again.is_error is False, again.content
        # The picker store above was already deleted; reconnecting must not
        # resurrect it (no connection-scoped state, no accidental replay).
        still_empty = await client.call_tool("uploads_list_files", {})
        assert still_empty.structured_content["files"] == []

    await transport.disconnect()
    _log("MCPB bundle lifecycle check passed: start, list, safe call, "
         "picker store/list/read/delete, close, reconnect.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=None, help="Reuse an existing .mcpb instead of building one.")
    parser.add_argument("--work-dir", type=Path, default=None, help="Directory to extract into (default: a temp dir).")
    args = parser.parse_args()

    bundle = args.bundle or _build_bundle()
    work_dir = args.work_dir
    cleanup = work_dir is None
    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="mcpb-verify-"))
    try:
        bundle_dir = _extract_bundle(bundle, work_dir / "extracted")
        anyio.run(_run_lifecycle, bundle_dir)
    finally:
        if cleanup:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
