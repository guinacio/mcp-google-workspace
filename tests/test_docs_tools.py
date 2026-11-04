import json
import re
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastmcp import Client

import mcp_google_workspace.auth.google_auth as google_auth
from mcp_google_workspace.docs.presentation import (
    OBJECT_MARKER,
    batch_update_result,
    created_document,
    document_outline,
    document_text_view,
    raw_document_view,
    render_document,
    replace_text_result,
    text_update_result,
)
from mcp_google_workspace.docs.server import docs_mcp
from mcp_google_workspace.docs.tools import (
    AppendDocumentTextRequest,
    BatchUpdateDocumentRequest,
    CreateDocumentRequest,
    GetDocumentRequest,
    ReadDocumentRequest,
    ReplaceDocumentTextRequest,
    append_document_text_payload,
    batch_update_document_payload,
    build_append_text_requests,
    build_replace_text_requests,
    create_document_payload,
    get_document_payload,
    read_document_payload,
    replace_document_text_payload,
)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from fixtures.google_payloads import load  # noqa: E402

LONG_DOCUMENT = "docs_get_document_tabs.json"


async def _list_tool_names(server):
    tools = await server.list_tools(run_middleware=False)
    return [tool.name for tool in tools]


async def _get_tool(server, name):
    tools = await server.list_tools(run_middleware=False)
    return next(tool for tool in tools if tool.name == name)


class _Exec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _DocumentsApi:
    def __init__(self):
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return _Exec({"kind": "documents.get", "kwargs": kwargs})

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return _Exec({"kind": "documents.create", "kwargs": kwargs})

    def batchUpdate(self, **kwargs):
        self.calls.append(("batchUpdate", kwargs))
        return _Exec({"kind": "documents.batchUpdate", "kwargs": kwargs})


class _DocsService:
    def __init__(self):
        self.documents_api = _DocumentsApi()

    def documents(self):
        return self.documents_api


def test_docs_server_registers_expected_tools():
    tool_names = anyio.run(_list_tool_names, docs_mcp)
    assert "get_document" in tool_names
    assert "read_document" in tool_names
    assert "create_document" in tool_names
    assert "append_document_text" in tool_names
    assert "replace_document_text" in tool_names
    assert "batch_update_document" in tool_names


def test_docs_request_builders():
    assert build_append_text_requests("Hello") == [{"insertText": {"endOfSegmentLocation": {}, "text": "Hello"}}]
    assert build_replace_text_requests("old", "new", match_case=True) == [
        {
            "replaceAllText": {
                "containsText": {"text": "old", "matchCase": True},
                "replaceText": "new",
            }
        }
    ]


def test_docs_payload_helpers(monkeypatch):
    service = _DocsService()
    monkeypatch.setattr("mcp_google_workspace.docs.tools.docs_service", lambda: service)

    fetched = get_document_payload(GetDocumentRequest(document_id="doc-1", include_tabs_content=True))
    read = read_document_payload(ReadDocumentRequest(document_id="doc-1", format="markdown"))
    created = create_document_payload(CreateDocumentRequest(title="Sprint Notes"))
    appended = append_document_text_payload(AppendDocumentTextRequest(document_id="doc-1", text="\nNext item"))
    replaced = replace_document_text_payload(
        ReplaceDocumentTextRequest(document_id="doc-1", contains_text="TODO", replace_text="DONE")
    )
    updated = batch_update_document_payload(
        BatchUpdateDocumentRequest(document_id="doc-1", requests=[{"updateDocumentStyle": {}}])
    )

    assert fetched["kwargs"]["documentId"] == "doc-1"
    assert read["kwargs"] == {"documentId": "doc-1", "includeTabsContent": True}
    assert created["kwargs"]["body"]["title"] == "Sprint Notes"
    assert appended["kwargs"]["body"]["requests"][0]["insertText"]["text"] == "\nNext item"
    assert replaced["kwargs"]["body"]["requests"][0]["replaceAllText"]["replaceText"] == "DONE"
    assert updated["kwargs"]["body"]["requests"] == [{"updateDocumentStyle": {}}]


def test_docs_tool_annotations():
    get_tool = anyio.run(_get_tool, docs_mcp, "get_document")
    read_tool = anyio.run(_get_tool, docs_mcp, "read_document")
    replace_tool = anyio.run(_get_tool, docs_mcp, "replace_document_text")

    assert get_tool.annotations.read_only_hint is True
    assert read_tool.annotations.read_only_hint is True
    assert read_tool.annotations.destructive_hint is False
    assert replace_tool.annotations.idempotent_hint is True


