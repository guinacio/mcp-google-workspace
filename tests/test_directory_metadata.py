"""Directory/platform compatibility metadata: annotations, view domain, schema bounds, result docs."""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from fastmcp import Client, FastMCP

from mcp_google_workspace.common.output_field_docs import OUTPUT_FIELD_DOCS
from mcp_google_workspace.runtime import mcp_app_domain
from mcp_google_workspace.server import workspace_mcp
from mcp_google_workspace.tool_discovery import AnnotatedBM25SearchTransform


def _tools(server: FastMCP) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        async with Client(server) as client:
            return {tool.name: tool for tool in await client.list_tools()}

    return anyio.run(run)


def test_tool_search_synthetic_tools_declare_boolean_hints() -> None:
    server = FastMCP("probe")

    @server.tool
    def ping() -> str:
        return "pong"

    server.add_transform(AnnotatedBM25SearchTransform(search_tool_name="search_tools", call_tool_name="call_tool"))
    tools = _tools(server)
    search, call = tools["search_tools"].annotations, tools["call_tool"].annotations
    assert (search.read_only_hint, search.destructive_hint, search.idempotent_hint, search.open_world_hint) == (
        True, False, True, False,
    )
    # call_tool can reach any discoverable tool, including destructive ones.
    assert (call.read_only_hint, call.destructive_hint, call.idempotent_hint, call.open_world_hint) == (
        False, True, False, True,
    )
    assert "description" in tools["call_tool"].input_schema["properties"]["arguments"]


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"MCP_HTTP_BASE_URL": "https://googleworkspace.fastmcp.app/"}, "https://googleworkspace.fastmcp.app"),
        ({"MCP_HTTP_BASE_URL": "https://example.test/gw"}, "https://example.test"),
        ({"MCP_HTTP_BASE_URL": "https://a.test", "MCP_APP_DOMAIN": "https://views.test"}, "https://views.test"),
        ({"MCP_HTTP_BASE_URL": "http://localhost:8000"}, None),
        ({}, None),
    ],
)
def test_app_domain_is_the_public_https_origin(monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: str | None) -> None:
    for name in ("MCP_HTTP_BASE_URL", "MCP_APP_DOMAIN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert mcp_app_domain() == expected


BOUNDS = {
    ("calendar_search_events", "max_results"): (1, 2500),
    ("drive_list_files", "page_size"): (1, 1000),
    ("gmail_get_mail_digest", "max_items"): (1, 100),
    ("gmail_search_emails", "max_results"): (1, 500),
    ("search_workspace", "max_results_per_service"): (1, 25),
    ("tasks_tasks_digest", "max_results"): (1, 100),
}


def test_enforced_bounds_are_in_the_schema_not_the_prose() -> None:
    tools = _tools(workspace_mcp)
    for (tool, field), (low, high) in BOUNDS.items():
        schema = tools[tool].input_schema["properties"][field]
        assert (schema["minimum"], schema["maximum"]) == (low, high), (tool, field)
        assert f"{low} to {high}" not in schema["description"], (tool, field)
    assert tools["gmail_get_mail_digest"].input_schema["properties"]["window"]["pattern"] == r"^\d+[dhw]$"


def test_out_of_range_argument_is_a_structured_invalid_input() -> None:
    async def run() -> Any:
        async with Client(workspace_mcp) as client:
            return await client.call_tool("calendar_search_events", {"max_results": 0}, raise_on_error=False)

    result = anyio.run(run)
    assert result.is_error
    assert result.structured_content["code"] == "invalid_input"
    assert result.structured_content["field_errors"][0]["field"] == "max_results"


def test_documented_result_fields_reach_the_published_schema() -> None:
    tools = _tools(workspace_mcp)
    for tool, fields in OUTPUT_FIELD_DOCS.items():
        properties = tools[tool].output_schema["properties"]
        for field, documented in fields.items():
            assert properties[field]["type"] == documented["type"], (tool, field)
            assert properties[field]["description"], (tool, field)
        assert tools[tool].output_schema["additionalProperties"] is True


def test_renderer_mode_defaults_to_bundled_and_cdn_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from mcp_google_workspace.file_uploads import configure_prefab_renderer

    monkeypatch.delenv("PREFAB_RENDERER_URL", raising=False)
    monkeypatch.delenv("MCP_PREFAB_RENDERER", raising=False)
    monkeypatch.setenv("PREFAB_BUNDLED_RENDERER", "")
    assert configure_prefab_renderer() == "bundled"
    assert os.environ["PREFAB_BUNDLED_RENDERER"] == "1"

    monkeypatch.setenv("MCP_PREFAB_RENDERER", "cdn")
    assert configure_prefab_renderer() == "cdn"
    assert "PREFAB_BUNDLED_RENDERER" not in os.environ

    monkeypatch.setenv("MCP_PREFAB_RENDERER", "remote")
    with pytest.raises(ValueError, match="bundled or cdn"):
        configure_prefab_renderer()


def test_picker_declares_openai_invocation_labels() -> None:
    meta = _tools(workspace_mcp)["uploads_file_manager"].meta or {}
    assert meta["openai/toolInvocation/invoking"] and meta["openai/toolInvocation/invoked"]
