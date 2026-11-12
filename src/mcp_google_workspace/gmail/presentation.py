"""Compact, model-friendly representations of Gmail messages."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr, parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any

import pytz

from ..common.text_paging import (  # noqa: F401 - re-exported for Gmail callers
    BODY_LIMIT_CHARS,
    PAGE_BOUNDARY_WINDOW,
    page_text,
    paged_text,
)
from .mime_utils import decode_rfc2047, extract_message_bodies, flatten_parts

PREVIEW_LIMIT_CHARS = 1_000
SNIPPET_LIMIT_CHARS = 150
_HTML_PLACEHOLDERS = {"this message contains html content.", "this email contains html content."}

# Every Unicode format (Cf) character, plus combining and filler characters that
# render as nothing and are used to pad pre-headers (U+034F, Hangul fillers,
# Khmer inherent vowels). Emoji ZWJ sequences lose their joiner and show as the
# separate emoji, which keeps their meaning.
INVISIBLE_CHARACTERS = (
    "\u00ad\u034f\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u115f\u1160"
    "\u17b4\u17b5\u180e\u200b-\u200f\u202a-\u202e\u2060-\u206f\u3164\ufeff\uffa0"
    "\ufff9-\ufffb\U000110bd\U000110cd\U00013430-\U0001343f\U0001bca0-\U0001bca3"
    "\U0001d173-\U0001d17a\U000e0001\U000e0020-\U000e007f"
)
_INVISIBLE = re.compile(f"[{INVISIBLE_CHARACTERS}]+")
# No-break, typographic and braille-blank spaces read as an ordinary space.
_UNICODE_SPACES = re.compile("[\u00a0\u1680\u2000-\u200a\u202f\u205f\u2800\u3000]")
_LINE_SEPARATORS = re.compile("[\u000b\u000c\u0085\u2028\u2029]")
_CONTROLS = re.compile("[\u0000-\u0008\u000e-\u001f\u007f-\u0084\u0086-\u009f]")

_SKIPPED_TAGS = frozenset({"head", "title", "style", "script", "template", "noscript"})
_HEAD_TAGS = frozenset({"title", "style", "script", "meta", "link", "base", "noscript", "template"})
_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)
_PARAGRAPH_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "table", "ul", "ol", "pre", "hr"})
_BLOCK_TAGS = frozenset(
    {
        "p", "div", "li", "tr", "br", "section", "article", "header", "footer", "main", "nav", "aside",
        "center", "dl", "dt", "dd", "address", "figure", "figcaption", "form", "fieldset", "caption",
    }
)
_CELL_TAGS = frozenset({"td", "th"})
# Start tags that implicitly close an open element of the listed kinds, as in HTML.
_IMPLICIT_CLOSE = {
    "p": frozenset({"p"}),
    "li": frozenset({"li"}),
    "tr": frozenset({"tr", "td", "th"}),
    "td": frozenset({"td", "th"}),
    "th": frozenset({"td", "th"}),
    "dt": frozenset({"dt", "dd"}),
    "dd": frozenset({"dt", "dd"}),
    "option": frozenset({"option"}),
}
_ZERO_LENGTH = r"0(?:\.0+)?(?:px|pt|em|rem|%)?(?:!important)?(?:;|$)"
_HIDDEN_STYLE = re.compile(rf"(?:^|;)(?:display:none|opacity:{_ZERO_LENGTH}|max-height:{_ZERO_LENGTH}|max-width:{_ZERO_LENGTH})")
_FONT_SIZE = re.compile(r"(?:^|;)font-size:(\d*\.?\d+)(px|pt|em|rem|%)?")
_VISIBILITY = re.compile(r"(?:^|;)visibility:(hidden|visible|collapse)")
_LINK_SCHEME = re.compile(r"(?:https?://|mailto:)(?:www\.)?", re.I)
# A link text longer than this many text nodes is never just its URL.
_LINK_LABEL_CHUNKS = 64


@dataclass
class _Frame:
    tag: str
    hidden: bool = False  # display:none and friends: no descendant can show again
    invisible: bool = False  # visibility:hidden, which a descendant may override
    tiny: bool = False  # font-size of 0 or 1px, which a descendant may override
    pre: bool = False
    href: str | None = None
    link_start: int = 0


class _HTMLText(HTMLParser):
    """Render the text a mail client would show, as plain lines and paragraphs.

    Every step is amortized constant time per tag or text node (running
    counters instead of rescans), because the markup comes from any sender.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.stack: list[_Frame] = [_Frame("#root")]
        self.open_tags: Counter[str] = Counter()
        self.trailing_newlines = 0
        self.links_seen: set[str] = set()

    @property
    def _top(self) -> _Frame:
        return self.stack[-1]

    def _shown(self) -> bool:
        top = self._top
        return not (top.hidden or top.invisible or top.tiny)

    def _append(self, text: str) -> None:
        self.chunks.append(text)
        stripped = text.rstrip(" ")
        newlines = len(stripped) - len(stripped.rstrip("\n"))
        if stripped.strip("\n"):
            self.trailing_newlines = newlines
        else:
            self.trailing_newlines += newlines

    def _emit_break(self, tag: str) -> None:
        # Hidden subtrees emit nothing; tiny or invisible ones may hold visible blocks.
        if self._top.hidden:
            return
        if tag in _CELL_TAGS:
            self._append(" ")
            return
        if tag == "br":  # an explicit line break, so <br><br> keeps its blank line
            self._append("\n")
            return
        wanted = 2 if tag in _PARAGRAPH_TAGS else 1 if tag in _BLOCK_TAGS else 0
        missing = wanted - self.trailing_newlines
        if missing > 0 and self.chunks:
            self._append("\n" * missing)

    def _child_frame(self, tag: str, attrs: list[tuple[str, str | None]]) -> _Frame:
        parent = self._top
        attributes = {name.lower(): (value or "") for name, value in attrs}
        style = re.sub(r"\s+", "", attributes.get("style", "").lower())
        frame = _Frame(
            tag,
            hidden=(
                parent.hidden
                or tag in _SKIPPED_TAGS
                or "hidden" in attributes
                or attributes.get("aria-hidden", "").strip().lower() == "true"
                or bool(_HIDDEN_STYLE.search(style))
            ),
            invisible=parent.invisible,
            tiny=parent.tiny,
            pre=parent.pre or tag == "pre",
        )
        if visibility := _VISIBILITY.search(style):
            frame.invisible = visibility.group(1) != "visible"
        if font_size := _FONT_SIZE.search(style):
            size, unit = float(font_size.group(1)), font_size.group(2) or "px"
            frame.tiny = size == 0 or (unit in {"px", "pt"} and size <= 1)
        if tag == "a":
            frame.href = attributes.get("href", "").strip()
            frame.link_start = len(self.chunks)
        return frame

    def _push(self, frame: _Frame) -> None:
        self.stack.append(frame)
        self.open_tags[frame.tag] += 1

    def _pop(self) -> None:
        frame = self.stack.pop()
        self.open_tags[frame.tag] -= 1
        if frame.href is None or frame.hidden or frame.invisible or frame.tiny:
            return
        # Tracking queries and fragments are dropped, as clean_whitespace does for text.
        url = _bare_link(frame.href)
        if not _LINK_SCHEME.match(url) or url in self.links_seen:
            return
        # Only a short link text is inspected; a longer one always shows the URL.
        if len(self.chunks) - frame.link_start <= _LINK_LABEL_CHUNKS:
            label = re.sub(r"\s+", "", "".join(self.chunks[frame.link_start :]))
            if not label:
                return  # an image or empty link: nothing to attach the URL to
            # The URL is left out only when the text is exactly that URL; any other
            # text, including text that merely contains it, shows the destination.
            if _comparable_link(label) == _comparable_link(url):
                return
        self.links_seen.add(url)
        self._append(f" ({url})")

    def _close_through(self, index: int) -> None:
        while len(self.stack) > index:
            self._pop()

    def _close_nearest(self, tag: str) -> bool:
        if not self.open_tags[tag]:
            return False
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self._close_through(index)
                return True
        return False  # pragma: no cover - open_tags mirrors the stack

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.open_tags["head"] and (tag == "body" or tag not in _HEAD_TAGS):
            self._close_nearest("head")  # a body or body content ends an unclosed <head>
        closers = _IMPLICIT_CLOSE.get(tag)
        while closers and len(self.stack) > 1 and self._top.tag in closers:
            self._pop()
        self._emit_break(tag)
        if tag not in _VOID_TAGS:
            self._push(self._child_frame(tag, attrs))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        self._close_nearest(tag)
        self._emit_break(tag)

    def handle_data(self, data: str) -> None:
        if not self._shown():
            return
        self._append(data if self._top.pre else re.sub(r"[ \t\r\n\f]+", " ", data))

    def text(self) -> str:
        self._close_through(1)
        return "".join(self.chunks)


