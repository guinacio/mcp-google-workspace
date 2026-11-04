"""Docs normalizers over recorded-shape Document and batchUpdate payloads."""

from __future__ import annotations

from typing import Any

from mcp_google_workspace.docs.presentation import (
    batch_update_result,
    created_document,
    document_outline,
    document_text_view,
    raw_document_view,
    replace_text_result,
    text_update_result,
)

from . import Case

LONG_DOCUMENT = "docs_get_document_tabs.json"


def _first_tab_only(document: dict[str, Any]) -> dict[str, Any]:
    """The default documents.get response: the first tab's content at the root, no ``tabs``."""
    return {key: value for key, value in document.items() if key != "tabs"}


def _second_markdown_page(document: dict[str, Any]) -> dict[str, Any]:
    first = document_text_view(document, format="markdown")
    assert first["next_offset"] is not None
    return document_text_view(document, format="markdown", offset=first["next_offset"])


CASES: list[Case] = [
    Case("docs_get_document", "docs_get_document.json", lambda doc: document_outline(_first_tab_only(doc))),
    Case("docs_get_document_tabs", LONG_DOCUMENT, document_outline, items_key="tabs"),
    Case("docs_create_document", "docs_create_document.json", created_document),
    Case("docs_append_document_text", "docs_batch_update_append.json", text_update_result),
    Case("docs_replace_document_text", "docs_batch_update_replace.json", replace_text_result),
    Case(
        "docs_batch_update_document",
        "docs_batch_update_document.json",
        batch_update_result,
        items_key="replies",
        opaque_keys=("replies",),
    ),
    Case("docs_read_document_text", LONG_DOCUMENT, lambda doc: document_text_view(doc, format="text")),
    Case("docs_read_document_markdown", LONG_DOCUMENT, lambda doc: document_text_view(doc, format="markdown")),
    Case("docs_read_document_markdown_page_2", LONG_DOCUMENT, _second_markdown_page),
    Case("docs_read_document_raw", "docs_get_document.json", raw_document_view, opaque_keys=("document",)),
]
