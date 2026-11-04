"""Element paging for Google resources returned unchanged (``format=raw``).

A raw resource is Google's JSON object as the API returned it. It can be far
larger than one tool result should be (a deck's layouts and masters, a
spreadsheet's grid data), so it is paged the way the Docs outline is: by whole
elements, never cutting one.

The paging unit is one element of the resource's top-level lists (a sheet, a
slide, a layout, a form item, ...), counted across those lists in a fixed
order: the tool's main list first, then every other non-empty list in key
order. Every other field (identity, properties, settings, empty lists) comes
on the first page. A page holds about ``limit`` serialized characters; an
element larger than that comes alone on its page, and one larger than
``RAW_ELEMENT_MAX_CHARS`` fails the call with ``invalid_input`` naming how to
narrow the read and the offset that skips it.

Each page's resource has the resource's own keys: on the first page every
field plus the slices of the lists the page reaches, on later pages only
``id_key`` (when given) and those slices. Concatenating each list's slices in page order,
with the first page's other fields, gives Google's resource exactly; a
resource that fits one page is returned as Google sent it. ``element_count``,
``raw_chars`` and ``raw_sha256`` describe the whole resource, so a caller can
tell whether it changed between pages.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .errors import RecoverableToolError

# Serialized characters per raw page (about 8,000 tokens of JSON): twice the
# Docs outline page, since raw elements carry every style and transform.
RAW_LIMIT_CHARS = 32_000
# One element alone on its page may reach four pages; past that the read must
# be narrowed instead of returning an unbounded result.
RAW_ELEMENT_MAX_CHARS = 4 * RAW_LIMIT_CHARS


def _serialized(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _list_order(resource: dict[str, Any], lists: tuple[str, ...]) -> list[str]:
    """The paged lists in paging order: ``lists`` first, then the other non-empty lists by key."""
    present = [key for key, value in resource.items() if isinstance(value, list) and value]
    return [key for key in lists if key in present] + sorted(key for key in present if key not in lists)


def raw_resource_page(
    resource: dict[str, Any],
    *,
    id_key: str | None,
    lists: tuple[str, ...],
    offset: int = 0,
    limit: int = RAW_LIMIT_CHARS,
    narrow_hint: str,
) -> dict[str, Any]:
    """One page of ``resource`` (``page``) with ``next_offset`` and the whole resource's size fields.

    ``narrow_hint`` completes the oversized-element error ("narrow ranges",
    "read it with slide_ids", ...).
    """
    order = _list_order(resource, lists)
    elements = [(key, index) for key in order for index in range(len(resource[key]))]
    total = len(elements)
    offset = min(max(0, offset), total)
    fields = {key: value for key, value in resource.items() if key not in order}
    used = len(_serialized(fields)) if offset == 0 else 0
    end = offset
    for key, index in elements[offset:]:
        cost = len(_serialized(resource[key][index]))
        if end == offset and cost > RAW_ELEMENT_MAX_CHARS:
            raise RecoverableToolError(
                "invalid_input",
                f"{key}[{index}] (element {offset}) is {cost:,} characters of JSON, above the "
                f"{RAW_ELEMENT_MAX_CHARS:,} one raw page can hold; {narrow_hint}, or skip it with "
                f"offset {offset + 1}.",
                required_action={"action": "correct_arguments", "field_errors": []},
                details={"offset": offset, "element_chars": cost, "next_offset": offset + 1 if offset + 1 < total else None},
            )
        if end > offset and used + cost > limit:
            break
        used += cost
        end += 1
    next_offset = end if end < total else None
    if offset == 0 and next_offset is None:
        page = resource
    else:
        page = dict(fields) if offset == 0 else {id_key: resource.get(id_key)} if id_key else {}
        first = 0
        for key in order:
            last = first + len(resource[key])
            if first < end and last > offset:
                page[key] = resource[key][max(offset - first, 0) : end - first]
            first = last
    whole = _serialized(resource)
    return {
        "page": page,
        "next_offset": next_offset,
        "element_count": total,
        "raw_chars": len(whole),
        "raw_sha256": hashlib.sha256(whole.encode("utf-8")).hexdigest(),
    }
