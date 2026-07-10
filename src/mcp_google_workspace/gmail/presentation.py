"""Compact, model-friendly representations of Gmail messages."""

from __future__ import annotations

import re
from datetime import datetime, tzinfo
from email.utils import parseaddr, parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any

import pytz

from .mime_utils import decode_rfc2047, extract_message_bodies, flatten_parts

BODY_LIMIT_CHARS = 8_000  # approximately 2,000 tokens for ordinary email text
_HTML_PLACEHOLDERS = {"this message contains html content.", "this email contains html content."}
_DEADLINE_MARKERS = re.compile(
    r"\b(?:deadline|due(?:\s+date)?|respond|reply|response|by|before|até|prazo|data\s+limite|devolutiva\s+até)\b",
    re.IGNORECASE,
)
_DATE_TOKEN = re.compile(r"\b(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b")
_TIME_TOKEN = re.compile(r"\b(?P<hour>[01]?\d|2[0-3])(?::(?P<minute>[0-5]\d))?\s*(?:h|hrs?|am|pm)\b", re.IGNORECASE)
_RESPONSE_LANGUAGE = re.compile(
    r"\?|\b(?:please|can you|could you|let me know|respond|reply|deadline|due|responda|retorne|devolutiva)\b",
    re.IGNORECASE,
)
_GREETING_SENTENCE = re.compile(
    r"^(?:ol[áa]|oi|hi|hello|dear|prezad[oa])"
    r"(?:\s*,?\s*(?:[A-ZÀ-Ý][\wÀ-ÿ.'-]*)(?:\s+[A-ZÀ-Ý][\wÀ-ÿ.'-]*){0,2})?[!,.]*$",
    re.IGNORECASE,
)


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


def first_meaningful_sentence(text: str, *, max_length: int = 240) -> str | None:
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        candidate = sentence.strip()
        if (
            len(candidate) >= 12
            and not candidate.startswith("[quoted:")
            and not _GREETING_SENTENCE.fullmatch(candidate)
        ):
            return candidate[:max_length]
    return None


def _reference_datetime(date_header: str | None) -> datetime:
    if date_header:
        try:
            parsed = parsedate_to_datetime(date_header)
            if parsed.tzinfo is not None:
                return parsed
        except (TypeError, ValueError):
            pass
    return datetime.now().astimezone()


def _deadline_timezone(account_timezone: str | None, reference: datetime) -> tzinfo:
    if account_timezone:
        try:
            return pytz.timezone(account_timezone)
        except pytz.UnknownTimeZoneError:
            pass
    if reference.tzinfo is not None:
        return reference.tzinfo
    local_timezone = datetime.now().astimezone().tzinfo
    if local_timezone is None:  # pragma: no cover - datetime always provides one locally
        return pytz.UTC
    return local_timezone


def _normalize_deadline(
    value: str,
    reference: datetime,
    *,
    account_timezone: str | None = None,
) -> str | None:
    date_match = _DATE_TOKEN.search(value)
    time_match = _TIME_TOKEN.search(value)
    if not date_match and not time_match:
        return None

    deadline_timezone = _deadline_timezone(account_timezone, reference)
    reference = reference.astimezone(deadline_timezone)

    if date_match:
        token = date_match.group(0).replace("-", "/")
        if re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", token):
            year, month, day = (int(part) for part in token.split("/"))
        else:
            parts = [int(part) for part in token.split("/")]
            day, month = parts[:2]
            year = parts[2] if len(parts) == 3 else reference.year
            if year < 100:
                year += 2000
    else:
        year, month, day = reference.year, reference.month, reference.day

    hour = int(time_match.group("hour")) if time_match else 23
    minute = int(time_match.group("minute") or 0) if time_match else 59
    if time_match and time_match.group(0).casefold().endswith("pm") and hour < 12:
        hour += 12
    try:
        deadline_naive = datetime(
            year,
            month,
            day,
            hour,
            minute,
        )
        deadline = (
            deadline_timezone.localize(deadline_naive)
            if isinstance(deadline_timezone, pytz.BaseTzInfo)
            else deadline_naive.replace(tzinfo=deadline_timezone)
        )
    except ValueError:
        return None
    if not date_match:
        return deadline.isoformat(timespec="minutes")
    if year == reference.year and deadline < reference and (reference - deadline).days > 180:
        deadline = deadline.replace(year=year + 1)
    return deadline.isoformat(timespec="minutes") if time_match else deadline.date().isoformat()


def detect_deadline(
    text: str,
    *,
    date_header: str | None = None,
    account_timezone: str | None = None,
) -> str | None:
    """Return an ISO deadline only when a marker is paired with a real date/time token.

    A deadline phrase without its own timezone is interpreted in the account's
    Calendar timezone, not the message header's transport timezone.
    """
    reference = _reference_datetime(date_header)
    for marker in _DEADLINE_MARKERS.finditer(text):
        candidate = text[marker.end() : marker.end() + 120]
        # Bare "by" is common in prose; only accept it when a date/time begins immediately after it.
        if marker.group(0).casefold() == "by" and not re.match(r"\s*(?:on\s+)?(?:\d{1,4}|[01]?\d(?::\d{2})?\s*(?:am|pm|h))", candidate, re.I):
            continue
        if normalized := _normalize_deadline(
            candidate,
            reference,
            account_timezone=account_timezone,
        ):
            return normalized
    return None


def requires_response(text: str, *, is_automated: bool, is_newsletter: bool) -> bool:
    return not is_automated and not is_newsletter and bool(_RESPONSE_LANGUAGE.search(text))


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
    }


def clean_message_content(message: dict[str, Any], *, offset: int = 0, limit: int = BODY_LIMIT_CHARS) -> dict[str, Any]:
    clean, quoted_messages = cleaned_message_body(message)
    end = offset + limit
    return {
        "body": clean[offset:end],
        "truncated": len(clean) > end,
        "next_offset": end if len(clean) > end else None,
        "quoted_messages_collapsed": quoted_messages,
    }
