"""Gmail body and snippet hygiene: corpus, properties, paging and tool shapes.

The corpus in ``tests/fixtures/gmail_corpus`` is synthetic. Its committed
``metrics.json`` is regenerated with ``uv run python scripts/measure_tokens.py
--write`` after a deliberate cleaner change, and reviewed like code.
"""

from __future__ import annotations

import asyncio
import random
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

import mcp_google_workspace.gmail.tools.drafts as gmail_drafts
import mcp_google_workspace.gmail.tools.messages as gmail_messages
import mcp_google_workspace.gmail.tools.search as gmail_search
import mcp_google_workspace.gmail.tools.threads as gmail_threads
from mcp_google_workspace.gmail.presentation import (
    BODY_LIMIT_CHARS,
    INVISIBLE_CHARACTERS,
    PAGE_BOUNDARY_WINDOW,
    clean_body,
    clean_message_content,
    clean_snippet,
    clean_text,
    envelope,
    html_to_text,
    mail_feed_envelope,
    page_text,
)
from mcp_google_workspace.gmail.server import gmail_mcp

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from fixtures.gmail_corpus import CORPUS  # noqa: E402
from fixtures.gmail_corpus.metrics import METRICS_PATH, compute_metrics, render  # noqa: E402

_INVISIBLE = re.compile(f"[{INVISIBLE_CHARACTERS}]")
_ENTITY = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]{1,30}|#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6});")
_CORPUS = {item.name: item for item in CORPUS}


def _assert_hygienic(text: str) -> None:
    assert not _INVISIBLE.search(text), [f"U+{ord(c):04X}" for c in _INVISIBLE.findall(text)][:5]
    odd = {c for c in text if unicodedata.category(c) in {"Cf", "Cc", "Zs", "Zl", "Zp"} and c not in " \n"}
    assert not odd, sorted(f"U+{ord(c):04X}" for c in odd)
    assert "\t" not in text


# Corpus ------------------------------------------------------------------


def test_corpus_has_the_planned_mix() -> None:
    assert len(CORPUS) == 20
    assert len({item.name for item in CORPUS}) == 20
    assert sum(item.html_heavy for item in CORPUS) >= 8
    tags = [tag for item in CORPUS for tag in item.tags]
    assert tags.count("reply_chain") >= 4
    assert tags.count("plain_text") >= 3
    assert tags.count("attachment") >= 3
    assert {"portuguese", "german", "calendar", "forwarded", "entities"} <= set(tags)


@pytest.mark.parametrize("item", CORPUS, ids=lambda item: item.name)
def test_corpus_clean_output_is_free_of_markup_padding_and_entities(item: Any) -> None:
    result = clean_message_content(item.message)
    body = result["body"]
    assert body.strip()
    _assert_hygienic(body)
    assert "!important" not in body
    assert "{" not in body and "}" not in body
    assert not _ENTITY.search(body), _ENTITY.findall(body)[:5]
    assert "\n\n\n" not in body
    assert not re.search(r"[ ]+\n", body.replace("\n-- \n", ""))
    snippet = envelope(item.message, account_timezone="UTC")["snippet"]
    assert snippet and len(snippet) <= 150
    _assert_hygienic(snippet)
    assert "\n" not in snippet
    assert not _ENTITY.search(snippet)


def test_corpus_metrics_file_is_current() -> None:
    expected = METRICS_PATH.read_text(encoding="utf-8")
    assert render(compute_metrics()) == expected, (
        "tests/fixtures/gmail_corpus/metrics.json is stale; run "
        "`uv run python scripts/measure_tokens.py --write` and review the diff"
    )


def test_corpus_first_pages_never_grow_and_newsletters_shrink() -> None:
    metrics = compute_metrics()
    for item in metrics["messages"]:
        before, after = item["before"], item["after"]
        assert after["first_page_tokens"] <= before["first_page_tokens"], item["name"]
        if item["html_heavy"]:
            assert after["first_page_tokens"] <= 0.6 * before["first_page_tokens"], item["name"]
    totals = metrics["totals"]
    assert totals["after"]["truncated_messages"] <= totals["before"]["truncated_messages"]


