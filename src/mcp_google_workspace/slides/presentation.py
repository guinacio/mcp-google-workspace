"""Readable Google Slides representations.

Pure functions that turn Slides API payloads into the shapes the slides_
tools return: an outline of slides with their text and speaker notes, page
elements typed and in reading order, and geometry in points. Masters,
layouts (once their names are resolved), styles and fills are dropped.
"""

from __future__ import annotations

import math
from typing import Any

EMU_PER_PT = 12700
_TITLE_PLACEHOLDERS = ("TITLE", "CENTERED_TITLE")
# PageElement content field -> element type.
_ELEMENT_TYPES = {
    "shape": "shape",
    "image": "image",
    "table": "table",
    "video": "video",
    "line": "line",
    "elementGroup": "group",
    "sheetsChart": "sheets_chart",
    "wordArt": "word_art",
    "speakerSpotlight": "speaker_spotlight",
}
# An affine transform as (scaleX, shearY, shearX, scaleY, translateX, translateY) in points.
_Matrix = tuple[float, float, float, float, float, float]
_IDENTITY: _Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def presentation_url(presentation_id: str | None) -> str | None:
    return f"https://docs.google.com/presentation/d/{presentation_id}/edit" if presentation_id else None


def to_points(magnitude: float | int | None, unit: str | None) -> float:
    """A Slides length in points (12700 EMU per point), rounded to one decimal."""
    value = float(magnitude or 0)
    return round(value / EMU_PER_PT if unit != "PT" else value, 1)


def _dimension_pt(dimension: dict[str, Any] | None) -> float | None:
    if not isinstance(dimension, dict):
        return None
    return to_points(dimension.get("magnitude"), dimension.get("unit"))


