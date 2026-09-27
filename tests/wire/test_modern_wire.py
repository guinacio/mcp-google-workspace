"""Golden wire-contract fixtures: MCP 2026-07-28 (modern).

Raw JSON-RPC over HTTP (``starlette.testclient.TestClient``), against the
production composition (``mcp_google_workspace.server.workspace_mcp``) for
every application-specific scenario, and against a tiny dedicated FastMCP
instance for the one generic-protocol scenario (pagination) that production
does not exercise -- ``workspace_mcp`` sets no ``list_page_size``, so its
``tools/list`` always returns one page (see
``fastmcp.server.mixins.mcp_operations._apply_pagination``: ``page_size=None``
returns everything unpaginated). Pagination mechanics run through the exact
same handler either way, so a 3-tool toy app keeps that fixture small per the
plan's instruction not to embed the full catalog.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import anyio
import pytest
from fastmcp import Client, FastMCP
from starlette.testclient import TestClient

import mcp_google_workspace.auth.google_auth as google_auth
from mcp_google_workspace.server import workspace_mcp

from .harness import MODERN, assert_matches_fixture, modern_request, normalize


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    app = workspace_mcp.http_app(
        transport="http",
        stateless_http=False,
        json_response=True,
        allowed_hosts=["testserver"],
        allowed_origins=["http://testserver"],
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def toy_client() -> Iterator[TestClient]:
    """A 3-tool FastMCP instance with a small page size, for pagination only."""
    toy = FastMCP("wire-toy", list_page_size=2)

    @toy.tool()
    def alpha() -> str:
        return "a"

    @toy.tool()
    def bravo() -> str:
        return "b"

    @toy.tool()
    def charlie() -> str:
        return "c"

    app = toy.http_app(
        transport="http",
        stateless_http=False,
        json_response=True,
        allowed_hosts=["testserver"],
        allowed_origins=["http://testserver"],
    )
    with TestClient(app) as test_client:
        yield test_client


def test_server_discover(client: TestClient) -> None:
    response = modern_request(client, 1, "server/discover")
    assert response.status_code == 200, response.text
    result = response.json()["result"]

    # The 2026-07-28 changelog deprecates Logging (SEP-2577); FastMCP 4.0.10
    # still advertises `logging: {}` unconditionally (see
    # docs/migration/MIGRATION_FASTMCP4_MCP_2026-07-28.md 9.9 and
    # tests/wire/fixtures/modern_server_discover.json -- `capabilities.logging`
    # is asserted present on purpose, as a pinned finding, not an oversight).
    payload = {
        "status_code": response.status_code,
        "result": {
            "resultType": result["resultType"],
            "ttlMs": result["ttlMs"],
            "cacheScope": result["cacheScope"],
            "supportedVersions": sorted(result["supportedVersions"]),
            "capabilities_keys": sorted(result["capabilities"].keys()),
            "logging_capability_present": "logging" in result["capabilities"],
            "extensions_keys": sorted((result["capabilities"].get("extensions") or {}).keys()),
        },
    }
    assert_matches_fixture("modern_server_discover", normalize(payload))


def test_tools_list_first_page_and_pagination_cursor(toy_client: TestClient) -> None:
    first = modern_request(toy_client, 1, "tools/list")
    assert first.status_code == 200, first.text
    first_result = first.json()["result"]
    assert "nextCursor" in first_result

    second = modern_request(toy_client, 2, "tools/list", {"cursor": first_result["nextCursor"]})
    assert second.status_code == 200, second.text
    second_result = second.json()["result"]

    payload = {
        "first_page": {
            "tool_names": [tool["name"] for tool in first_result["tools"]],
            "has_next_cursor": "nextCursor" in first_result,
            "resultType": first_result["resultType"],
        },
        "second_page": {
            "tool_names": [tool["name"] for tool in second_result["tools"]],
            "has_next_cursor": "nextCursor" in second_result,
            "resultType": second_result["resultType"],
        },
    }
    assert_matches_fixture("modern_tools_list_pagination", normalize(payload))


def test_tools_list_shape_and_named_tools(client: TestClient) -> None:
    """No initialize; first call is tools/list against the real catalog.

    Curated: shape + a couple of named tools by exact dict, not the full
    ~140-tool catalog (tests/test_catalog_contract.py owns that).
    """
    response = modern_request(client, 1, "tools/list")
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    by_name = {tool["name"]: tool for tool in result["tools"]}
    assert "nextCursor" not in result  # production sets no list_page_size

    payload = {
        "resultType": result["resultType"],
        "ttlMs": result["ttlMs"],
        "cacheScope": result["cacheScope"],
        "tool_count_at_least": 100,
        "actual_tool_count_over_100": len(result["tools"]) >= 100,
        "named_tools": {
            "get_workspace_capabilities": by_name["get_workspace_capabilities"],
            "files_file_manager": by_name["files_file_manager"],
        },
    }
    assert_matches_fixture("modern_tools_list_shape", normalize(payload))


def test_safe_tool_call_with_result_type(client: TestClient) -> None:
    response = modern_request(
        client,
        2,
        "tools/call",
        {"name": "get_workspace_capabilities", "arguments": {}},
        name="get_workspace_capabilities",
    )
    assert response.status_code == 200, response.text
    payload = {"status_code": response.status_code, "result": response.json()["result"]}
    assert_matches_fixture("modern_safe_tool_call", normalize(payload))


class _GoogleRecorder:
    """Mirrors tests/test_http_wire.py's stub Google client -- no network calls."""

    def __init__(self, calls: list[str], path: tuple[str, ...] = ()) -> None:
        self._calls = calls
        self._path = path

    def __getattr__(self, name: str) -> "_GoogleRecorder":
        if name.startswith("__"):
            raise AttributeError(name)
        return _GoogleRecorder(self._calls, (*self._path, name))

    def __call__(self, *_args: Any, **_kwargs: Any) -> "_GoogleRecorder":
        return self

    def execute(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self._calls.append(".".join(self._path))
        return {}


_DELETE_CONTACT = {"name": "people_delete_contact", "arguments": {"person_name": "people/c-wire"}}


def test_mrtr_ask_and_answer_round(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _GoogleRecorder(calls))
    capabilities = {"elicitation": {"form": {}}}

    asked = modern_request(
        client, 10, "tools/call", _DELETE_CONTACT, name="people_delete_contact", capabilities=capabilities
    )
    assert asked.status_code == 200, asked.text
    first = asked.json()["result"]
    assert calls == []

    answered = modern_request(
        client,
        11,
        "tools/call",
        {
            **_DELETE_CONTACT,
            "inputResponses": {"confirm": {"action": "accept", "content": {"value": True}}},
            "requestState": first["requestState"],
        },
        name="people_delete_contact",
        capabilities=capabilities,
    )
    assert answered.status_code == 200, answered.text
    second = answered.json()["result"]
    assert calls == ["people.deleteContact"]

    payload = {"ask": first, "answer": second}
    assert_matches_fixture("modern_mrtr_ask_and_answer", normalize(payload))


def test_error_missing_protocol_version(client: TestClient) -> None:
    response = modern_request(client, 20, "server/discover", version=None)
    payload = {"status_code": response.status_code, "body": response.json()}
    assert_matches_fixture("modern_error_missing_version", normalize(payload))


def test_error_unsupported_protocol_version(client: TestClient) -> None:
    response = modern_request(client, 21, "server/discover", version="1999-01-01")
    payload = {"status_code": response.status_code, "body": response.json()}
    assert_matches_fixture("modern_error_unsupported_version", normalize(payload))


def test_error_malformed_request(client: TestClient) -> None:
    response = client.post(
        "/mcp",
        headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "MCP-Protocol-Version": MODERN},
        json={"jsonrpc": "2.0", "id": 22},
    )
    payload = {"status_code": response.status_code, "body": response.json()}
    assert_matches_fixture("modern_error_malformed", normalize(payload))