def test_newsletter_like_the_87kb_case_is_complete_and_starts_with_its_message() -> None:
    item = _CORPUS["newsletter_fintech_security_87kb"]
    html_chars = len(re.sub(r"\s", "", str(item.message["payload"]["body"]["data"])))
    assert html_chars * 3 // 4 > 80_000
    result = clean_message_content(item.message)
    assert result["truncated"] is False and result["next_offset"] is None
    assert result["body"].startswith("Set a secure code to be shown in all genuine Example Pay emails.")
    assert result["body_chars"] == len(result["body"]) < 3_000
    # The hidden pre-header is gone from the body but Gmail's snippet keeps it.
    assert "Choose a phrase we will show" not in result["body"]
    assert envelope(item.message, account_timezone="UTC")["snippet"] == "You're getting noticed"
    assert "Set my secure code (https://pay.example.com/settings/security)" in result["body"]
    assert "phishing@pay.example.com" in result["body"]
    assert "(mailto:" not in result["body"]


def test_corpus_hidden_and_conditional_content() -> None:
    travel = clean_message_content(_CORPUS["newsletter_travel_mso"].message)["body"]
    assert "Swipe to see more destinations." in travel  # mso-hide:all is visible outside Outlook
    assert "Outlook-only banner text" not in travel
    assert travel.count("See Lisbon deals") == 1  # the VML twin lives in a comment

    software = clean_message_content(_CORPUS["newsletter_software_release"].message)["body"]
    assert "example-cli upgrade --channel stable\nexample-cli doctor" in software
    for absent in ("schema.org", "potentialAction", "does not run scripts", "never rendered"):
        assert absent not in software

    news = clean_message_content(_CORPUS["newsletter_news_digest_links"].message)["body"]
    assert news.count("(https://news.example.net/story/0)") == 1
    assert "Hidden duplicate" not in news

    event = clean_message_content(_CORPUS["newsletter_event_hidden_variants"].message)["body"]
    assert "Save your seat" not in event and "Hover text" not in event
    assert event.startswith("Community meetup, 14 November")

    retail = clean_message_content(_CORPUS["newsletter_retail_sale"].message)["body"]
    assert "Shop Rain jacket (https://shop.example.com/p/rj-02)" in retail  # font-size:0 column wrapper
    assert "HTML content" not in retail

    receipt = clean_message_content(_CORPUS["receipt_order_table"].message)["body"]
    assert "Laptop stand 1 €39.90" in receipt

    nonprofit = clean_message_content(_CORPUS["newsletter_nonprofit_entities"].message)["body"]
    assert "we’d like to reach 3 000, and we can't do it without you…" in nonprofit
    assert "taxdeductible" in nonprofit


def test_corpus_replies_text_parts_and_languages() -> None:
    plain = clean_message_content(_CORPUS["reply_chain_plain_quotes"].message)
    assert plain["body"].startswith("Thursday at 10 works for me.")
    assert plain["quoted_messages_collapsed"] >= 1
    assert "budget draft" not in plain["body"]

    outlook = clean_message_content(_CORPUS["reply_chain_outlook_signature"].message)["body"]
    assert outlook.endswith("Carla") and "Operations" not in outlook

    mobile = clean_message_content(_CORPUS["reply_chain_mobile"].message)["body"]
    assert "Sent from my phone" not in mobile or mobile.index("Sent from my phone") < mobile.index("[quoted")

    html_reply = clean_message_content(_CORPUS["reply_chain_html_gmail_quote"].message)
    assert html_reply["body"].startswith("Sounds good, see you there.")
    assert "Lunch on Wednesday" not in html_reply["body"]

    forwarded = clean_message_content(_CORPUS["forwarded_with_attachment"].message)["body"]
    assert "From 1 December our prices rise by 3%." in forwarded

    portuguese = clean_message_content(_CORPUS["portuguese_notice"].message)["body"]
    assert "A reunião de condomínio foi remarcada para quinta-feira, às 19h30" in portuguese
    german = clean_message_content(_CORPUS["german_text_only"].message)["body"]
    assert "„Sonderanfertigungen“" in german and german.endswith("Jörg Weiß")

    invite = clean_message_content(_CORPUS["calendar_invite"].message)["body"]
    assert invite.startswith("Mia Example has invited you to Design review")
    assert "BEGIN:VCALENDAR" not in invite

    long_report = clean_message_content(_CORPUS["plain_text_long_report"].message)
    assert long_report["truncated"] is True
    assert long_report["body"].endswith("\n\n")  # cut at a paragraph break
    assert BODY_LIMIT_CHARS * (1 - PAGE_BOUNDARY_WINDOW) <= len(long_report["body"]) <= BODY_LIMIT_CHARS


