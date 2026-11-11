"""format=raw on sheets_get_spreadsheet, slides_read_presentation and forms_get_form (2.1.0).

The raw read returns Google's resource unchanged, paged by whole elements
(``common/raw_paging.py``), and only in the extended and complete tool tiers.
The default (normalized) responses must stay exactly what 2.0.1 returned: the
committed goldens under ``tests/fixtures/google_payloads/golden/`` are the
2.0.1 output, so each default call is compared with its golden byte for byte.

The payloads are the hand-built fixtures in ``tests/fixtures/google_payloads``,
shaped like the Sheets, Slides and Forms API references; no live Google call
is made.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastmcp import Client

import mcp_google_workspace.auth.google_auth as google_auth
from mcp_google_workspace.common.errors import RecoverableToolError
from mcp_google_workspace.common.raw_paging import RAW_ELEMENT_MAX_CHARS, RAW_LIMIT_CHARS, raw_resource_page

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from fixtures.google_payloads import GOLDEN_DIR, load  # noqa: E402

SPREADSHEET = "sheets_get_spreadsheet_formatting.json"
PRESENTATION = "slides_get_presentation.json"
FORM = "forms_get_form_quiz.json"


class _Service:
    """A Google client stub: records every call with its keyword arguments.

    ``payloads`` maps a method name to its response, or to a function of the
    call's keyword arguments.
    """

    def __init__(self, payloads: dict[str, Any], calls: list[tuple[str, dict[str, Any]]]) -> None:
        self._payloads = payloads
        self._calls = calls
        self._name = ""
        self._kwargs: dict[str, Any] = {}

    def __getattr__(self, name: str) -> "_Service":
        if name.startswith("__"):
            raise AttributeError(name)
        self._name = name
        return self

    def __call__(self, *_args: Any, **kwargs: Any) -> "_Service":
        if kwargs:
            self._calls.append((self._name, kwargs))
            self._kwargs = kwargs
        return self

    def execute(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        response = self._payloads[self._name]
        if callable(response):
            response = response(self._kwargs)
        return copy.deepcopy(response)


def _stub(monkeypatch, payloads: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _Service(payloads, calls))
    return calls


async def _call(tool: str, arguments: dict[str, Any]) -> Any:
    from mcp_google_workspace.server import workspace_mcp

    async with Client(workspace_mcp) as client:
        return await client.call_tool(tool, arguments, raise_on_error=False)


def _render(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


# --- the paging contract ------------------------------------------------------


def _deck(slides: int) -> dict[str, Any]:
    """A presentation of ``slides`` copies of the fixture's slides, with distinct IDs."""
    payload = load(PRESENTATION)
    template = payload["slides"]
    payload["slides"] = []
    for index in range(slides):
        slide = copy.deepcopy(template[index % len(template)])
        slide["objectId"] = f"slide-{index}"
        payload["slides"].append(slide)
    return payload


def _pages(resource: dict[str, Any], *, lists: tuple[str, ...], limit: int, id_key: str) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    offset: int | None = 0
    while offset is not None:
        page = raw_resource_page(resource, id_key=id_key, lists=lists, offset=offset, limit=limit, narrow_hint="narrow it")
        pages.append(page)
        offset = page["next_offset"]
    return pages


def _join(pages: list[dict[str, Any]]) -> dict[str, Any]:
    joined = dict(pages[0]["page"])
    for page in pages[1:]:
        for key, value in page["page"].items():
            if isinstance(value, list):
                joined[key] = joined.get(key, []) + value
    return joined


