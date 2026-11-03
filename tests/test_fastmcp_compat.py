"""Pin the one module that reads FastMCP private registry state."""

from __future__ import annotations

import anyio
import pytest
from fastmcp import FastMCP

from mcp_google_workspace.common import fastmcp_compat
from mcp_google_workspace.file_uploads import workspace_file_upload
from mcp_google_workspace.gmail import gmail_mcp


def test_local_tools_match_the_public_async_listing() -> None:
    public = list(anyio.run(gmail_mcp.local_provider.list_tools))
    adapted = fastmcp_compat.local_tools(gmail_mcp)
    assert [tool.name for tool in adapted] == [tool.name for tool in public]
    # Live registered objects, so decoration applies to every dispatch path.
    assert all(a is b for a, b in zip(adapted, public))


def test_local_tools_cover_fastmcp_app_providers() -> None:
    public = anyio.run(workspace_file_upload.list_tools)
    assert {tool.name for tool in fastmcp_compat.local_tools(workspace_file_upload)} == {
        tool.name for tool in public
    }


def test_layout_changes_fail_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FastMCP("layout-probe")
    monkeypatch.setattr(server.local_provider, "_components", None)
    with pytest.raises(RuntimeError, match="Re-validate"):
        fastmcp_compat.local_tools(server)
    with pytest.raises(TypeError):
        fastmcp_compat.local_tools(object())  # type: ignore[arg-type]
