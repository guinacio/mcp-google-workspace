"""Compact Google Docs representations.

Two views of a Document resource:

* the editing outline (``docs_read_document`` format 'outline'): every structural element with
  the ``start_index``/``end_index`` a batchUpdate request needs, its plain
  text, and only the structure an edit depends on (named style, list
  membership, table cells, inline objects); styles and object geometry are
  dropped;
* the reading view (``docs_read_document``): the document rendered as clean
  text or Markdown, every tab in order, paged like an email body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from ..common.resources import clean_web_url
from ..common.text_paging import paged_text
from ..gmail.presentation import remove_invisible_characters

# An inline object (image, chip, footnote reference, break) occupies index
# units but has no text; in the outline each unit reads as U+FFFC, so a
# paragraph's start_index plus a position in its text stays a document index.
# Positions count UTF-16 code units, as Docs indices do: a character outside
# the Basic Multilingual Plane (most emoji) is one Python character but two
# units. Every index in the outline is Google's own, never a Python length.
OBJECT_MARKER = "\ufffc"

ReadFormat = Literal["text", "markdown"]

_ORDERED_GLYPHS = frozenset({"DECIMAL", "ZERO_DECIMAL", "ALPHA", "UPPER_ALPHA", "ROMAN", "UPPER_ROMAN"})
_HEADING_LEVELS = {
    "TITLE": 1,
    "SUBTITLE": 2,
    "HEADING_1": 1,
    "HEADING_2": 2,
    "HEADING_3": 3,
    "HEADING_4": 4,
    "HEADING_5": 5,
    "HEADING_6": 6,
}
_LINK_SCHEME = re.compile(r"^(?:https?://|mailto:)(?:www\.)?", re.I)


def document_url(document_id: str | None) -> str | None:
    """The canonical editor link; the Docs API returns none."""
    return f"https://docs.google.com/document/d/{document_id}/edit" if document_id else None


def _revision_id(payload: dict[str, Any]) -> str | None:
    return (payload.get("writeControl") or {}).get("requiredRevisionId")


# --- tabs -----------------------------------------------------------------


@dataclass
class _Tab:
    properties: dict[str, Any]
    # The DocumentTab, or the Document itself when Google returned no tabs.
    content: dict[str, Any]
    path: list[str] = field(default_factory=list)


def _tabs(document: dict[str, Any]) -> list[_Tab]:
    """Every tab in reading order, each child tab right after its parent.

    With ``includeTabsContent`` Google returns ``tabs`` (each with its own
    body, lists, footnotes and objects); without it, only the first tab's
    content, at the root of the Document and with no tab properties.
    """
    tabs = document.get("tabs")
    if not tabs:
        return [_Tab(properties={}, content=document)]
    found: list[_Tab] = []

    def walk(entries: list[dict[str, Any]], parents: list[str]) -> None:
        for entry in entries:
            properties = entry.get("tabProperties") or {}
            path = [*parents, str(properties.get("title") or "Untitled tab")]
            found.append(_Tab(properties=properties, content=entry.get("documentTab") or {}, path=path))
            walk(entry.get("childTabs") or [], path)

    walk(tabs, [])
    return found


def _list_level(lists: dict[str, Any], bullet: dict[str, Any]) -> dict[str, Any]:
    levels = ((lists.get(str(bullet.get("listId"))) or {}).get("listProperties") or {}).get("nestingLevels") or []
    level = int(bullet.get("nestingLevel", 0))
    return levels[level] if 0 <= level < len(levels) else {}


def _is_ordered(level: dict[str, Any]) -> bool:
    """A level numbers its items when it has a numbering glyph type and no bullet symbol."""
    return level.get("glyphType") in _ORDERED_GLYPHS and not level.get("glyphSymbol")


# --- editing outline ------------------------------------------------------


def _span(element: dict[str, Any]) -> tuple[int, int | None]:
    # Index 0 is omitted on the wire (proto3 default), e.g. the first section break.
    return int(element.get("startIndex", 0)), element.get("endIndex")


def _link_target(link: dict[str, Any]) -> str | None:
    """A link's URL; a heading, bookmark or tab in the document as the editor writes it (?tab=...#heading=...)."""
    if link.get("url"):
        return str(link["url"])
    heading = link.get("heading") or ({"id": link["headingId"]} if link.get("headingId") else None)
    bookmark = link.get("bookmark") or ({"id": link["bookmarkId"]} if link.get("bookmarkId") else None)
    target = heading or bookmark or {}
    tab_id = target.get("tabId") or link.get("tabId")
    tab = f"?tab={tab_id}" if tab_id else ""
    if heading:
        return f"{tab}#heading={heading.get('id')}"
    if bookmark:
        return f"{tab}#bookmark={bookmark.get('id')}"
    return tab or None