def test_search_and_digest_snippets_agree() -> None:
    for item in CORPUS:
        full = item.message
        # gmail_search_emails asks for no body data, only Gmail's snippet.
        metadata_only = {**full, "payload": {"headers": full["payload"]["headers"], "mimeType": "text/plain"}}
        searched = envelope(metadata_only, account_timezone="UTC")["snippet"]
        digested = mail_feed_envelope(full, account_timezone="UTC")
        assert digested is not None
        assert searched == digested["snippet"], item.name


# Properties ----------------------------------------------------------------

_ASTRAL_INVISIBLE = ["\U000e0041", "\U000e007f", "\U0001d173", "\U000110bd", "\U0001bca0"]
_INVISIBLE_SAMPLE = [
    chr(code)
    for start, end in [(0x00AD, 0x00AD), (0x034F, 0x034F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x2064),
                       (0x2066, 0x206F), (0xFEFF, 0xFEFF), (0x3164, 0x3164), (0x115F, 0x1160), (0x0600, 0x0605)]
    for code in range(start, end + 1)
] + _ASTRAL_INVISIBLE
_ODD_SPACES = [" ", " ", " ", "⠀", "　", " ", "\u000c", "\t", "\r\n", "\u0007"]
_WORDS = [
    "alpha", "beta", "Müller", "reunião", "東京", "naïve", "é", "https://example.com/a?x=1#f", "word.",
    "end!", "why?", "wait…", "。", "!!!!", "____", "-- ", ">", "On Mon someone wrote:", "Sent from my phone",
    "From: a", "Sent: b", "To: c", "\U0001f468‍\U0001f469", "\U0001f44d\U0001f3fd", "x" * 30,
]


def _random_text(rng: random.Random) -> str:
    pieces: list[str] = []
    for _ in range(rng.randint(0, 80)):
        roll = rng.random()
        if roll < 0.45:
            pieces.append(rng.choice(_WORDS))
        elif roll < 0.65:
            pieces.append(rng.choice([" ", " ", "\n", "\n\n", "\n\n\n", "  "]))
        elif roll < 0.8:
            pieces.append("".join(rng.choice(_INVISIBLE_SAMPLE) for _ in range(rng.randint(1, 6))))
        else:
            pieces.append(rng.choice(_ODD_SPACES))
    return "".join(pieces)


_TAGS = ["div", "p", "span", "td", "tr", "table", "a", "b", "li", "h2", "style", "script", "title", "br", "pre"]
_STYLES = [
    "", "color:red", "display:none", "display: none !important", "max-height:0;overflow:hidden", "opacity:0",
    "font-size:0", "font-size:1px", "font-size:16px", "visibility:hidden", "visibility:visible", "mso-hide:all",
]


def _random_html(rng: random.Random, depth: int = 0) -> str:
    parts: list[str] = []
    for _ in range(rng.randint(0, 5)):
        if depth < 4 and rng.random() < 0.5:
            tag = rng.choice(_TAGS)
            attrs = f' style="{rng.choice(_STYLES)}"'
            if tag == "a":
                attrs += f' href="https://example.org/{rng.randint(0, 3)}?utm=1"'
            if rng.random() < 0.1:
                attrs += rng.choice([" hidden", ' aria-hidden="true"'])
            inner = _random_html(rng, depth + 1)
            close = "" if rng.random() < 0.1 else f"</{tag}>"  # unclosed tags happen
            parts.append(f"<{tag}{attrs}>{inner}{close}")
        else:
            parts.append(
                rng.choice(["&nbsp;", "&zwnj;", "&#847;", "&#39;", "&amp;", "&#8199;", "&shy;", "&#65279;", ""])
                + _random_text(rng)[:40]
            )
    return "".join(parts)


