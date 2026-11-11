"""Character-offset paging for long text: email bodies, documents, files.

Every tool that returns long text pages it the same way: ``body`` is one page,
``next_offset`` the character offset to pass back as ``offset`` for the next
one (``null`` on the last page), and ``body_chars`` / ``body_sha256`` describe
the whole text, so a caller can tell whether it changed between pages.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

BODY_LIMIT_CHARS = 8_000  # approximately 2,000 tokens for ordinary prose
# A page may end early at a paragraph, line, sentence or word break, but only
# one found in the last 40% of the requested window.
PAGE_BOUNDARY_WINDOW = 0.4

_PAGE_BREAKS = (
    re.compile(r"\n\s*\n\s*"),  # paragraph
    re.compile(r"\n\s*"),  # line
    re.compile(r"(?<=[.!?\u2026])[\"')\]\u00bb\u201d\u2019]*\s+|(?<=[\u3002\uff01\uff1f])\s*"),  # sentence
    re.compile(r"\s+"),  # word
)


def _extends_previous(character: str) -> bool:
    """Combining marks, variation selectors and skin-tone modifiers attach to the character before."""
    return unicodedata.category(character).startswith("M") or "\U0001f3fb" <= character <= "\U0001f3ff"


def page_text(text: str, offset: int = 0, limit: int = BODY_LIMIT_CHARS) -> tuple[str, int | None]:
    """Return one page of ``text`` from ``offset`` and the offset of the next page.

    A page is at most ``limit`` characters. When the text continues, the page
    ends just after the last paragraph break in the final 40% of the window,
    else the last line, sentence or word break there, so the cut falls between
    words; only text without any whitespace in that window is cut at ``limit``,
    and then never between a character and its combining marks. Pages joined in
    order reproduce ``text`` exactly.
    """
    offset = max(0, offset)
    limit = max(1, limit)
    end = offset + limit
    if end >= len(text):
        return text[offset:], None
    floor = offset + max(1, int(limit * (1 - PAGE_BOUNDARY_WINDOW)))
    for pattern in _PAGE_BREAKS:
        last = None
        for last in pattern.finditer(text, floor, end):
            pass
        if last is not None and last.end() > offset:
            return text[offset : last.end()], last.end()
    cut = end
    while cut > offset + 1 and _extends_previous(text[cut]):
        cut -= 1
    return text[offset:cut], cut


def paged_text(text: str, *, offset: int = 0, limit: int | None = BODY_LIMIT_CHARS) -> dict[str, Any]:
    """One page of ``text`` with the shared paging fields (the rest of it when ``limit`` is None)."""
    if limit is None:
        body, next_offset = text[max(0, offset) :], None
    else:
        body, next_offset = page_text(text, offset, limit)
    return {
        "body": body,
        "truncated": next_offset is not None,
        "next_offset": next_offset,
        "body_chars": len(text),
        "body_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
