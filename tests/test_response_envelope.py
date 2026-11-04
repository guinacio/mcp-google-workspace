"""The shared response envelope: resource handles, list fields, search_workspace, drive_read_file."""

from __future__ import annotations

import threading
import time
from typing import Any

import anyio
import pytest
from fastmcp import Client
from googleapiclient.errors import HttpError
from httplib2 import Response

import mcp_google_workspace.drive.tools.files as drive_files
import mcp_google_workspace.server as server_module
from mcp_google_workspace.common.component_annotations import _paginated_result
from mcp_google_workspace.common.resources import add_resource_handles, clean_web_url, resource_handle
from mcp_google_workspace.drive.server import drive_mcp
from mcp_google_workspace.gmail.helpers import DEFAULT_FETCH_CONCURRENCY
from mcp_google_workspace.server import workspace_mcp

TIMEZONE = "America/Sao_Paulo"


async def _timezone() -> str:
    return TIMEZONE


# ---------------------------------------------------------------------------
# Resource handles
# ---------------------------------------------------------------------------


def test_resource_handle_omits_unknown_fields() -> None:
    assert resource_handle("gmail_message", "m1") == {
        "kind": "gmail_message",
        "id": "m1",
        "uri": "gmail-message:///m1",
    }


def test_attached_handle_adds_only_what_the_item_lacks() -> None:
    payload = {
        "files": [
            {"id": "f1", "name": "Plan", "mime_type": "text/plain", "modified_at": "x", "web_url": "y"},
        ],
        "contacts": [{"resource_name": "people/c1", "name": "Ana", "etag": "%E1"}],
        "event": {"id": "e1", "title": "Review", "url": "https://www.google.com/calendar/event?eid=abc"},
    }
    add_resource_handles(payload, "drive")
    assert payload["files"][0]["resource"] == {"kind": "drive_file", "id": "f1", "uri": "gdrive:///f1"}
    assert payload["contacts"][0]["resource"] == {"kind": "contact", "id": "people/c1", "uri": "gcontact:///people%2Fc1"}
    assert payload["event"]["resource"] == {"kind": "calendar_event", "id": "e1", "uri": "gcal-event:///e1"}


def test_handle_on_a_raw_item_keeps_the_facts_the_item_does_not_name() -> None:
    payload = {"id": "f1", "webViewLink": "https://drive.google.com/file/d/f1/view?usp=drivesdk"}
    add_resource_handles(payload, "drive")
    # The raw item carries webViewLink itself, so the handle does not repeat it.
    assert payload["resource"] == {"kind": "drive_file", "id": "f1", "uri": "gdrive:///f1"}
    bare = {"id": "f2"}
    add_resource_handles(bare, "drive")
    assert bare["resource"] == {"kind": "drive_file", "id": "f2", "uri": "gdrive:///f2"}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://docs.google.com/document/d/abc/edit?usp=drivesdk",
            "https://docs.google.com/document/d/abc/edit",
        ),
        (
            "https://drive.google.com/file/d/abc/view?usp=drivesdk&ouid=1046&rtpof=true&sd=true",
            "https://drive.google.com/file/d/abc/view",
        ),
        (
            "https://www.google.com/calendar/event?eid=NG0yazlx&utm_source=x",
            "https://www.google.com/calendar/event?eid=NG0yazlx",
        ),
        ("https://drive.google.com/drive/folders/abc", "https://drive.google.com/drive/folders/abc"),
        (None, None),
    ],
)
def test_clean_web_url_strips_only_tracking_parameters(url: str | None, expected: str | None) -> None:
    assert clean_web_url(url) == expected


# ---------------------------------------------------------------------------
# One list envelope
# ---------------------------------------------------------------------------