def test_error_unknown_method(client: TestClient) -> None:
    response = modern_request(client, 23, "totally/unknown")
    payload = {"status_code": response.status_code, "body": response.json()}
    assert_matches_fixture("modern_error_unknown_method", normalize(payload))


def test_is_error_tool_result_envelope(client: TestClient) -> None:
    response = modern_request(
        client,
        24,
        "tools/call",
        {"name": "files_read_file", "arguments": {"name": "upl_does_not_exist"}},
        name="files_read_file",
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is True
    payload = {"status_code": response.status_code, "result": result}
    assert_matches_fixture("modern_is_error_result", normalize(payload))


def test_listing_cache_hints(client: TestClient) -> None:
    tools = modern_request(client, 25, "tools/list")
    resources = modern_request(client, 26, "resources/list")
    payload = {
        "tools_list": {
            "resultType": tools.json()["result"]["resultType"],
            "ttlMs": tools.json()["result"]["ttlMs"],
            "cacheScope": tools.json()["result"]["cacheScope"],
        },
        "resources_list": {
            "resultType": resources.json()["result"]["resultType"],
            "ttlMs": resources.json()["result"]["ttlMs"],
            "cacheScope": resources.json()["result"]["cacheScope"],
        },
    }
    assert_matches_fixture("modern_listing_cache_hints", normalize(payload))


async def _stdio_discover_and_safe_call() -> dict[str, Any]:
    async with Client(workspace_mcp, mode="auto") as fastmcp_client:
        assert fastmcp_client.protocol_version == MODERN
        result = await fastmcp_client.session.call_tool(
            "get_workspace_capabilities", {}, allow_input_required=True
        )
        protocol_version = fastmcp_client.protocol_version
    return {
        "protocol_version": protocol_version,
        "is_error": result.is_error,
        "structured_content": result.structured_content,
    }


def test_modern_stdio_safe_call() -> None:
    """The same safe call over the in-memory transport that stands in for stdio."""
    payload = anyio.run(_stdio_discover_and_safe_call)
    assert_matches_fixture("modern_stdio_safe_call", normalize(payload))