def _bare_link(href: str) -> str:
    return re.sub(r"[?#].*$", "", href)


def _comparable_link(value: str) -> str:
    return _LINK_SCHEME.sub("", _bare_link(value)).rstrip("/").casefold()


def header_map(payload: dict[str, Any]) -> dict[str, str]:
    return {
        str(header.get("name", "")).lower(): str(header.get("value", ""))
        for header in payload.get("headers", [])
        if isinstance(header, dict)
    }


def remove_invisible_characters(value: str) -> str:
    """Drop zero-width, format and control characters; read odd spaces as spaces."""
    value = _INVISIBLE.sub("", value)
    value = _CONTROLS.sub("", value)
    value = _LINE_SEPARATORS.sub("\n", value)
    return _UNICODE_SPACES.sub(" ", value)


def html_to_text(value: str) -> str:
    """Convert an HTML body to the text a reader sees.

    Skips head, style, script, title, template and noscript content and any
    element hidden inline (display:none, max-height:0, opacity:0, the hidden
    attribute, aria-hidden, visibility:hidden, font-size of 0 or 1px), emits
    line and paragraph breaks for block elements, and keeps a link's URL once,
    after its text, unless the text is that URL. ``mso-hide:all`` alone hides
    content from Outlook only, so that content is kept.
    """
    parser = _HTMLText()
    try:
        parser.feed(value)
        parser.close()
    except (AssertionError, ValueError):  # pragma: no cover - malformed markup
        pass
    return clean_text(unescape(parser.text()))