def _inline_marker(element: dict[str, Any], start: int, end: int) -> dict[str, Any] | None:
    if "inlineObjectElement" in element:
        return {
            "kind": "image",
            "start_index": start,
            "end_index": end,
            "object_id": element["inlineObjectElement"].get("inlineObjectId"),
        }
    if "person" in element:
        person = element["person"].get("personProperties") or {}
        return {
            "kind": "person",
            "start_index": start,
            "end_index": end,
            "name": person.get("name"),
            "email": person.get("email"),
        }
    if "richLink" in element:
        rich = element["richLink"].get("richLinkProperties") or {}
        return {
            "kind": "rich_link",
            "start_index": start,
            "end_index": end,
            "title": rich.get("title"),
            "url": clean_web_url(rich.get("uri")),
        }
    if "dateElement" in element:
        date = element["dateElement"].get("dateElementProperties") or {}
        return {
            "kind": "date",
            "start_index": start,
            "end_index": end,
            "text": date.get("displayText") or date.get("timestamp"),
        }
    if "footnoteReference" in element:
        reference = element["footnoteReference"]
        return {
            "kind": "footnote_reference",
            "start_index": start,
            "end_index": end,
            "footnote_id": reference.get("footnoteId"),
            "number": reference.get("footnoteNumber"),
        }
    for key, kind in (
        ("pageBreak", "page_break"),
        ("columnBreak", "column_break"),
        ("horizontalRule", "horizontal_rule"),
        ("equation", "equation"),
        ("autoText", "auto_text"),
    ):
        if key in element:
            return {"kind": kind, "start_index": start, "end_index": end}
    return None