def test_invisible_set_covers_every_unicode_format_character() -> None:
    missing = [
        f"U+{code:04X}" for code in range(0x110000)
        if unicodedata.category(chr(code)) == "Cf" and not _INVISIBLE.match(chr(code))
    ]
    assert missing == [], "extend INVISIBLE_CHARACTERS for this Python's Unicode version"


def test_text_cleaner_is_idempotent_and_never_emits_invisible_characters() -> None:
    rng = random.Random(20261103)
    for _ in range(600):
        raw = _random_text(rng)
        once = clean_text(raw)
        assert clean_text(once) == once
        _assert_hygienic(once)
        body, _ = clean_body(raw)
        assert clean_body(body)[0] == body, repr(raw)
        _assert_hygienic(body)
        snippet = clean_snippet(raw, html_escaped=False)
        assert len(snippet) <= 150 and "\n" not in snippet
        _assert_hygienic(snippet)


def test_html_cleaner_output_is_clean_text_and_free_of_hidden_content() -> None:
    rng = random.Random(1103)
    for _ in range(400):
        markup = _random_html(rng)
        text = html_to_text(markup)
        _assert_hygienic(text)
        assert clean_text(text) == text, repr(markup)
        assert "utm=" not in text
    assert html_to_text('<p>Shown</p><div style="display:none"><p>Hidden <b>deep</b></p></div><p>Also</p>') == "Shown\nAlso"
    assert html_to_text('<td style="font-size:0"><p style="font-size:14px">Column</p></td>') == "Column"
    assert html_to_text('<span style="visibility:hidden">no <i style="visibility:visible">yes</i></span>') == "yes"
    assert html_to_text("<head><title>T</title><style>p{}</style><body>Body") == "Body"


def test_link_urls_are_kept_once_and_never_hidden_behind_lookalike_text() -> None:
    assert html_to_text('<a href="https://example.com/a?u=1">https://example.com/a</a>') == "https://example.com/a"
    assert html_to_text('<a href="https://www.example.com/a/">example.com/a</a>') == "example.com/a"
    assert html_to_text('<a href="mailto:x@example.com">x@example.com</a>') == "x@example.com"
    # Text that merely contains the destination still shows where the link goes.
    spoofed = html_to_text(
        '<a href="https://evil.example.net/login">https://bank.example.com/?r=evil.example.net/login</a>'
    )
    assert spoofed == "https://bank.example.com/ (https://evil.example.net/login)"
    assert html_to_text('<a href="https://evil.example.net">evil.example.net.bank.example.com</a>').endswith(
        "(https://evil.example.net)"
    )
    repeated = html_to_text('<a href="https://e.com/x?a=1">One</a> <a href="https://e.com/x?a=2">Two</a>')
    assert repeated == "One (https://e.com/x) Two"
    assert html_to_text('<a href="javascript:alert(1)">Run</a> <a href="#top">Top</a>') == "Run Top"
    assert html_to_text('<a href="https://e.com/logo"><img src="logo.png" alt="Logo"></a>') == ""
    padded = html_to_text('<a href="https://evil.example.net">' + "<span> </span>" * 80 + "Your bank</a>")
    assert padded == "Your bank (https://evil.example.net)"


def test_hostile_markup_is_converted_in_linear_time() -> None:
    import time

    started = time.perf_counter()
    html_to_text("<div>" * 60_000 + "</span>" * 60_000 + "x")
    html_to_text("<div> </div>" * 60_000)
    html_to_text('<a href="https://e.com/x">t' * 30_000)
    html_to_text("<a href='https://e.com/y'>" * 30_000 + "t" * 10 + "</a>" * 30_000)
    html_to_text("<head>" + "<meta>" * 60_000 + "<p>x")
    clean_body("hi\n" + "Sent from my phone\n" * 30_000 + "end")
    # Quadratic handling of any of these takes minutes; linear takes well under a second.
    assert time.perf_counter() - started < 15