# --- mutation results ---------------------------------------------------------


def test_mutation_results_are_normalized():
    created = created_document(load("docs_create_document.json"))
    assert created == {
        "status": "ok",
        "document_id": "1NeWdOcUmEnT0Sprint42Retro0Example0000000abc",
        "title": "Sprint 42 retrospective",
        "revision_id": "ALBJ4Lu7NewDocRevision0abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUV",
        "url": "https://docs.google.com/document/d/1NeWdOcUmEnT0Sprint42Retro0Example0000000abc/edit",
    }
    appended = text_update_result(load("docs_batch_update_append.json"))
    assert set(appended) == {"status", "document_id", "revision_id"}
    assert appended["revision_id"].endswith("abcdeg")
    assert replace_text_result(load("docs_batch_update_replace.json"))["occurrences_changed"] == 3
    batch = batch_update_result(load("docs_batch_update_document.json"))
    assert batch["count"] == len(batch["replies"]) == 6
    assert batch["replies"][1] == {"createNamedRange": {"namedRangeId": "kix.nr7f3k2m9q1a"}}


@pytest.mark.parametrize("replies", [[{"replaceAllText": {}}], [{}], []])
def test_replace_reports_zero_when_google_omits_the_count(replies):
    result = replace_text_result({"documentId": "doc-1", "replies": replies})
    assert result["occurrences_changed"] == 0
    assert result["revision_id"] is None


# --- editing outline ----------------------------------------------------------


def test_outline_without_tabs_content_has_one_untitled_tab():
    document = load("docs_get_document.json")
    document.pop("tabs")
    outline = document_outline(document)
    assert [tab["tab_id"] for tab in outline["tabs"]] == [None]
    first = outline["tabs"][0]
    assert [element["kind"] for element in first["content"]] == ["section_break"] + ["paragraph"] * 4
    assert first["content"][0]["start_index"] == 0
    heading = first["content"][1]
    assert (heading["style"], heading["text"], heading["start_index"], heading["end_index"]) == (
        "HEADING_1", "Launch plan", 1, 13,
    )
    assert first["content"][2]["style"] is None
    assert first["content"][3]["list"] == {"list_id": "kix.list0", "nesting_level": 0, "ordered": False}
    assert first["content"][4]["inline"] == [
        {"kind": "image", "start_index": 80, "end_index": 81, "object_id": "kix.img0"}
    ]


def test_outline_flattens_every_tab_and_keeps_indices_usable():
    document = load(LONG_DOCUMENT)
    outline = document_outline(document)
    tabs = [(tab["tab_id"], tab["title"], tab["nesting_level"], tab["parent_tab_id"]) for tab in outline["tabs"]]
    assert tabs == [
        ("t.0", "Overview", 0, None),
        ("t.tl1", "Timeline", 1, "t.0"),
        ("t.eng", "Engineering plan", 0, None),
        ("t.dec", "Decisions log", 0, None),
        ("t.run", "Migration runbook", 0, None),
    ]
    overview = outline["tabs"][0]["content"]
    kinds = {element["kind"] for element in overview}
    assert kinds == {"section_break", "paragraph", "table", "table_of_contents"}
    # A paragraph's start_index plus a character position is a document index.
    chips = next(element for element in overview if element["kind"] == "paragraph" and "reviewed with" in element["text"])
    for marker in chips["inline"]:
        assert chips["text"][marker["start_index"] - chips["start_index"]] == OBJECT_MARKER
    numbered = [element["list"] for element in overview if element["kind"] == "paragraph" and element["list"]]
    assert {(entry["list_id"], entry["ordered"]) for entry in numbered} == {("kix.bul1", False), ("kix.num1", True)}
    table = next(element for element in overview if element["kind"] == "table")
    assert (table["rows"], table["columns"]) == (5, 4)
    assert table["cells"][1][1]["inline"][0]["name"] == "Priya Raman"
    link = next(
        marker for element in overview if element["kind"] == "paragraph"
        for marker in element["inline"] if marker["kind"] == "link" and marker["url"].startswith("?tab=")
    )
    assert link["url"] == "?tab=t.eng#heading=h.risk01"


def test_outline_is_much_smaller_than_the_document():
    document = load(LONG_DOCUMENT)
    raw = len(json.dumps(document, ensure_ascii=False))
    outline = len(json.dumps(document_outline(document), ensure_ascii=False))
    assert outline * 3 < raw
    for dropped in ("namedStyles", "documentStyle", "textStyle", "paragraphStyle", "glyphSymbol", "contentUri"):
        assert dropped not in json.dumps(document_outline(document))


