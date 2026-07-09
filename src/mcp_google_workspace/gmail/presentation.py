"""Compact, model-friendly representations of Gmail messages."""

from __future__ import annotations

import re
from email.utils import parseaddr
from html import unescape
from html.parser import HTMLParser
from typing import Any

from .mime_utils import decode_rfc2047, extract_message_bodies, flatten_parts

BODY_LIMIT_CHARS = 8_000  # approximately 2,000 tokens for ordinary email text


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


def header_map(payload: dict[str, Any]) -> dict[str, str]:
    return {
        str(header.get("name", "")).lower(): str(header.get("value", ""))
        for header in payload.get("headers", [])
        if isinstance(header, dict)
    }


def html_to_text(value: str) -> str:
    parser = _HTMLText()
    parser.feed(value)
    return clean_whitespace(unescape(parser.text()))


def clean_whitespace(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
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


def message_attachments(payload: dict[str, Any], *, include_download_id: bool = False) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for part in flatten_parts(payload):
        filename = part.get("filename")
        body = part.get("body", {})
        attachment_id = body.get("attachmentId") if isinstance(body, dict) else None
        if filename and attachment_id:
            item = {"filename": filename, "mime_type": part.get("mimeType"), "size": body.get("size", 0)}
            if include_download_id:
                item["download_id"] = attachment_id
            attachments.append(item)
    return attachments


def envelope(message: dict[str, Any]) -> dict[str, Any]:
    payload = message.get("payload", {})
    headers = header_map(payload)
    sender = decode_rfc2047(headers.get("from"))
    name, email = parseaddr(sender)
    labels = message.get("labelIds", [])
    categories = [label for label in labels if isinstance(label, str) and label.startswith("CATEGORY_")]
    bodies = extract_message_bodies(payload)
    source = bodies.get("text") or html_to_text(bodies.get("html", "")) or message.get("snippet", "")
    snippet, _ = clean_body(source)
    snippet = clean_whitespace(snippet).replace("\n", " ")[:150]
    sender_email = email.lower()
    automated = (
        headers.get("precedence", "").lower() == "bulk"
        or bool(re.search(r"(?:no[._-]?reply|donotreply|mailer-daemon)@", sender_email))
    )
    return {
        "id": message.get("id"),
        "thread_id": message.get("threadId"),
        "from": {"name": name or sender, "email": email},
        "subject": decode_rfc2047(headers.get("subject")),
        "date": headers.get("date"),
        "snippet": snippet,
        "category": categories[0] if categories else None,
        "unread": "UNREAD" in labels,
        "has_attachments": bool(message_attachments(payload)),
        "is_newsletter": "list-unsubscribe" in headers,
        "is_automated": automated,
    }


def clean_message_content(message: dict[str, Any], *, offset: int = 0, limit: int = BODY_LIMIT_CHARS) -> dict[str, Any]:
    payload = message.get("payload", {})
    bodies = extract_message_bodies(payload)
    source = bodies.get("text") or html_to_text(bodies.get("html", ""))
    clean, quoted_messages = clean_body(source)
    end = offset + limit
    return {
        "body": clean[offset:end],
        "truncated": len(clean) > end,
        "next_offset": end if len(clean) > end else None,
        "quoted_messages_collapsed": quoted_messages,
    }