def _pages(text: str, limit: int) -> list[tuple[int, str, int | None]]:
    pages, offset = [], 0
    while True:
        body, next_offset = page_text(text, offset, limit)
        pages.append((offset, body, next_offset))
        if next_offset is None:
            return pages
        assert next_offset > offset
        offset = next_offset


def test_pages_reassemble_the_text_and_cut_between_words() -> None:
    rng = random.Random(8000)
    for _ in range(400):
        text, _ = clean_body(_random_text(rng) * rng.randint(1, 4))
        limit = rng.randint(8, 300)
        pages = _pages(text, limit)
        assert "".join(body for _, body, _ in pages) == text
        for offset, body, next_offset in pages:
            assert len(body) <= limit
            if next_offset is None:
                continue
            window = text[offset + max(1, int(limit * (1 - PAGE_BOUNDARY_WINDOW))) : offset + limit]
            if re.search(r"\s", window):
                assert text[next_offset - 1].isspace() or text[next_offset - 1] in "。！？", (
                    repr(body[-20:]),
                    repr(text[next_offset : next_offset + 20]),
                )
            if "\n\n" in window:
                assert body.endswith("\n")
            assert not unicodedata.category(text[next_offset]).startswith("M")


def test_page_prefers_paragraph_then_sentence_then_word() -> None:
    paragraph = "a" * 70 + "\n\n" + "b" * 50
    assert page_text(paragraph, 0, 100) == ("a" * 70 + "\n\n", 72)
    sentence = "word " * 13 + "end. " + "tail " * 20
    body, next_offset = page_text(sentence, 0, 100)
    assert body.endswith("end. ") and sentence[next_offset:].startswith("tail")
    words = "abc " * 40
    assert page_text(words, 0, 10) == ("abc abc ", 8)
    unbroken = "x" * 50 + "é" * 30
    body, next_offset = page_text(unbroken, 0, 51)
    assert len(body) == 50 and next_offset == 50
    assert page_text("short", 0, 100) == ("short", None)
    assert page_text("short", 10, 100) == ("", None)


# Tool shapes -----------------------------------------------------------------


class _Call:
    def __init__(self, path: str, kwargs: dict[str, Any]) -> None:
        self.path = path
        self.kwargs = kwargs


class _FakeService:
    def __init__(self, path: tuple[str, ...] = ()) -> None:
        self._path = path

    def __getattr__(self, name: str) -> Any:
        def method(**kwargs: Any) -> Any:
            if kwargs:
                return _Call(".".join((*self._path, name)), kwargs)
            return _FakeService((*self._path, name))

        return method


async def _utc() -> str:
    return "UTC"


def _call_tool(monkeypatch: pytest.MonkeyPatch, module: Any, responder: Any, name: str, arguments: dict[str, Any]) -> dict:
    async def fake_execute(call: _Call) -> Any:
        return responder(call)

    monkeypatch.setattr(module, "gmail_service", _FakeService)
    monkeypatch.setattr(module, "execute_google_request", fake_execute)
    if hasattr(module, "resolve_user_timezone"):
        monkeypatch.setattr(module, "resolve_user_timezone", _utc)

    async def scenario() -> dict:
        async with Client(gmail_mcp) as client:
            result = await client.call_tool(name, arguments)
            return result.structured_content or result.data

    return asyncio.run(scenario())


def _by_id(call: _Call) -> dict[str, Any]:
    return next(item.message for item in CORPUS if item.message["id"] == call.kwargs["id"])