def clean_whitespace(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"(https?://[^\s?#]+)(?:\?[^\s#]*)?(?:#[^\s]*)?", r"\1", value)
    value = re.sub(r"_{3,}", " ", value)
    value = re.sub(r"([!?.,=])\1{2,}", r"\1", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    # Strip trailing spaces, except on a "-- " signature delimiter line.
    value = re.sub(r"(?m)(?<!^--)[ \t]+$", "", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def clean_text(value: str) -> str:
    """Normalize decoded text: invisible characters, spaces, tracking queries, blank runs."""
    return clean_whitespace(remove_invisible_characters(value))


_MOBILE_FOOTER = re.compile(r"(?:Sent from my |Get Outlook for ).+", re.I)


def clean_body(value: str) -> tuple[str, int]:
    """Remove common signatures and collapse copied reply chains."""
    text = clean_text(value)
    if "\n-- \n" in text:
        text = text.split("\n-- \n", 1)[0].rstrip()
    lines = text.split("\n")
    while len(lines) > 1 and _MOBILE_FOOTER.fullmatch(lines[-1]):
        lines.pop()
    text = "\n".join(lines).rstrip()
    quoted = re.search(r"\n(?:On .+ wrote:|From:.+\nSent:.+\nTo:|>{1,})", text, flags=re.I)
    if not quoted:
        return text, 0
    remainder = text[quoted.start() :]
    earlier = max(1, len(re.findall(r"(?:^|\n)(?:On .+ wrote:|From:|>)", remainder, flags=re.I)))
    marker = f"[quoted: {earlier} earlier message{'s' if earlier != 1 else ''} in thread]"
    head = text[: quoted.start()].rstrip()
    return (f"{head}\n\n{marker}" if head else marker), earlier


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
    if text and clean_text(text).casefold() not in _HTML_PLACEHOLDERS:
        return text
    return html_to_text(bodies.get("html", "")) or text


def cleaned_message_body(message: dict[str, Any]) -> tuple[str, int]:
    return clean_body(message_body(message))


def clean_snippet(value: str, *, html_escaped: bool = True, limit: int = SNIPPET_LIMIT_CHARS) -> str:
    """One decoded, de-padded line of at most ``limit`` characters, cut between words.

    Gmail's own ``snippet`` is HTML-escaped (``&#39;``) and carries pre-header
    padding; body text passed with ``html_escaped=False`` is not unescaped again.
    """
    text = clean_text(unescape(value) if html_escaped else value)
    text = re.sub(r"\s+", " ", text).strip()
    return page_text(text, 0, limit)[0].rstrip()


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


def message_snippet(message: dict[str, Any]) -> str:
    """Gmail's own snippet, decoded; the cleaned body only when Gmail sent none.

    Search, digest, thread and read results all take this path, so one message
    has the same snippet everywhere whether or not its body was fetched.
    """
    gmail_snippet = message.get("snippet")
    if isinstance(gmail_snippet, str) and (snippet := clean_snippet(gmail_snippet)):
        return snippet
    body, _ = cleaned_message_body(message)
    return clean_snippet(body, html_escaped=False)


def envelope(message: dict[str, Any], *, account_timezone: str) -> dict[str, Any]:
    payload = message.get("payload", {})
    headers = header_map(payload)
    sender = decode_rfc2047(headers.get("from"))
    name, email = parseaddr(sender)
    labels = message.get("labelIds", [])
    categories = [label for label in labels if isinstance(label, str) and label.startswith("CATEGORY_")]
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
        "snippet": message_snippet(message),
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


def clean_message_content(
    message: dict[str, Any], *, offset: int = 0, limit: int | None = BODY_LIMIT_CHARS
) -> dict[str, Any]:
    """One page of the cleaned body (the whole body when ``limit`` is None).

    ``body_chars`` and ``body_sha256`` describe the whole cleaned body, so a
    caller paging with ``next_offset`` can tell whether the message changed
    between pages.
    """
    clean, quoted_messages = cleaned_message_body(message)
    return {**paged_text(clean, offset=offset, limit=limit), "quoted_messages_collapsed": quoted_messages}


# ---------------------------------------------------------------------------
# Drafts, labels, filters and settings
# ---------------------------------------------------------------------------


def draft_summary(draft: dict[str, Any]) -> dict[str, Any]:
    """The draft a save returns: the draft_id the other draft tools take and its message's IDs."""
    message = draft.get("message") or {}
    return {
        "id": draft.get("id"),
        "message_id": message.get("id"),
        "thread_id": message.get("threadId"),
        "label_ids": message.get("labelIds"),
    }


def _sender(value: str | None) -> dict[str, str] | None:
    if value is None:
        return None
    decoded = decode_rfc2047(value) or ""
    name, email = parseaddr(decoded)
    return {"name": name or decoded, "email": email}


def draft_detail(draft: dict[str, Any], *, format: str, account_timezone: str) -> dict[str, Any]:
    """One draft read with drafts.get, in the names gmail_read_emails uses.

    Headers come only with formats 'full' and 'metadata', attachments only with
    'full'; what the format did not fetch is null. 'full' adds the whole cleaned
    body, 'raw' the base64url RFC 2822 message as raw.
    """
    message = draft.get("message") or {}
    payload = message.get("payload")
    headers = header_map(payload) if isinstance(payload, dict) else None
    snippet = message.get("snippet")

    def header(name: str) -> str | None:
        if headers is None or name not in headers:
            return None
        return decode_rfc2047(headers[name])

    item: dict[str, Any] = {
        "id": draft.get("id"),
        "message_id": message.get("id"),
        "thread_id": message.get("threadId"),
        "label_ids": message.get("labelIds"),
        "date": _received_at_in_account_timezone(message, headers or {}, account_timezone),
        "from": _sender(headers.get("from")) if headers is not None else None,
        "to": header("to"),
        "cc": header("cc"),
        "bcc": header("bcc"),
        "subject": header("subject"),
        "snippet": clean_snippet(snippet) if isinstance(snippet, str) else snippet,
        "attachments": (
            message_attachments(payload, include_download_id=True)
            if format == "full" and isinstance(payload, dict)
            else None
        ),
    }
    if format == "full":
        # The same cleaned text as gmail_read_emails, whole and unpaged.
        item.update(clean_message_content(message, limit=None))
    elif format == "raw":
        item["raw"] = message.get("raw")
    return item


def label_envelope(label: dict[str, Any]) -> dict[str, Any]:
    """A Gmail label: its ID for the other label tools, name, type and how Gmail shows it."""
    color = label.get("color") or {}
    return {
        "id": label.get("id"),
        "name": label.get("name"),
        "type": label.get("type"),
        "message_list_visibility": label.get("messageListVisibility"),
        "label_list_visibility": label.get("labelListVisibility"),
        "background_color": color.get("backgroundColor"),
        "text_color": color.get("textColor"),
    }


# Gmail filter criteria and action fields, in the names gmail_manage_filters takes.
_FILTER_CRITERIA = {
    "from": "from",
    "to": "to",
    "subject": "subject",
    "query": "query",
    "negatedQuery": "negated_query",
    "hasAttachment": "has_attachment",
    "excludeChats": "exclude_chats",
    "size": "size",
    "sizeComparison": "size_comparison",
}
_FILTER_ACTION = {"addLabelIds": "add_label_ids", "removeLabelIds": "remove_label_ids", "forward": "forward"}


def filter_envelope(gmail_filter: dict[str, Any]) -> dict[str, Any]:
    """A Gmail filter: its id (for delete), the criteria a message must all match and the action taken.

    Criteria and action list only the fields the filter sets: an absent one is
    not a condition, so it is left out rather than reported as null.
    """
    criteria = gmail_filter.get("criteria") or {}
    action = gmail_filter.get("action") or {}
    return {
        "id": gmail_filter.get("id"),
        "criteria": {name: criteria[key] for key, name in _FILTER_CRITERIA.items() if key in criteria},
        "action": {name: action[key] for key, name in _FILTER_ACTION.items() if key in action},
    }


def forwarding_address_envelope(address: dict[str, Any]) -> dict[str, Any]:
    return {
        "forwarding_email": address.get("forwardingEmail"),
        "verification_status": address.get("verificationStatus"),
    }


def _epoch_millis(value: Any) -> int | None:
    """Gmail sends int64 values as strings; the tool takes and returns epoch milliseconds as numbers."""
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def vacation_envelope(settings: dict[str, Any]) -> dict[str, Any]:
    """The vacation auto-reply, in the parameter names gmail_manage_vacation takes."""
    return {
        "enable_auto_reply": settings.get("enableAutoReply"),
        "response_subject": settings.get("responseSubject"),
        "response_body_plain_text": settings.get("responseBodyPlainText"),
        "response_body_html": settings.get("responseBodyHtml"),
        "restrict_to_contacts": settings.get("restrictToContacts"),
        "restrict_to_domain": settings.get("restrictToDomain"),
        "start_time": _epoch_millis(settings.get("startTime")),
        "end_time": _epoch_millis(settings.get("endTime")),
    }