_ENVELOPE_FIELDS = {"count", "has_more", "next_page_token", "fetched_at"}
# Per-tool spellings the shared envelope replaced; none may come back.
_RETIRED_KEYS = {"message_count", "new_count", "total_suggestions", "nextPageToken", "items", "status"}
# List tools whose items carry dates in the account time zone.
_DATED_LISTS = {
    "calendar_get_calendar_digest",
    "calendar_search_events",
    "drive_list_files",
    "forms_list_form_responses",
    "gmail_check_mail_updates",
    "gmail_get_mail_digest",
    "gmail_get_thread",
    "gmail_search_emails",
    "search_workspace",
    "tasks_list_tasklists",
    "tasks_list_tasks",
    "tasks_tasks_digest",
}
_LIST_SHAPED = {
    "gmail_check_mail_updates",
    "gmail_get_mail_digest",
    "gmail_get_thread",
    "calendar_get_calendar_digest",
    "tasks_tasks_digest",
}


async def _published_tools() -> dict[str, Any]:
    async with Client(workspace_mcp) as client:
        return {tool.name: tool for tool in await client.list_tools()}


def _is_list_tool(name: str) -> bool:
    namespace, _, base = name.partition("_")
    base = base if namespace in {"calendar", "docs", "drive", "forms", "gmail", "people", "sheets", "slides", "tasks"} else name
    return base.startswith(("list_", "search_")) or name in _LIST_SHAPED


def test_every_list_tool_publishes_the_same_envelope_keys() -> None:
    tools = anyio.run(_published_tools)
    list_tools = sorted(name for name in tools if _is_list_tool(name) and not name.startswith("uploads_"))
    assert {
        "people_search_contacts", "tasks_tasks_digest", "gmail_get_thread", "gmail_list_drafts",
        "drive_list_permissions", "drive_list_drives", "forms_list_form_responses",
    } <= set(list_tools)
    problems: dict[str, Any] = {}
    for name in list_tools:
        schema = tools[name].output_schema
        assert schema is not None, f"{name} publishes no output schema"
        assert schema.get("additionalProperties") is True, name
        properties = set(schema.get("properties", {}))
        missing = _ENVELOPE_FIELDS - properties
        retired = properties & _RETIRED_KEYS
        lists = [key for key, value in schema["properties"].items() if value.get("type") == "array"]
        if missing or retired or (name in _DATED_LISTS and "account_timezone" not in properties):
            problems[name] = {"missing": sorted(missing), "retired": sorted(retired), "lists": lists}
    assert problems == {}


def test_wrapper_completes_the_envelope_without_overriding_the_tool() -> None:
    result = _paginated_result({"tasks": [1, 2], "next_page_token": "t"})
    assert result["count"] == 2 and result["has_more"] is True and result["next_page_token"] == "t"
    assert "fetched_at" in result
    explicit = _paginated_result({"overdue": [1], "upcoming": [2, 3], "count": 3})
    assert explicit["count"] == 3 and explicit["has_more"] is False and explicit["next_page_token"] is None


# ---------------------------------------------------------------------------
# search_workspace
# ---------------------------------------------------------------------------


class _Request:
    def __init__(self, run: Any) -> None:
        self._run = run

    def execute(self, *_a: Any, **_k: Any) -> Any:
        return self._run()


class _GmailFake:
    """messages.list returns ``count`` IDs; each messages.get sleeps to expose concurrency."""

    def __init__(self, count: int, *, missing: frozenset[str] = frozenset()) -> None:
        self.count = count
        self.missing = missing
        self.get_calls: list[dict[str, Any]] = []
        self.in_flight = 0
        self.peak = 0
        self._lock = threading.Lock()

    def users(self) -> "_GmailFake":
        return self

    def messages(self) -> "_GmailFake":
        return self

    def list(self, **_kwargs: Any) -> _Request:
        return _Request(lambda: {"messages": [{"id": f"m{i}", "threadId": f"t{i}"} for i in range(self.count)]})

    def get(self, **kwargs: Any) -> _Request:
        self.get_calls.append(kwargs)

        def run() -> dict[str, Any]:
            with self._lock:
                self.in_flight += 1
                self.peak = max(self.peak, self.in_flight)
            try:
                time.sleep(0.02)
                if kwargs["id"] in self.missing:
                    raise HttpError(Response({"status": "404"}), b'{"error":{"message":"Not found"}}')
                index = kwargs["id"][1:]
                return {
                    "id": kwargs["id"],
                    "threadId": f"t{index}",
                    "labelIds": ["INBOX", "UNREAD"],
                    "snippet": f"Message {index} body &amp; more",
                    "internalDate": "1793710812000",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": f"Sender {index} <s{index}@example.com>"},
                            {"name": "Subject", "value": f"Subject {index}"},
                            {"name": "Date", "value": "Tue, 3 Nov 2026 13:00:12 +0000"},
                        ]
                    },
                }
            finally:
                with self._lock:
                    self.in_flight -= 1

        return _Request(run)