def test_read_emails_preview_clean_and_full_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    newsletter = _CORPUS["newsletter_fintech_security_87kb"].message
    expected = clean_message_content(newsletter, limit=None)["body"]

    preview = _call_tool(monkeypatch, gmail_messages, _by_id, "read_emails", {"message_ids": ["c01"], "format": "preview"})
    item = preview["messages"][0]
    assert item["body"] == page_text(expected, 0, 1_000)[0]
    assert item["truncated"] is True and item["next_offset"] == len(item["body"])
    assert item["body"].startswith("Set a secure code") and item["snippet"] == "You're getting noticed"

    clean = _call_tool(monkeypatch, gmail_messages, _by_id, "read_emails", {"message_ids": ["c01"]})["messages"][0]
    assert clean["body"] == expected and clean["truncated"] is False
    assert clean["body_chars"] == len(expected) and clean["body_sha256"] == item["body_sha256"]

    full = _call_tool(monkeypatch, gmail_messages, _by_id, "read_emails", {"message_ids": ["c01", "c13"], "format": "full"})
    newsletter_full, plain_full = full["messages"]
    assert newsletter_full["body"] == expected and newsletter_full["html_body"] is None
    assert newsletter_full["truncated"] is False and newsletter_full["next_offset"] is None
    assert plain_full["text_body"].startswith("Hi,\n\nCan we move")  # the original text part, unmodified
    assert plain_full["html_body"] is None

    with_html = _call_tool(
        monkeypatch, gmail_messages, _by_id, "read_emails",
        {"message_ids": ["c01"], "format": "full", "include_html": True},
    )["messages"][0]
    assert with_html["html_body"].startswith("<!DOCTYPE html>") and len(with_html["html_body"]) > 80_000
    assert with_html["body"] == expected


def test_read_emails_clean_pages_reassemble_a_long_body(monkeypatch: pytest.MonkeyPatch) -> None:
    pages: list[str] = []
    offset: int | None = 0
    hashes = set()
    while offset is not None:
        result = _call_tool(
            monkeypatch, gmail_messages, _by_id, "read_emails", {"message_ids": ["c14"], "offset": offset}
        )["messages"][0]
        pages.append(result["body"])
        hashes.add((result["body_chars"], result["body_sha256"]))
        offset = result["next_offset"]
    assert len(pages) >= 3 and len(hashes) == 1
    assert "".join(pages) == clean_message_content(_CORPUS["plain_text_long_report"].message, limit=None)["body"]
    assert all(page.endswith("\n\n") for page in pages[:-1])


def test_get_draft_full_returns_the_cleaned_body(monkeypatch: pytest.MonkeyPatch) -> None:
    message = {**_CORPUS["html_only_entities"].message, "labelIds": ["DRAFT"]}

    def responder(call: _Call) -> dict[str, Any]:
        assert call.path == "users.drafts.get"
        return {"id": "r-1", "message": message}

    result = _call_tool(monkeypatch, gmail_drafts, responder, "get_draft", {"draft_id": "r-1"})
    assert result["body"] == "Hi Jo,\nHere's the \"final\" agenda for Friday – R&D first, then sales & marketing.\nCheers,\nKai"
    assert result["snippet"] == "Hi Jo, Here's the \"final\" agenda for Friday – R&D first, then sales & marketing."
    assert result["body_chars"] == len(result["body"]) and len(result["body_sha256"]) == 64
    assert result["draft"]["id"] == "r-1"


def test_get_thread_clean_uses_the_same_cleaner(monkeypatch: pytest.MonkeyPatch) -> None:
    older = _CORPUS["newsletter_nonprofit_entities"].message
    latest = _CORPUS["newsletter_fintech_security_87kb"].message

    def responder(call: _Call) -> dict[str, Any]:
        return {"id": "t-1", "historyId": "9", "messages": [older, latest]}

    result = _call_tool(monkeypatch, gmail_threads, responder, "get_thread", {"thread_id": "t-1"})
    assert result["latest"]["body"] == clean_message_content(latest)["body"]
    assert result["latest"]["body_sha256"] == clean_message_content(latest)["body_sha256"]
    assert result["prior_messages"][0]["snippet"].startswith("Help us reach 3,000 coats this winter. We'd like")


def test_search_snippets_are_decoded_without_body_data(monkeypatch: pytest.MonkeyPatch) -> None:
    def responder(call: _Call) -> dict[str, Any]:
        if call.path == "users.messages.list":
            return {"messages": [{"id": "c01"}, {"id": "c15"}], "resultSizeEstimate": 2}
        message = _by_id(call)
        return {**message, "payload": {"headers": message["payload"]["headers"], "mimeType": "text/html"}}

    result = _call_tool(monkeypatch, gmail_search, responder, "search_emails", {"query": "x"})
    assert [item["snippet"] for item in result["messages"]] == [
        "You're getting noticed",
        "Hi Jo, Here's the \"final\" agenda for Friday – R&D first, then sales & marketing.",
    ]