def test_a_resource_that_fits_one_page_is_returned_unchanged() -> None:
    form = load(FORM)
    page = raw_resource_page(form, id_key="formId", lists=("items",), narrow_hint="narrow it")
    assert page["page"] == form
    assert page["next_offset"] is None
    assert page["element_count"] == len(form["items"])
    assert page["raw_chars"] == len(json.dumps(form, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def test_pages_cut_between_whole_elements_and_join_back_to_the_resource() -> None:
    deck = _deck(12)
    pages = _pages(deck, lists=("slides", "layouts", "masters"), limit=8_000, id_key="presentationId")

    assert len(pages) > 2
    assert _join(pages) == deck
    # The first page has every non-list field; later pages only the ID and their slices.
    assert set(pages[0]["page"]) >= {"presentationId", "title", "pageSize", "notesMaster", "revisionId", "locale"}
    for page in pages[1:]:
        assert set(page["page"]) <= {"presentationId", "slides", "layouts", "masters"}
        assert page["page"]["presentationId"] == deck["presentationId"]
    # Elements are counted across the lists in order: slides, then layouts, then masters.
    assert pages[0]["element_count"] == 12 + len(deck["layouts"]) + len(deck["masters"])
    assert "masters" in pages[-1]["page"]
    # Every page describes the whole resource the same way.
    assert {(page["raw_chars"], page["raw_sha256"]) for page in pages} == {(pages[0]["raw_chars"], pages[0]["raw_sha256"])}


def test_each_page_stays_within_its_budget_unless_one_element_is_larger() -> None:
    deck = _deck(12)
    limit = 8_000
    for page in _pages(deck, lists=("slides",), limit=limit, id_key="presentationId")[1:]:
        size = len(json.dumps(page["page"], ensure_ascii=False, separators=(",", ":")))
        elements = sum(len(value) for value in page["page"].values() if isinstance(value, list))
        assert size <= limit + 200 or elements == 1


def test_an_element_larger_than_the_page_comes_alone() -> None:
    deck = _deck(3)
    pages = _pages(deck, lists=("slides",), limit=100, id_key="presentationId")
    assert [len(page["page"].get("slides", [])) for page in pages][:3] == [1, 1, 1]
    assert _join(pages) == deck


def test_an_element_above_the_hard_ceiling_fails_with_the_offset_that_skips_it() -> None:
    form = load(FORM)
    form["items"][1]["description"] = "x" * (RAW_ELEMENT_MAX_CHARS + 1)
    with pytest.raises(RecoverableToolError) as raised:
        raw_resource_page(form, id_key="formId", lists=("items",), offset=1, narrow_hint="read it another way")
    assert raised.value.error_code == "invalid_input"
    assert "items[1]" in str(raised.value) and "read it another way" in str(raised.value)
    assert raised.value.details == {"offset": 1, "element_chars": pytest.approx(RAW_ELEMENT_MAX_CHARS, rel=0.01), "next_offset": 2}
    # The pages before and after it still read.
    assert raw_resource_page(form, id_key="formId", lists=("items",), offset=2, narrow_hint="")["next_offset"] is None


def test_an_offset_past_the_end_returns_the_id_only() -> None:
    form = load(FORM)
    page = raw_resource_page(form, id_key="formId", lists=("items",), offset=99, narrow_hint="")
    assert page["page"] == {"formId": form["formId"]}
    assert page["next_offset"] is None


# --- the tier gate --------------------------------------------------------------

_RAW_CALLS = [
    ("sheets_get_spreadsheet", {"spreadsheet_id": "sheet-1", "format": "raw"}, {"get": load(SPREADSHEET)}),
    ("slides_read_presentation", {"presentation_id": "deck-1", "format": "raw"}, {"get": load(PRESENTATION)}),
    ("forms_get_form", {"form_id": "form-1", "format": "raw"}, {"get": load(FORM)}),
]


@pytest.mark.parametrize(("tool", "arguments", "payloads"), _RAW_CALLS, ids=[call[0] for call in _RAW_CALLS])
def test_core_tier_refuses_raw_before_calling_google(monkeypatch, tool, arguments, payloads) -> None:
    monkeypatch.delenv("MCP_TOOL_TIER", raising=False)
    calls = _stub(monkeypatch, payloads)

    result = anyio.run(_call, tool, arguments)

    assert result.is_error is True
    error = result.structured_content
    assert error["code"] == "invalid_input"
    assert "MCP_TOOL_TIER=extended" in error["message"] and "MCP_TOOL_TIER=core" in error["message"]
    assert error["required_action"]["field_errors"][0]["field"] == "format"
    assert error["details"] == {"required_tier": "extended", "tool_tier": "core"}
    assert calls == []


@pytest.mark.parametrize("tier", ["extended", "complete"])
@pytest.mark.parametrize(("tool", "arguments", "payloads"), _RAW_CALLS, ids=[call[0] for call in _RAW_CALLS])
def test_extended_and_complete_tiers_return_the_resource_unchanged(monkeypatch, tier, tool, arguments, payloads) -> None:
    monkeypatch.setenv("MCP_TOOL_TIER", tier)
    _stub(monkeypatch, payloads)

    result = anyio.run(_call, tool, arguments)

    assert result.is_error is False, result.content
    structured = result.structured_content
    resource_key = {"sheets": "spreadsheet", "slides": "presentation", "forms": "form"}[tool.split("_", 1)[0]]
    assert structured[resource_key] == payloads["get"]
    assert structured["format"] == "raw"
    assert (structured["truncated"], structured["next_offset"]) == (False, None)


# --- what each raw read asks Google for --------------------------------------------


def test_sheets_raw_passes_ranges_and_never_asks_for_unrequested_grid_data(monkeypatch) -> None:
    monkeypatch.setenv("MCP_TOOL_TIER", "extended")
    calls = _stub(monkeypatch, {"get": load(SPREADSHEET)})

    anyio.run(_call, "sheets_get_spreadsheet", {"spreadsheet_id": "sheet-1", "format": "raw"})
    anyio.run(
        _call,
        "sheets_get_spreadsheet",
        {"spreadsheet_id": "sheet-1", "format": "raw", "ranges": ["Ledger!A1:C3"], "include_grid_data": True},
    )

    assert calls == [
        ("get", {"spreadsheetId": "sheet-1", "includeGridData": False, "ranges": None}),
        ("get", {"spreadsheetId": "sheet-1", "includeGridData": True, "ranges": ["Ledger!A1:C3"]}),
    ]


def test_sheets_raw_keeps_what_the_normalized_shape_drops(monkeypatch) -> None:
    monkeypatch.setenv("MCP_TOOL_TIER", "extended")
    _stub(monkeypatch, {"get": load(SPREADSHEET)})

    raw = anyio.run(_call, "sheets_get_spreadsheet", {"spreadsheet_id": "sheet-1", "format": "raw"}).structured_content
    normalized = anyio.run(_call, "sheets_get_spreadsheet", {"spreadsheet_id": "sheet-1"}).structured_content

    summary = raw["spreadsheet"]["sheets"][0]
    assert {"charts", "merges", "basicFilter", "filterViews", "conditionalFormats"} <= set(summary)
    assert "spreadsheetTheme" in raw["spreadsheet"]["properties"]
    assert "rowData" in json.dumps(raw["spreadsheet"]["sheets"][1])
    assert "charts" not in json.dumps(normalized) and "spreadsheet" not in normalized


def test_slides_raw_with_slide_ids_reads_each_page(monkeypatch) -> None:
    monkeypatch.setenv("MCP_TOOL_TIER", "extended")
    deck = load(PRESENTATION)
    by_id = {slide["objectId"]: {**slide, "pageType": "SLIDE", "revisionId": deck["revisionId"]} for slide in deck["slides"]}
    calls = _stub(monkeypatch, {"get": lambda kwargs: by_id[kwargs["pageObjectId"]]})
    wanted = [deck["slides"][2]["objectId"], deck["slides"][0]["objectId"], deck["slides"][2]["objectId"]]

    result = anyio.run(_call, "slides_read_presentation", {"presentation_id": "deck-1", "format": "raw", "slide_ids": wanted})

    assert result.is_error is False, result.content
    assert calls == [
        ("get", {"presentationId": "deck-1", "pageObjectId": wanted[0]}),
        ("get", {"presentationId": "deck-1", "pageObjectId": wanted[1]}),
    ]
    structured = result.structured_content
    assert structured["pages"] == [by_id[wanted[0]], by_id[wanted[1]]]
    assert structured["revision_id"] == deck["revisionId"]
    assert "presentation" not in structured


def test_slides_raw_pages_through_a_large_deck(monkeypatch) -> None:
    monkeypatch.setenv("MCP_TOOL_TIER", "extended")
    deck = _deck(40)
    _stub(monkeypatch, {"get": deck})
    pages: list[dict[str, Any]] = []
    arguments: dict[str, Any] = {"presentation_id": "deck-1", "format": "raw"}
    while True:
        structured = anyio.run(_call, "slides_read_presentation", arguments).structured_content
        pages.append(structured)
        if structured["next_offset"] is None:
            break
        assert structured["truncated"] is True
        arguments = {**arguments, "offset": structured["next_offset"]}

    assert len(pages) > 1
    assert all(len(json.dumps(page["presentation"])) < RAW_ELEMENT_MAX_CHARS for page in pages)
    assert _join([{"page": page["presentation"]} for page in pages]) == deck


# --- the default responses are 2.0.1's ---------------------------------------------

_DEFAULT_CALLS = [
    ("sheets_get_spreadsheet", {"spreadsheet_id": "sheet-1"}, "sheets_get_spreadsheet.json", "sheets_get_spreadsheet"),
    (
        "sheets_get_spreadsheet",
        {"spreadsheet_id": "sheet-1", "include_grid_data": True},
        "sheets_get_spreadsheet_grid_data.json",
        "sheets_get_spreadsheet_grid_data",
    ),
    ("slides_read_presentation", {"presentation_id": "deck-1"}, "slides_get_presentation.json", "slides_get_presentation"),
    ("forms_get_form", {"form_id": "form-1"}, "forms_get_form.json", "forms_get_form"),
]


@pytest.mark.parametrize("tier", ["core", "extended"])
@pytest.mark.parametrize("explicit", [False, True], ids=["omitted", "normalized"])
@pytest.mark.parametrize(("tool", "arguments", "fixture", "golden"), _DEFAULT_CALLS, ids=[call[3] for call in _DEFAULT_CALLS])
def test_default_responses_are_byte_identical_to_2_0_1(monkeypatch, tier, explicit, tool, arguments, fixture, golden) -> None:
    monkeypatch.setenv("MCP_TOOL_TIER", tier)
    _stub(monkeypatch, {"get": load(fixture)})
    if explicit:
        arguments = {**arguments, "format": "normalized", "offset": 3}

    result = anyio.run(_call, tool, arguments)

    assert result.is_error is False, result.content
    assert _render(result.structured_content) == (GOLDEN_DIR / f"{golden}.json").read_text(encoding="utf-8")


def test_raw_page_budget_is_documented_in_the_offset_descriptions() -> None:
    async def schemas() -> dict[str, Any]:
        from mcp_google_workspace.server import workspace_mcp

        async with Client(workspace_mcp) as client:
            return {tool.name: tool.input_schema for tool in await client.list_tools()}

    published = anyio.run(schemas)
    for tool in ("sheets_get_spreadsheet", "slides_read_presentation", "forms_get_form"):
        properties = published[tool]["properties"]
        assert properties["format"]["enum"] == ["normalized", "raw"]
        assert properties["format"]["default"] == "normalized"
        assert f"{RAW_LIMIT_CHARS:,}" in properties["offset"]["description"]
        assert "MCP_TOOL_TIER=core" in properties["format"]["description"]