def _paragraph_text(paragraph: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """The paragraph's text in index order, and its links and inline objects."""
    pieces: list[str] = []
    inline: list[dict[str, Any]] = []
    for element in paragraph.get("elements") or []:
        start, raw_end = _span(element)
        end = int(raw_end) if raw_end is not None else start + 1
        run = element.get("textRun")
        if run is not None:
            pieces.append(str(run.get("content", "")))
            target = _link_target((run.get("textStyle") or {}).get("link") or {})
            if target is None:
                continue
            previous = inline[-1] if inline else None
            if previous and previous["kind"] == "link" and previous["url"] == target and previous["end_index"] == start:
                previous["end_index"] = end
            else:
                inline.append({"kind": "link", "start_index": start, "end_index": end, "url": target})
            continue
        pieces.append(OBJECT_MARKER * max(1, end - start))
        marker = _inline_marker(element, start, end)
        if marker is not None:
            inline.append(marker)
    for object_id in paragraph.get("positionedObjectIds") or []:
        anchor = int((paragraph.get("elements") or [{}])[0].get("startIndex", 0))
        inline.append({"kind": "positioned_image", "start_index": anchor, "end_index": anchor, "object_id": object_id})
    text = "".join(pieces)
    return (text[:-1] if text.endswith("\n") else text), inline


def _content_text(content: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Plain text of a segment (a cell, a table of contents), one paragraph per line, and its inline objects."""
    lines: list[str] = []
    inline: list[dict[str, Any]] = []
    for element in content:
        if "paragraph" in element:
            text, markers = _paragraph_text(element["paragraph"])
            lines.append(text)
            inline.extend(markers)
        elif "table" in element:
            for row in element["table"].get("tableRows") or []:
                cells = [_content_text(cell.get("content") or []) for cell in row.get("tableCells") or []]
                lines.append(" | ".join(text for text, _ in cells))
                inline.extend(marker for _, markers in cells for marker in markers)
    return "\n".join(lines), inline


def _outline_paragraph(element: dict[str, Any], lists: dict[str, Any]) -> dict[str, Any]:
    paragraph = element["paragraph"]
    start, end = _span(element)
    text, inline = _paragraph_text(paragraph)
    style = (paragraph.get("paragraphStyle") or {}).get("namedStyleType")
    bullet = paragraph.get("bullet")
    return {
        "kind": "paragraph",
        "start_index": start,
        "end_index": end,
        "text": text,
        "style": None if style in (None, "NORMAL_TEXT") else style,
        "list": (
            {
                "list_id": bullet.get("listId"),
                "nesting_level": int(bullet.get("nestingLevel", 0)),
                "ordered": _is_ordered(_list_level(lists, bullet)),
            }
            if bullet
            else None
        ),
        "inline": inline,
    }


def _outline_table(element: dict[str, Any]) -> dict[str, Any]:
    table = element["table"]
    start, end = _span(element)
    return {
        "kind": "table",
        "start_index": start,
        "end_index": end,
        "rows": table.get("rows"),
        "columns": table.get("columns"),
        "cells": [[_outline_cell(cell) for cell in row.get("tableCells") or []] for row in table.get("tableRows") or []],
    }


def _outline_cell(cell: dict[str, Any]) -> dict[str, Any]:
    start, end = _span(cell)
    text, inline = _content_text(cell.get("content") or [])
    return {"start_index": start, "end_index": end, "text": text, "inline": inline}


def _outline_content(content: list[dict[str, Any]], lists: dict[str, Any]) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    for element in content:
        start, end = _span(element)
        if "paragraph" in element:
            elements.append(_outline_paragraph(element, lists))
        elif "table" in element:
            elements.append(_outline_table(element))
        elif "sectionBreak" in element:
            elements.append({"kind": "section_break", "start_index": start, "end_index": end})
        elif "tableOfContents" in element:
            elements.append(
                {
                    "kind": "table_of_contents",
                    "start_index": start,
                    "end_index": end,
                    "text": _content_text(element["tableOfContents"].get("content") or [])[0],
                }
            )
    return elements


def _outline_segments(segments: dict[str, Any], id_key: str, lists: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {id_key: segment_id, "content": _outline_content(segment.get("content") or [], lists)}
        for segment_id, segment in (segments or {}).items()
    ]


def _outline_named_ranges(named_ranges: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": entry.get("name"),
            "named_range_id": entry.get("namedRangeId"),
            "ranges": [
                {
                    "start_index": int(span.get("startIndex", 0)),
                    "end_index": span.get("endIndex"),
                    "segment_id": span.get("segmentId"),
                }
                for span in entry.get("ranges") or []
            ],
        }
        for group in (named_ranges or {}).values()
        for entry in group.get("namedRanges") or []
    ]


def _outline_tab(tab: _Tab) -> dict[str, Any]:
    properties, content = tab.properties, tab.content
    lists = content.get("lists") or {}
    return {
        "tab_id": properties.get("tabId"),
        "title": properties.get("title"),
        "index": int(properties.get("index", 0)),
        "nesting_level": int(properties.get("nestingLevel", 0)),
        "parent_tab_id": properties.get("parentTabId"),
        "content": _outline_content((content.get("body") or {}).get("content") or [], lists),
        "headers": _outline_segments(content.get("headers") or {}, "header_id", lists),
        "footers": _outline_segments(content.get("footers") or {}, "footer_id", lists),
        "footnotes": _outline_segments(content.get("footnotes") or {}, "footnote_id", lists),
        "named_ranges": _outline_named_ranges(content.get("namedRanges") or {}),
    }


def document_outline(payload: dict[str, Any]) -> dict[str, Any]:
    """docs_read_document (outline): the editing outline of a Document resource."""
    document_id = payload.get("documentId")
    return {
        "document_id": document_id,
        "title": payload.get("title"),
        "revision_id": payload.get("revisionId"),
        "url": document_url(document_id),
        "suggestions_view_mode": payload.get("suggestionsViewMode"),
        "tabs": [_outline_tab(tab) for tab in _tabs(payload)],
    }


# --- mutations ------------------------------------------------------------


def created_document(payload: dict[str, Any]) -> dict[str, Any]:
    """docs_create_document: the new document's identity."""
    document_id = payload.get("documentId")
    return {
        "status": "ok",
        "document_id": document_id,
        "title": payload.get("title"),
        "revision_id": payload.get("revisionId"),
        "url": document_url(document_id),
    }


def text_update_result(payload: dict[str, Any]) -> dict[str, Any]:
    """docs_append_document_text: the batchUpdate response without its single empty reply."""
    return {
        "status": "ok",
        "document_id": payload.get("documentId"),
        "revision_id": _revision_id(payload),
    }


def replace_text_result(payload: dict[str, Any]) -> dict[str, Any]:
    """docs_replace_document_text: how many occurrences changed (Google omits a zero)."""
    replies = payload.get("replies") or [{}]
    changed = ((replies[0] or {}).get("replaceAllText") or {}).get("occurrencesChanged")
    return {
        "status": "ok",
        "document_id": payload.get("documentId"),
        "revision_id": _revision_id(payload),
        "occurrences_changed": int(changed or 0),
    }


def batch_update_result(payload: dict[str, Any]) -> dict[str, Any]:
    """docs_batch_update_document: Google's replies, one per request, unchanged."""
    replies = payload.get("replies") or []
    return {
        "status": "ok",
        "document_id": payload.get("documentId"),
        "revision_id": _revision_id(payload),
        "replies": replies,
        "count": len(replies),
    }


# --- reading view ---------------------------------------------------------


def _comparable_link(value: str) -> str:
    return _LINK_SCHEME.sub("", value.strip()).rstrip("/").casefold()


def _roman(number: int) -> str:
    numerals = (
        (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
        (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
    )
    out = []
    for value, numeral in numerals:
        while number >= value:
            out.append(numeral)
            number -= value
    return "".join(out)


def _alpha(number: int) -> str:
    letters = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters = chr(ord("a") + remainder) + letters
    return letters


def _numeral(glyph_type: str | None, number: int) -> str:
    if glyph_type == "ZERO_DECIMAL":
        return f"{number:02d}"
    if glyph_type == "ALPHA":
        return _alpha(number)
    if glyph_type == "UPPER_ALPHA":
        return _alpha(number).upper()
    if glyph_type == "ROMAN":
        return _roman(number)
    if glyph_type == "UPPER_ROMAN":
        return _roman(number).upper()
    return str(number)


def _tidy(text: str) -> str:
    """Invisible characters out, no trailing spaces, at most one blank line in a row."""
    text = remove_invisible_characters(text)
    text = re.sub(r"[ \t]+$", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n")


@dataclass
class _Block:
    text: str
    list_item: bool = False


class _Renderer:
    """Render a Document as readable text or Markdown, tab after tab."""

    def __init__(self, fmt: ReadFormat) -> None:
        self.markdown = fmt == "markdown"
        self.footnotes_seen = 0

    # inline ---------------------------------------------------------------

    def _link(self, text: str, url: str) -> str:
        core = text.strip()
        if not core:
            return text
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()) :]
        if self.markdown:
            if _comparable_link(core) == _comparable_link(url):
                return f"{lead}<{url}>{trail}"
            label = core.replace("[", "\\[").replace("]", "\\]")
            return f"{lead}[{label}]({url}){trail}"
        if _comparable_link(core) == _comparable_link(url):
            return f"{lead}{core}{trail}"
        return f"{lead}{core} ({url}){trail}"

    def _labelled(self, label: str | None, url: str | None) -> str:
        if label and url:
            return self._link(label, url)
        return label or url or ""

    def _object(self, element: dict[str, Any], tab: _Tab, footnotes: list[tuple[int, str]]) -> str:
        if "inlineObjectElement" in element:
            object_id = element["inlineObjectElement"].get("inlineObjectId")
            embedded = (
                ((tab.content.get("inlineObjects") or {}).get(object_id) or {}).get("inlineObjectProperties") or {}
            ).get("embeddedObject") or {}
            alt = " ".join(str(embedded.get("description") or embedded.get("title") or "").split())
            if not alt:
                return "[image]"
            return f"![{alt}]()" if self.markdown else f"[image: {alt}]"
        if "person" in element:
            person = element["person"].get("personProperties") or {}
            name, email = person.get("name"), person.get("email")
            if self.markdown:
                return self._labelled(name or email, f"mailto:{email}" if email else None)
            return f"{name} <{email}>" if name and email else str(name or email or "")
        if "richLink" in element:
            rich = element["richLink"].get("richLinkProperties") or {}
            return self._labelled(rich.get("title"), clean_web_url(rich.get("uri")))
        if "dateElement" in element:
            date = element["dateElement"].get("dateElementProperties") or {}
            return str(date.get("displayText") or date.get("timestamp") or "")
        if "footnoteReference" in element:
            self.footnotes_seen += 1
            footnotes.append((self.footnotes_seen, str(element["footnoteReference"].get("footnoteId"))))
            return f"[^{self.footnotes_seen}]" if self.markdown else f"[{self.footnotes_seen}]"
        if "horizontalRule" in element:
            return "\n\n---\n\n"
        if "pageBreak" in element or "columnBreak" in element:
            return "\n"
        if "equation" in element:
            return "[equation]"
        return ""  # autoText (page numbers) and anything unknown

    def _inline(self, paragraph: dict[str, Any], tab: _Tab, footnotes: list[tuple[int, str]]) -> str:
        parts: list[str] = []
        run_text: list[str] = []
        run_url: str | None = None

        def flush() -> None:
            text = "".join(run_text)
            parts.append(self._link(text, run_url) if run_url else text)
            run_text.clear()

        for element in paragraph.get("elements") or []:
            run = element.get("textRun")
            if run is not None:
                if run.get("suggestedInsertionIds"):
                    continue  # not part of the document until accepted
                link = (run.get("textStyle") or {}).get("link") or {}
                url = link.get("url")
                if url != run_url:
                    flush()
                    run_url = url
                run_text.append(remove_invisible_characters(str(run.get("content", ""))))
                continue
            flush()
            run_url = None
            parts.append(self._object(element, tab, footnotes))
        flush()
        return "".join(parts)

    # blocks ---------------------------------------------------------------

    def _list_item(
        self, text: str, bullet: dict[str, Any], tab: _Tab, counters: dict[str, dict[int, int]]
    ) -> _Block:
        lists = tab.content.get("lists") or {}
        list_id = str(bullet.get("listId"))
        level = int(bullet.get("nestingLevel", 0))
        properties = _list_level(lists, bullet)
        seen = counters.setdefault(list_id, {})
        for deeper in [key for key in seen if key > level]:
            del seen[deeper]
        if _is_ordered(properties):
            number = seen.get(level, int(properties.get("startNumber", 1)) - 1) + 1
            seen[level] = number
            if self.markdown:
                marker = f"{number}."
            else:
                levels = ((lists.get(list_id) or {}).get("listProperties") or {}).get("nestingLevels") or []

                def numeral(match: re.Match[str]) -> str:
                    index = int(match.group(1))
                    glyph = levels[index].get("glyphType") if index < len(levels) else None
                    return _numeral(glyph, seen.get(index, 1))

                # glyphFormat names each level's number as %<level>, e.g. "%0." or "%0.%1.".
                glyph_format = str(properties.get("glyphFormat") or f"%{level}.")
                marker = re.sub(r"%(\d)", numeral, glyph_format).strip() or f"{number}."
        else:
            seen[level] = seen.get(level, 0) + 1
            marker = "-"
        indent = ("    " if self.markdown else "  ") * level
        continuation = "\n" + " " * (len(indent) + len(marker) + 1)
        return _Block(f"{indent}{marker} {continuation.join(text.split(chr(10)))}", list_item=True)

    def _cell(self, cell: dict[str, Any], tab: _Tab, footnotes: list[tuple[int, str]]) -> str:
        lines: list[str] = []
        for element in cell.get("content") or []:
            if "paragraph" in element:
                text = self._inline(element["paragraph"], tab, footnotes).strip()
            elif "table" in element:
                text = " ".join(
                    self._cell(inner, tab, footnotes)
                    for row in element["table"].get("tableRows") or []
                    for inner in row.get("tableCells") or []
                )
            else:
                continue
            if text:
                lines.extend(line.strip() for line in text.split("\n") if line.strip())
        if self.markdown:
            return "<br>".join(line.replace("|", "\\|") for line in lines)
        return "; ".join(lines)

    def _table(self, table: dict[str, Any], tab: _Tab, footnotes: list[tuple[int, str]]) -> _Block | None:
        rows = [
            [self._cell(cell, tab, footnotes) for cell in row.get("tableCells") or []]
            for row in table.get("tableRows") or []
        ]
        rows = [row for row in rows if row]
        if not rows:
            return None
        if not self.markdown:
            return _Block("\n".join(" | ".join(row) for row in rows))
        width = max(len(row) for row in rows)
        padded = [row + [""] * (width - len(row)) for row in rows]
        lines = ["| " + " | ".join(padded[0]) + " |", "|" + " --- |" * width]
        lines.extend("| " + " | ".join(row) + " |" for row in padded[1:])
        return _Block("\n".join(lines))

    def _paragraph(
        self,
        paragraph: dict[str, Any],
        tab: _Tab,
        counters: dict[str, dict[int, int]],
        footnotes: list[tuple[int, str]],
    ) -> _Block | None:
        text = self._inline(paragraph, tab, footnotes).strip()
        if not text:
            return None
        bullet = paragraph.get("bullet")
        if bullet:
            return self._list_item(text, bullet, tab, counters)
        level = _HEADING_LEVELS.get((paragraph.get("paragraphStyle") or {}).get("namedStyleType") or "")
        if level is not None:
            heading = " ".join(text.split())
            return _Block(f"{'#' * level} {heading}" if self.markdown else heading)
        return _Block(text)

    def _content(
        self,
        content: list[dict[str, Any]],
        tab: _Tab,
        counters: dict[str, dict[int, int]],
        footnotes: list[tuple[int, str]],
    ) -> list[_Block]:
        blocks: list[_Block] = []
        for element in content:
            block: _Block | None = None
            if "paragraph" in element:
                block = self._paragraph(element["paragraph"], tab, counters, footnotes)
            elif "table" in element:
                block = self._table(element["table"], tab, footnotes)
            # sectionBreak: no text; tableOfContents: it repeats the headings.
            if block is not None:
                blocks.append(block)
        return blocks

    def _footnotes(self, tab: _Tab, footnotes: list[tuple[int, str]]) -> list[_Block]:
        segments = tab.content.get("footnotes") or {}
        lines: list[str] = []
        for number, footnote_id in footnotes:
            texts = [
                self._inline(element["paragraph"], tab, [])
                for element in (segments.get(footnote_id) or {}).get("content") or []
                if "paragraph" in element
            ]
            text = " ".join(" ".join(texts).split())
            lines.append(f"[^{number}]: {text}" if self.markdown else f"[{number}] {text}")
        return [_Block("\n".join(lines))] if lines else []

    def tab(self, tab: _Tab, *, titled: bool) -> str:
        counters: dict[str, dict[int, int]] = {}
        footnotes: list[tuple[int, str]] = []
        blocks: list[_Block] = []
        if titled:
            path = " / ".join(tab.path)
            blocks.append(_Block(f"# Tab: {path}" if self.markdown else f"=== {path} ==="))
        blocks.extend(self._content((tab.content.get("body") or {}).get("content") or [], tab, counters, footnotes))
        blocks.extend(self._footnotes(tab, footnotes))
        out: list[str] = []
        for index, block in enumerate(blocks):
            if index:
                out.append("\n" if block.list_item and blocks[index - 1].list_item else "\n\n")
            out.append(block.text.strip("\n"))
        return "".join(out)


def render_document(payload: dict[str, Any], fmt: ReadFormat) -> str:
    """The whole document as clean text or Markdown, every tab in order."""
    renderer = _Renderer(fmt)
    tabs = _tabs(payload)
    titled = len(tabs) > 1
    return _tidy("\n\n".join(renderer.tab(tab, titled=titled) for tab in tabs))


def _tab_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"tab_id": tab.properties.get("tabId"), "title": tab.properties.get("title")} for tab in _tabs(payload)]


def document_text_view(payload: dict[str, Any], *, format: ReadFormat, offset: int = 0) -> dict[str, Any]:
    """docs_read_document (text, markdown): one page of the rendered document."""
    document_id = payload.get("documentId")
    page = paged_text(render_document(payload, format), offset=offset)
    return {
        "document_id": document_id,
        "title": payload.get("title"),
        "revision_id": payload.get("revisionId"),
        "url": document_url(document_id),
        "format": format,
        "tabs": _tab_list(payload),
        # Typed conversions, so the published output schema reads each field's type.
        "body": str(page["body"]),
        "truncated": bool(page["truncated"]),
        "next_offset": page["next_offset"],
        "body_chars": int(page["body_chars"]),
        "body_sha256": str(page["body_sha256"]),
    }


def raw_document_view(payload: dict[str, Any]) -> dict[str, Any]:
    """docs_read_document (raw): Google's Document resource unchanged under ``document``."""
    document_id = payload.get("documentId")
    return {
        "document_id": document_id,
        "title": payload.get("title"),
        "revision_id": payload.get("revisionId"),
        "url": document_url(document_id),
        "format": "raw",
        "document": payload,
    }
