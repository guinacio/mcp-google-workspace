"""Compact, model-friendly representations of Gmail messages."""

from __future__ import annotations

import re
from datetime import datetime
from email.utils import parseaddr, parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any

import pytz

from .mime_utils import decode_rfc2047, extract_message_bodies, flatten_parts

BODY_LIMIT_CHARS = 8_000  # approximately 2,000 tokens for ordinary email text
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


def _received_at_in_account_timezone(
    message: dict[str, Any], headers: dict[str, str], account_timezone: str
) -> str | None:
    timezone = pytz.timezone(account_timezone)
    internal_date = message.get("internalDate")
    try:
        if internal_date is not None:
            return datetime.fromtimestamp(int(internal_date) / 1000, tz=pytz.UTC).astimezone(timezone).isoformat()
    except (TypeError, ValueError, OSError):
        pass
    try:
        header_date = parsedate_to_datetime(headers.get("date", ""))
        if header_date.tzinfo is not None:
            return header_date.astimezone(timezone).isoformat()
    except (TypeError, ValueError):
        pass
    return None


def envelope(message: dict[str, Any], *, account_timezone: str) -> dict[str, Any]:
    payload = message.get("payload", {})
    headers = header_map(payload)
    sender = decode_rfc2047(headers.get("from"))
    name, email = parseaddr(sender)
    labels = message.get("labelIds", [])
    categories = [label for label in labels if isinstance(label, str) and label.startswith("CATEGORY_")]
    source = message_body(message) or message.get("snippet", "")
    snippet, _ = clean_body(source)
    snippet = clean_whitespace(snippet).replace("\n", " ")[:150]
    sender_email = email.lower()
    sender_local_part, _, sender_domain = sender_email.partition("@")
    github_notification = (
        "x-github-reason" in headers
        or (
            sender_domain == "github.com"
            and sender_local_part in {"notifications", "noreply"}
        )
    )
    automated = (
        headers.get("precedence", "").lower() == "bulk"
        or bool(re.search(r"(?:no[._-]?reply|donotreply|mailer-daemon)@", sender_email))
        or github_notification
    )
    received_at = _received_at_in_account_timezone(message, headers, account_timezone)
    return {
        "id": message.get("id"),
        "thread_id": message.get("threadId"),
        "from": {"name": name or sender, "email": email},
        "subject": decode_rfc2047(headers.get("subject")),
        "date": received_at,
        "date_timezone": account_timezone if received_at else None,
        "source_date": headers.get("date"),
        "snippet": snippet,
        "category": categories[0] if categories else None,
        "unread": "UNREAD" in labels,
        "has_attachments": bool(message_attachments(payload)),
        "is_newsletter": "list-unsubscribe" in headers,
        "is_automated": automated,
        "is_draft": "DRAFT" in labels,
        "is_sent": "SENT" in labels,
    }


def mail_feed_envelope(message: dict[str, Any], *, account_timezone: str) -> dict[str, Any] | None:
    """Return an unbiased received/sent summary, or ``None`` for drafts.

    Digest and update-feed callers should not receive inferred categories that
    encourage them to deprioritize routed, newsletter, or automated messages.
    """
    item = envelope(message, account_timezone=account_timezone)
    if item["is_draft"]:
        return None
    item["direction"] = "sent" if item["is_sent"] else "received"
    for field in ("category", "is_newsletter", "is_automated", "is_draft", "is_sent"):
        item.pop(field, None)
    return item


def clean_message_content(message: dict[str, Any], *, offset: int = 0, limit: int = BODY_LIMIT_CHARS) -> dict[str, Any]:
    clean, quoted_messages = cleaned_message_body(message)
    end = offset + limit
    return {
        "body": clean[offset:end],
        "truncated": len(clean) > end,
        "next_offset": end if len(clean) > end else None,
        "quoted_messages_collapsed": quoted_messages,
    }