# --- reading view -------------------------------------------------------------


def _pages(document: dict[str, Any], fmt: str) -> list[dict[str, Any]]:
    pages = [document_text_view(document, format=fmt)]
    while pages[-1]["next_offset"] is not None:
        pages.append(document_text_view(document, format=fmt, offset=pages[-1]["next_offset"]))
    return pages


@pytest.mark.parametrize("fmt", ["text", "markdown"])
def test_read_pages_join_to_the_whole_rendering(fmt):
    document = load(LONG_DOCUMENT)
    rendered = render_document(document, fmt)
    pages = _pages(document, fmt)
    assert len(pages) >= 2
    assert "".join(page["body"] for page in pages) == rendered
    assert [page["truncated"] for page in pages] == [True] * (len(pages) - 1) + [False]
    assert pages[-1]["next_offset"] is None
    assert {page["body_sha256"] for page in pages} == {pages[0]["body_sha256"]}
    assert {page["body_chars"] for page in pages} == {len(rendered)}
    assert all(len(page["body"]) <= 8_000 for page in pages)
    # Pages end at a paragraph break.
    assert all(page["body"].endswith("\n\n") for page in pages[:-1])


def test_read_view_shape():
    document = load(LONG_DOCUMENT)
    view = document_text_view(document, format="markdown")
    assert list(view) == [
        "document_id", "title", "revision_id", "url", "format", "tabs",
        "body", "truncated", "next_offset", "body_chars", "body_sha256",
    ]
    assert view["tabs"][1] == {"tab_id": "t.tl1", "title": "Timeline"}
    raw = raw_document_view(document)
    assert raw["format"] == "raw"
    assert raw["document"] is document


def test_markdown_renders_each_construct():
    markdown = render_document(load(LONG_DOCUMENT), "markdown")
    expected = [
        "# Tab: Overview\n\n# Project Atlas: Q3 delivery plan\n\n## Northwind Labs",  # tab, TITLE, SUBTITLE
        "\n# Summary\n",
        "\n## Architecture\n",
        "\n### Week 27\n",
        "# Tab: Overview / Timeline",  # child tab
        # nested bullets
        "- One order service handles every first-party order in the north and south regions.\n"
        "    - Peak traffic is served without manual scaling.\n"
        "        - Black-friday load test at three times last year's peak.\n"
        "    - Failover between zones takes less than two minutes.\n"
        "- The legacy pipelines",
        # numbered, nesting resets and continues per level
        "1. Shadow traffic\n    1. Mirror north-region orders to Atlas and compare outcomes.\n"
        "    2. Fix every divergence above 0.1% before moving on.\n        1. Triage divergences daily.\n"
        "        2. Publish the comparison dashboard.\n2. Canary in the north region\n"
        "    1. Route 5% of orders",
        # a numbered list interrupted by paragraphs keeps counting
        "\n2. Keep the fraud vendor for this quarter.\n",
        "\n7. Close the window. Post a summary",
        # table with header separator, chips and escaped pipes
        "| Milestone | Owner | Target date | Status |\n| --- | --- | --- | --- |\n"
        "| Shadow traffic | [Priya Raman](mailto:priya.raman@example.com) | Jul 14 | Done |",
        "| Not started<br>Blocked on capacity review |",
        "| Not started \\| pending |",
        "[latency SLO runbook](https://wiki.example.com/platform/slo/order-latency)",
        "On-call rota: <https://oncall.example.com/rotas/atlas>",  # link whose text is the url
        "[Ana Souza](mailto:ana.souza@example.com)",  # person chip
        "[Atlas launch checklist](https://docs.google.com/spreadsheets/d/1Sheet0Atlas0Launch0Checklist0Example000/edit)",
        "on Oct 28, 2026.",  # date chip
        "strategy memo[^1] and",
        "\n[^1]: Platform strategy memo, March 2026, section 4",
        "![Diagram: storefronts publish order events",  # image with alt text
        "capacity formula [equation],",
        "| Contingency (20%) | $30,700 | Released after launch |\n\n---\n\nEarlier versions",  # rule
        "milestone owner.\nOn-call rota",  # soft line break
        "under 400 ms",  # no-break space
        "the risk register in the Engineering plan tab.",  # internal link: text only
    ]
    for fragment in expected:
        assert fragment in markdown, fragment
    assert markdown.count("Owners and contacts") == 1  # the table of contents is skipped
    assert "pending legal review" not in markdown  # a suggested insertion is not content yet
    for character in ("\u000b", "​", "﻿", " ", OBJECT_MARKER):
        assert character not in markdown
    assert not re.search(r"[ \t]$", markdown, re.M)
    assert "\n\n\n" not in markdown


