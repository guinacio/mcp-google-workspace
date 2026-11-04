"""Frozen copy of the Gmail body cleaner as shipped in 1.0.1.

Kept only so the corpus test and ``scripts/measure_tokens.py`` can compare the
current pipeline against it. Do not import it from the package and do not fix
it: its faults (CSS and pre-header padding in the text, fixed-offset cuts,
undecoded snippets) are what the comparison measures.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any

from mcp_google_workspace.gmail.mime_utils import extract_message_bodies

BODY_LIMIT_CHARS = 8_000
_HTML_PLACEHOLDERS = {"this message contains html content.", "this email contains html content."}


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self.chunks.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.chunks.append("\n")

    def text(self) -> str:
        return "".join(self.chunks)


def html_to_text(value: str) -> str:
    parser = _HTMLText()
    parser.feed(value)
    return clean_whitespace(unescape(parser.text()))


def clean_whitespace(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"(https?://[^\s?#]+)(?:\?[^\s#]*)?(?:#[^\s]*)?", r"\1", value)
    value = re.sub(r"_{3,}", " ", value)
    value = re.sub(r"([!?.,=])\1{2,}", r"\1", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def clean_body(value: str) -> tuple[str, int]:
    """Remove common signatures and collapse copied reply chains."""
    text = clean_whitespace(value)
    if "\n-- \n" in text:
        text = text.split("\n-- \n", 1)[0].rstrip()
    text = re.sub(r"\n(?:Sent from my .+|Get Outlook for .+)$", "", text, flags=re.I)
    quoted = re.search(r"\n(?:On .+ wrote:|From:.+\nSent:.+\nTo:|>{1,})", text, flags=re.I)
    if not quoted:
        return text, 0
    remainder = text[quoted.start() :]
    earlier = max(1, len(re.findall(r"(?:^|\n)(?:On .+ wrote:|From:|>)", remainder, flags=re.I)))
    return f"{text[:quoted.start()].rstrip()}\n\n[quoted: {earlier} earlier message{'s' if earlier != 1 else ''} in thread]", earlier


def message_body(message: dict[str, Any]) -> str:
    """Prefer meaningful text, falling back to converted HTML when text is a MIME placeholder."""
    bodies = extract_message_bodies(message.get("payload", {}))
    text = (bodies.get("text") or "").strip()
    html_text = html_to_text(bodies.get("html", ""))
    if text and text.casefold() not in _HTML_PLACEHOLDERS:
        return text
    return html_text or text


def cleaned_message_body(message: dict[str, Any]) -> tuple[str, int]:
    return clean_body(message_body(message))



def clean_message_content(message: dict[str, Any], *, offset: int = 0, limit: int = BODY_LIMIT_CHARS) -> dict[str, Any]:
    clean, quoted_messages = cleaned_message_body(message)
    end = offset + limit
    return {
        "body": clean[offset:end],
        "truncated": len(clean) > end,
        "next_offset": end if len(clean) > end else None,
        "quoted_messages_collapsed": quoted_messages,
    }


def snippet(message: dict[str, Any]) -> str:
    source = message_body(message) or message.get("snippet", "")
    text, _ = clean_body(source)
    return clean_whitespace(text).replace("\n", " ")[:150]