async def _search(arguments: dict[str, Any]) -> Any:
    async with Client(workspace_mcp) as client:
        return await client.call_tool("search_workspace", arguments, raise_on_error=False)


def test_search_workspace_gmail_hits_carry_subject_sender_date_and_snippet(monkeypatch: pytest.MonkeyPatch) -> None:
    gmail = _GmailFake(25, missing=frozenset({"m3"}))
    monkeypatch.setattr(server_module, "build_gmail_service", lambda: gmail)
    monkeypatch.setattr(server_module, "resolve_user_timezone", _timezone)

    result = anyio.run(_search, {"query": "budget", "services": ["gmail"], "max_results_per_service": 25})

    assert result.is_error is False, result.content
    payload = result.structured_content
    hits = payload["matches"]
    assert [hit["id"] for hit in hits] == [f"m{i}" for i in range(25) if i != 3]  # order kept, deleted one skipped
    first = hits[0]
    assert first == {
        "service": "gmail",
        "id": "m0",
        "kind": "message",
        "thread_id": "t0",
        "subject": "Subject 0",
        "from": {"name": "Sender 0", "email": "s0@example.com"},
        "date": "2026-11-03T10:00:12-03:00",
        "snippet": "Message 0 body & more",
        "unread": True,
        "resource": {"kind": "gmail_message", "id": "m0", "uri": "gmail-message:///m0"},
    }
    assert payload["account_timezone"] == TIMEZONE
    assert payload["count"] == 24 and payload["has_more"] is False and "status" not in payload
    # One metadata request per hit, headers only, no body.
    assert len(gmail.get_calls) == 25
    assert all(call["format"] == "metadata" for call in gmail.get_calls)
    assert all(call["metadataHeaders"] == ["From", "Subject", "Date"] for call in gmail.get_calls)
    # Concurrent, but never more than the shared fetch limit at once.
    assert 1 < gmail.peak <= DEFAULT_FETCH_CONCURRENCY


class _DriveFake:
    def __init__(self, files: list[dict[str, Any]]) -> None:
        self.files_payload = files
        self.queries: list[str] = []

    def files(self) -> "_DriveFake":
        return self

    def list(self, **kwargs: Any) -> _Request:
        self.queries.append(kwargs["q"])
        return _Request(lambda: {"files": self.files_payload})


