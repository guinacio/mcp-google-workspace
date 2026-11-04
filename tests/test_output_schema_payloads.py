"""Realistic Google payloads must pass the published output schemas.

``tests/fixtures/google_payloads/`` holds one response per raw passthrough
tool, shaped like the Google API reference (field names, nesting and types),
with identifiers and personal data replaced. Each is served through a stub
Google client and the tool is called over the in-memory FastMCP client, which
validates ``structuredContent`` against ``outputSchema`` exactly as a
conforming MCP client does (``mcp.client.session.validate_tool_result``).

Before 1.0.1 the schemas were closed and typed by a name heuristic, so a
``namedStyles`` object or a ``totalItems`` counter Google returns made the
whole call fail on the client. The fixtures keep those fields on purpose.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastmcp import Client
from jsonschema import Draft202012Validator

import mcp_google_workspace.auth.google_auth as google_auth
from mcp_google_workspace.server import workspace_mcp

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "google_payloads"

# (tool, fixture, arguments, top-level fields the old heuristic typed wrongly or did not list)
CASES: list[tuple[str, str, dict[str, Any], tuple[str, ...]]] = [
    ("docs_get_document", "docs_get_document.json", {"document_id": "doc-1"}, ("namedStyles", "inlineObjects", "positionedObjects", "tabs")),
    ("people_list_contacts", "people_list_contacts.json", {"page_size": 2}, ("totalItems", "totalPeople")),
    ("calendar_list_calendars", "calendar_list_calendars.json", {}, ("nextSyncToken", "items")),
    ("sheets_get_spreadsheet", "sheets_get_spreadsheet.json", {"spreadsheet_id": "sheet-1"}, ("dataSources", "developerMetadata")),
    ("slides_get_presentation", "slides_get_presentation.json", {"presentation_id": "pres-1"}, ("notesMaster", "masters", "layouts")),
    ("forms_get_form", "forms_get_form.json", {"form_id": "form-1"}, ("publishSettings", "settings")),
]


class _PayloadService:
    """A Google client stub whose every ``execute()`` returns one payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __getattr__(self, name: str) -> "_PayloadService":
        if name.startswith("__"):
            raise AttributeError(name)
        return self

    def __call__(self, *_args: Any, **_kwargs: Any) -> "_PayloadService":
        return self

    def execute(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return json.loads(json.dumps(self._payload))


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


async def _call(tool: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    async with Client(workspace_mcp) as client:
        listed = {entry.name: entry for entry in await client.list_tools()}
        result = await client.call_tool(tool, arguments)
    return listed[tool].output_schema, result


@pytest.mark.parametrize(("tool", "fixture", "arguments", "notable"), CASES, ids=[case[0] for case in CASES])
def test_realistic_payload_validates_against_published_output_schema(
    tool: str, fixture: str, arguments: dict[str, Any], notable: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _load(fixture)
    for field in notable:
        assert field in payload, f"fixture {fixture} must keep the {field!r} field"
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _PayloadService(payload))

    schema, result = anyio.run(_call, tool, arguments)

    assert result.is_error is False, result.content
    assert schema is not None and schema["additionalProperties"] is True
    structured = result.structured_content
    assert isinstance(structured, dict)
    for field in notable:
        assert field in structured
    # The in-memory client already validated; validate once more explicitly
    # so a regression names the offending field instead of a generic failure.
    errors = sorted(Draft202012Validator(schema).iter_errors(structured), key=lambda error: list(error.path))
    assert errors == [], [f"{list(error.path)}: {error.message}" for error in errors]


def test_fixture_fields_are_not_typed_by_the_plural_name_heuristic() -> None:
    """The exact failure from the audit: ``namedStyles must be array``."""
    from mcp_google_workspace.common.output_schemas import _named_field_schema, _registered_schema

    assert "type" not in _named_field_schema("namedStyles")
    assert "type" not in _named_field_schema("notes")  # a string on Tasks
    assert "type" not in _named_field_schema("tabs")
    assert _named_field_schema("items")["type"] == "array"
    assert _named_field_schema("totalItems")["type"] == "integer"
    document = _registered_schema("get_document")
    assert document is not None
    assert document["additionalProperties"] is True
    assert "type" not in document["properties"]["namedStyles"]