def page_size_envelope(page_size: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(page_size, dict):
        return None
    return {"width_pt": _dimension_pt(page_size.get("width")), "height_pt": _dimension_pt(page_size.get("height"))}


def _matrix(transform: dict[str, Any] | None) -> _Matrix:
    if not isinstance(transform, dict):
        return _IDENTITY
    unit = transform.get("unit")
    return (
        float(transform.get("scaleX", 1.0)),
        float(transform.get("shearY", 0.0)),
        float(transform.get("shearX", 0.0)),
        float(transform.get("scaleY", 1.0)),
        to_points(transform.get("translateX"), unit),
        to_points(transform.get("translateY"), unit),
    )


def _compose(parent: _Matrix, child: _Matrix) -> _Matrix:
    """parent x child: a grouped element's absolute transform (the API preconcatenates group transforms)."""
    a1, b1, c1, d1, e1, f1 = parent
    a2, b2, c2, d2, e2, f2 = child
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def text_content(text: dict[str, Any] | None) -> str | None:
    """The plain text of a Slides TextContent, or None when it holds none."""
    if not isinstance(text, dict):
        return None
    parts: list[str] = []
    for element in text.get("textElements", []):
        run = element.get("textRun") or element.get("autoText")
        if isinstance(run, dict) and run.get("content"):
            parts.append(str(run["content"]))
    # Slides marks soft line breaks with a vertical tab.
    content = "".join(parts).replace("\u000b", "\n").strip()
    return content or None


def _table_text(table: dict[str, Any]) -> str | None:
    rows: list[str] = []
    for row in table.get("tableRows", []):
        cells = [
            " ".join((text_content(cell.get("text")) or "").split())
            for cell in row.get("tableCells", [])
        ]
        if any(cells):
            rows.append(" | ".join(cells).rstrip())
    return "\n".join(rows) or None


def element_type(element: dict[str, Any]) -> str | None:
    return next((kind for key, kind in _ELEMENT_TYPES.items() if key in element), None)


def element_text(element: dict[str, Any]) -> str | None:
    if "shape" in element:
        return text_content(element["shape"].get("text"))
    if "table" in element:
        return _table_text(element["table"])
    if "wordArt" in element:
        return element["wordArt"].get("renderedText") or None
    return None


def _flatten(elements: list[dict[str, Any]], parent: _Matrix = _IDENTITY) -> list[tuple[dict[str, Any], _Matrix]]:
    """Page elements with their absolute transforms; a group is followed by its children."""
    flat: list[tuple[dict[str, Any], _Matrix]] = []
    for element in elements:
        absolute = _compose(parent, _matrix(element.get("transform")))
        flat.append((element, absolute))
        group = element.get("elementGroup")
        if isinstance(group, dict):
            flat.extend(_flatten(group.get("children", []), absolute))
    return flat


def _reading_order(elements: list[dict[str, Any]]) -> list[tuple[dict[str, Any], _Matrix]]:
    """Top-level elements top to bottom, then left to right; group children stay after their group."""
    def position(element: dict[str, Any]) -> tuple[float, float]:
        matrix = _matrix(element.get("transform"))
        return matrix[5], matrix[4]

    return _flatten(sorted(elements, key=position))


def _placeholder(element: dict[str, Any]) -> str | None:
    placeholder = (element.get("shape") or {}).get("placeholder")
    return placeholder.get("type") if isinstance(placeholder, dict) else None


def _outline_element(element: dict[str, Any]) -> dict[str, Any]:
    return {
        "object_id": element.get("objectId"),
        "type": element_type(element),
        "placeholder": _placeholder(element),
        "text": element_text(element),
    }


def _geometry_element(element: dict[str, Any], absolute: _Matrix) -> dict[str, Any]:
    size = element.get("size")
    width = _dimension_pt(size.get("width")) if isinstance(size, dict) else None
    height = _dimension_pt(size.get("height")) if isinstance(size, dict) else None
    scale_x, shear_y, shear_x, scale_y, translate_x, translate_y = absolute
    return {
        "object_id": element.get("objectId"),
        "type": element_type(element),
        "placeholder": _placeholder(element),
        "text": element_text(element),
        "size": (
            {
                "width_pt": round(width * math.hypot(scale_x, shear_y), 1),
                "height_pt": round(height * math.hypot(shear_x, scale_y), 1),
            }
            if width is not None and height is not None
            else None
        ),
        "position": {"x_pt": round(translate_x, 1), "y_pt": round(translate_y, 1)},
    }


def _slide_title(ordered: list[tuple[dict[str, Any], _Matrix]]) -> str | None:
    for element, _ in ordered:
        if _placeholder(element) in _TITLE_PLACEHOLDERS:
            return element_text(element)
    return None


def _slide_text(ordered: list[tuple[dict[str, Any], _Matrix]]) -> str | None:
    lines = [text for element, _ in ordered if (text := element_text(element))]
    return "\n".join(lines) or None


def speaker_notes(page: dict[str, Any]) -> str | None:
    """The speaker notes of a slide: its notes page's speaker-notes shape (the BODY placeholder)."""
    notes_page = (page.get("slideProperties") or {}).get("notesPage")
    if not isinstance(notes_page, dict):
        return None
    elements = [element for element, _ in _flatten(notes_page.get("pageElements", []))]
    speaker_id = (notes_page.get("notesProperties") or {}).get("speakerNotesObjectId")
    target = next((element for element in elements if speaker_id and element.get("objectId") == speaker_id), None)
    if target is None:
        target = next((element for element in elements if _placeholder(element) == "BODY"), None)
    return element_text(target) if target is not None else None


def _layout_names(payload: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for layout in payload.get("layouts", []):
        properties = layout.get("layoutProperties") or {}
        name = properties.get("displayName") or properties.get("name")
        if layout.get("objectId") and name:
            names[layout["objectId"]] = name
    return names


def slide_outline(
    slide: dict[str, Any], index: int, layout_names: dict[str, str], *, geometry: bool = False
) -> dict[str, Any]:
    """One slide's title, text, notes and elements; with ``geometry`` each element adds size and position."""
    ordered = _reading_order(slide.get("pageElements", []))
    layout_id = (slide.get("slideProperties") or {}).get("layoutObjectId")
    return {
        "object_id": slide.get("objectId"),
        "index": index,
        "layout": layout_names.get(layout_id) if layout_id else None,
        "title": _slide_title(ordered),
        "text": _slide_text(ordered),
        "notes": speaker_notes(slide),
        "elements": [
            _geometry_element(element, absolute) if geometry else _outline_element(element)
            for element, absolute in ordered
        ],
    }


def _selected_slides(slides: list[dict[str, Any]], slide_ids: list[str] | None) -> list[tuple[int, dict[str, Any]]]:
    """(deck index, slide) for every slide, or for the requested IDs in the order given."""
    indexed = list(enumerate(slides))
    if not slide_ids:
        return indexed
    by_id = {slide.get("objectId"): (index, slide) for index, slide in indexed}
    requested = list(dict.fromkeys(slide_ids))
    missing = [slide_id for slide_id in requested if slide_id not in by_id]
    if missing:
        raise ValueError(
            f"slide_ids not found in this presentation: {', '.join(missing)}; use the object_id of a "
            "slide from slides_read_presentation"
        )
    return [by_id[slide_id] for slide_id in requested]


def presentation_envelope(payload: dict[str, Any], *, slide_ids: list[str] | None = None) -> dict[str, Any]:
    """A presentation as an outline: every slide's title, text, notes and elements.

    With ``slide_ids`` only those slides, in the order given, each element
    with its size and position.
    """
    layout_names = _layout_names(payload)
    selected = _selected_slides(payload.get("slides", []), slide_ids)
    detailed = bool(slide_ids)
    return {
        "presentation_id": payload.get("presentationId"),
        "title": payload.get("title"),
        "revision_id": payload.get("revisionId"),
        "url": presentation_url(payload.get("presentationId")),
        "page_size": page_size_envelope(payload.get("pageSize")),
        "slides": [slide_outline(slide, index, layout_names, geometry=detailed) for index, slide in selected],
        "count": len(selected),
    }


def page_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """One page with its elements' text and geometry, for layout-aware edits."""
    ordered = _reading_order(payload.get("pageElements", []))
    return {
        "object_id": payload.get("objectId"),
        "page_type": payload.get("pageType"),
        "title": _slide_title(ordered),
        "text": _slide_text(ordered),
        "notes": speaker_notes(payload),
        "elements": [_geometry_element(element, absolute) for element, absolute in ordered],
    }


def thumbnail_envelope(payload: dict[str, Any], *, mime_type: str) -> dict[str, Any]:
    return {
        "content_url": payload.get("contentUrl"),
        "width": payload.get("width"),
        "height": payload.get("height"),
        "mime_type": mime_type,
    }


def created_presentation_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ok",
        "presentation_id": payload.get("presentationId"),
        "title": payload.get("title"),
        "url": presentation_url(payload.get("presentationId")),
        "slides": [
            {"object_id": slide.get("objectId"), "index": index}
            for index, slide in enumerate(payload.get("slides", []))
        ],
    }


def replace_text_envelope(payload: dict[str, Any], *, presentation_id: str) -> dict[str, Any]:
    replies = payload.get("replies") or [{}]
    changed = (replies[0].get("replaceAllText") or {}).get("occurrencesChanged", 0)
    return {
        "status": "ok",
        "presentation_id": payload.get("presentationId") or presentation_id,
        "occurrences_changed": int(changed or 0),
    }


def batch_update_presentation_envelope(payload: dict[str, Any], *, presentation_id: str) -> dict[str, Any]:
    replies = payload.get("replies", [])
    return {
        "status": "ok",
        "presentation_id": payload.get("presentationId") or presentation_id,
        "revision_id": (payload.get("writeControl") or {}).get("requiredRevisionId"),
        "replies": replies,
        "count": len(replies),
    }