def test_search_workspace_drive_searches_full_text_with_name_matches_first(monkeypatch: pytest.MonkeyPatch) -> None:
    drive = _DriveFake(
        [
            {"id": "f1", "name": "Minutes", "mimeType": "application/vnd.google-apps.document",
             "modifiedTime": "2026-11-02T18:45:03.120Z", "webViewLink": "https://docs.google.com/document/d/f1/edit?usp=drivesdk"},
            {"id": "f2", "name": "Budget 2027", "mimeType": "application/vnd.google-apps.spreadsheet",
             "modifiedTime": "2026-11-01T10:00:00Z", "webViewLink": "https://docs.google.com/spreadsheets/d/f2/edit"},
        ]
    )
    monkeypatch.setattr(server_module, "build_drive_service", lambda: drive)
    monkeypatch.setattr(server_module, "resolve_user_timezone", _timezone)

    result = anyio.run(_search, {"query": "budget's", "services": ["drive"]})

    assert result.is_error is False, result.content
    assert drive.queries == [
        "(fullText contains 'budget\\'s' or name contains 'budget\\'s') and trashed = false"
    ]
    hits = result.structured_content["matches"]
    # Both match the content; the one whose name contains the query ranks first.
    assert [hit["id"] for hit in hits] == ["f1", "f2"] or [hit["id"] for hit in hits] == ["f2", "f1"]
    drive.files_payload = [dict(item, name=item["name"].replace("'", "")) for item in drive.files_payload]
    result = anyio.run(_search, {"query": "budget", "services": ["drive"]})
    hits = result.structured_content["matches"]
    assert [hit["id"] for hit in hits] == ["f2", "f1"]
    assert hits[0] == {
        "service": "drive",
        "id": "f2",
        "kind": "spreadsheet",
        "name": "Budget 2027",
        "mime_type": "application/vnd.google-apps.spreadsheet",
        "modified_at": "2026-11-01T07:00:00-03:00",
        "web_url": "https://docs.google.com/spreadsheets/d/f2/edit",
        "resource": {"kind": "drive_file", "id": "f2", "uri": "gdrive:///f2"},
    }
    assert hits[1]["web_url"] == "https://docs.google.com/document/d/f1/edit"


# ---------------------------------------------------------------------------
# drive_read_file
# ---------------------------------------------------------------------------


class _DriveFiles:
    def __init__(self, files: dict[str, dict[str, Any]], contents: dict[str, bytes]) -> None:
        self.meta = files
        self.contents = contents
        self.exports: list[tuple[str, str]] = []
        self.downloads: list[str] = []

    def files(self) -> "_DriveFiles":
        return self

    def get(self, **kwargs: Any) -> _Request:
        return _Request(lambda: dict(self.meta[kwargs["fileId"]]))

    def export(self, **kwargs: Any) -> _Request:
        self.exports.append((kwargs["fileId"], kwargs["mimeType"]))
        return _Request(lambda: self.contents[kwargs["fileId"]])

    def get_media(self, **kwargs: Any) -> _Request:
        self.downloads.append(kwargs["fileId"])
        return _Request(lambda: self.contents[kwargs["fileId"]])


_DOC = "application/vnd.google-apps.document"
_LONG_TEXT = "\n\n".join(
    f"Section {index}. " + " ".join(f"Sentence {index}.{part} of the report." for part in range(12))
    for index in range(60)
)


def _drive_fake(monkeypatch: pytest.MonkeyPatch) -> _DriveFiles:
    fake = _DriveFiles(
        {
            "doc": {"id": "doc", "name": "Report", "mimeType": _DOC, "modifiedTime": "2026-11-02T18:45:03Z",
                    "webViewLink": "https://docs.google.com/document/d/doc/edit?usp=drivesdk"},
            "sheet": {"id": "sheet", "name": "Budget", "mimeType": "application/vnd.google-apps.spreadsheet"},
            "notes": {"id": "notes", "name": "notes.md", "mimeType": "text/markdown", "size": "40"},
            "page": {"id": "page", "name": "page.html", "mimeType": "text/html", "size": "90"},
            "pdf": {"id": "pdf", "name": "Invoice.pdf", "mimeType": "application/pdf", "size": "184213"},
            "big": {"id": "big", "name": "dump.txt", "mimeType": "text/plain", "size": str(6 * 1024 * 1024)},
            "fake": {"id": "fake", "name": "data.txt", "mimeType": "text/plain", "size": "8"},
            "form": {"id": "form", "name": "Survey", "mimeType": "application/vnd.google-apps.form"},
            "link": {"id": "link", "name": "Report shortcut", "mimeType": "application/vnd.google-apps.shortcut",
                     "shortcutDetails": {"targetId": "doc", "targetMimeType": _DOC}},
        },
        {
            "doc": ("﻿" + _LONG_TEXT).encode("utf-8"),
            "sheet": b"Item,Cost\r\nRent,1000\r\n",
            "notes": "# Notes​\r\n\r\n- one\r\n".encode("utf-8"),
            "page": b"<html><head><style>p{color:red}</style></head><body><p>Hello <b>there</b></p></body></html>",
            "fake": b"\x00\x01\x02binary",
        },
    )
    monkeypatch.setattr(drive_files, "drive_service", lambda: fake)
    monkeypatch.setattr(drive_files, "resolve_user_timezone", _timezone)
    return fake