def test_text_renders_readable_plain_text():
    text = render_document(load(LONG_DOCUMENT), "text")
    expected = [
        "=== Overview ===\n\nProject Atlas: Q3 delivery plan\n\nNorthwind Labs",
        "=== Overview / Timeline ===",
        "1. Shadow traffic\n  a. Mirror north-region orders",
        "    i. Triage divergences daily.\n    ii. Publish the comparison dashboard.",
        "Milestone | Owner | Target date | Status\nShadow traffic | Priya Raman <priya.raman@example.com> | Jul 14 | Done",
        "Not started; Blocked on capacity review",
        "latency SLO runbook (https://wiki.example.com/platform/slo/order-latency)",
        "On-call rota: https://oncall.example.com/rotas/atlas\n",
        "memo[1] and",
        "\n[1] Platform strategy memo",
        "[image: Diagram: storefronts publish",
    ]
    for fragment in expected:
        assert fragment in text, fragment
    assert "#" not in text.replace("#atlas-launch", "")
    assert "](" not in text


def test_markdown_list_numbers_start_at_the_level_start_number():
    document = {
        "documentId": "doc-1",
        "title": "Steps",
        "body": {
            "content": [
                {
                    "startIndex": 1,
                    "endIndex": 7,
                    "paragraph": {
                        "elements": [{"startIndex": 1, "endIndex": 7, "textRun": {"content": "Third\n"}}],
                        "bullet": {"listId": "kix.a"},
                    },
                }
            ]
        },
        "lists": {
            "kix.a": {"listProperties": {"nestingLevels": [{"glyphType": "UPPER_ROMAN", "glyphFormat": "%0)", "startNumber": 3}]}}
        },
    }
    assert render_document(document, "markdown") == "3. Third"
    assert render_document(document, "text") == "III) Third"


# --- through the server -------------------------------------------------------


class _RecordingService:
    """A Google client stub: records each call's keyword arguments, returns one payload."""

    def __init__(self, payload: dict[str, Any], calls: list[tuple[str, dict[str, Any]]]) -> None:
        self._payload = payload
        self._calls = calls
        self._name = ""

    def __getattr__(self, name: str) -> "_RecordingService":
        if name.startswith("__"):
            raise AttributeError(name)
        self._name = name
        return self

    def __call__(self, *_args: Any, **kwargs: Any) -> "_RecordingService":
        self._calls.append((self._name, kwargs))
        return self

    def execute(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return json.loads(json.dumps(self._payload))


async def _call_workspace(tool: str, arguments: dict[str, Any]) -> tuple[Any, Any]:
    from mcp_google_workspace.server import workspace_mcp

    async with Client(workspace_mcp) as client:
        listed = {entry.name: entry for entry in await client.list_tools()}
        result = await client.call_tool(tool, arguments)
    return listed[tool], result


def test_read_document_end_to_end(monkeypatch):
    payload = load(LONG_DOCUMENT)
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _RecordingService(payload, calls))

    tool, first = anyio.run(_call_workspace, "docs_read_document", {"document_id": payload["documentId"], "format": "markdown"})
    _, second = anyio.run(
        _call_workspace,
        "docs_read_document",
        {"document_id": payload["documentId"], "format": "markdown", "offset": first.structured_content["next_offset"]},
    )

    assert tool.annotations.read_only_hint is True
    assert first.is_error is False and second.is_error is False
    assert ("get", {"documentId": payload["documentId"], "includeTabsContent": True}) in calls
    assert first.structured_content["body"] + second.structured_content["body"] == render_document(payload, "markdown")
    assert second.structured_content["next_offset"] is None

    _, raw = anyio.run(_call_workspace, "docs_read_document", {"document_id": payload["documentId"], "format": "raw"})
    assert raw.structured_content["document"] == payload


def test_get_document_end_to_end(monkeypatch):
    payload = load(LONG_DOCUMENT)
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _RecordingService(payload, calls))

    _, result = anyio.run(
        _call_workspace, "docs_get_document", {"document_id": payload["documentId"], "include_tabs_content": True}
    )

    assert result.is_error is False, result.content
    assert result.structured_content == json.loads(json.dumps(document_outline(payload)))