async def _read(arguments: dict[str, Any]) -> Any:
    async with Client(drive_mcp) as client:
        return await client.call_tool("read_file", arguments, raise_on_error=False)


def test_drive_read_file_pages_an_exported_doc_with_the_email_body_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _drive_fake(monkeypatch)
    pages: list[str] = []
    offset: int | None = 0
    digests = set()
    while offset is not None:
        result = anyio.run(_read, {"file_id": "doc", "offset": offset})
        assert result.is_error is False, result.content
        page = result.structured_content
        pages.append(page["body"])
        digests.add((page["body_chars"], page["body_sha256"]))
        assert len(page["body"]) <= 8_000
        assert page["truncated"] is (page["next_offset"] is not None)
        offset = page["next_offset"]
    assert len(pages) > 1
    assert "".join(pages) == _LONG_TEXT  # BOM removed, nothing lost between pages
    assert len(digests) == 1  # the whole text's length and hash, the same on every page
    assert all(page.endswith("\n\n") for page in pages[:-1])  # pages end at a paragraph
    assert page["file_id"] == "doc" and page["exported_as"] == "text/plain" and page["format"] == "text"
    assert page["modified_at"] == "2026-11-02T15:45:03-03:00"
    assert page["web_url"] == "https://docs.google.com/document/d/doc/edit"
    assert set(fake.exports) == {("doc", "text/plain")}


def test_drive_read_file_exports_markdown_csv_and_reads_text_files(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _drive_fake(monkeypatch)
    markdown = anyio.run(_read, {"file_id": "doc", "format": "markdown"}).structured_content
    assert markdown["exported_as"] == "text/markdown" and ("doc", "text/markdown") in fake.exports
    sheet = anyio.run(_read, {"file_id": "sheet"}).structured_content
    assert sheet["exported_as"] == "text/csv" and sheet["body"] == "Item,Cost\nRent,1000"
    notes = anyio.run(_read, {"file_id": "notes"}).structured_content
    assert notes["exported_as"] is None and notes["body"] == "# Notes\n\n- one"
    page = anyio.run(_read, {"file_id": "page"}).structured_content
    assert page["body"] == "Hello there"
    shortcut = anyio.run(_read, {"file_id": "link"}).structured_content
    assert shortcut["file_id"] == "doc" and shortcut["name"] == "Report"
    assert fake.downloads == ["notes", "page"]


@pytest.mark.parametrize(
    ("file_id", "reason"),
    [
        ("pdf", "binary file (application/pdf)"),
        ("big", "above the 5,242,880-byte read limit"),
        ("fake", "its content is binary"),
        ("form", "forms_get_form"),
    ],
)
def test_drive_read_file_refuses_what_it_cannot_read(monkeypatch: pytest.MonkeyPatch, file_id: str, reason: str) -> None:
    fake = _drive_fake(monkeypatch)
    result = anyio.run(_read, {"file_id": file_id})
    assert result.is_error is True
    error = result.structured_content
    assert error["code"] == "invalid_input"
    assert reason in error["message"]
    assert error["required_action"] == {
        "tool": "drive_get_file",
        "arguments": {"file_id": file_id},
    }
    if file_id in {"pdf", "big", "form"}:
        assert fake.downloads == [] and fake.exports == []  # refused before any content is fetched


def test_drive_read_file_is_published_read_only() -> None:
    tools = anyio.run(_published_tools)
    tool = tools["drive_read_file"]
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    assert set(tool.input_schema["properties"]["format"]["enum"]) == {"text", "markdown"}
