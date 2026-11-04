import asyncio
import base64
import threading
from collections.abc import Callable
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace
from typing import Any

import anyio
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from googleapiclient.errors import HttpError
from httplib2 import Response

from mcp_google_workspace.common.async_ops import execute_google_request
from mcp_google_workspace.common.errors import classify_error
from mcp_google_workspace.gmail.helpers import gather_in_order
from mcp_google_workspace.gmail.mime_utils import (
    build_email_message,
    encode_subject,
    extract_message_bodies,
)
from mcp_google_workspace.gmail.presentation import (
    clean_message_content,
    envelope,
)
from mcp_google_workspace.gmail.schemas import SearchEmailRequest, SendEmailRequest
from mcp_google_workspace.gmail.server import gmail_mcp
import mcp_google_workspace.gmail.tools.history as gmail_history
import mcp_google_workspace.gmail.tools.messages as gmail_messages
import mcp_google_workspace.gmail.tools.search as gmail_search

from consent_testing import answer_consent, gated


class _ToolCapture:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., Any]] = {}

    def tool(self, *, name: str):
        def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
            self.tools[name] = function
            return function

        return decorator


def _reply_api(source: dict[str, Any]):
    sent_bodies: list[dict[str, Any]] = []

    class Messages:
        def get(self, **kwargs: Any) -> dict[str, Any]:
            return {"operation": "get", "kwargs": kwargs}

        def send(self, **kwargs: Any) -> dict[str, Any]:
            return {"operation": "send", "kwargs": kwargs}

    class SendAs:
        def list(self, **kwargs: Any) -> dict[str, Any]:
            return {"operation": "send_as", "kwargs": kwargs}

    class Settings:
        def sendAs(self) -> SendAs:
            return SendAs()

    class Users:
        def messages(self) -> Messages:
            return Messages()

        def settings(self) -> Settings:
            return Settings()

        def getProfile(self, **kwargs: Any) -> dict[str, Any]:
            return {"operation": "profile", "kwargs": kwargs}

    class Service:
        def users(self) -> Users:
            return Users()

    async def execute(request: dict[str, Any]) -> dict[str, Any]:
        operation = request["operation"]
        if operation == "get":
            assert request["kwargs"]["format"] == "metadata"
            return source
        if operation == "profile":
            return {"emailAddress": "me@example.com"}
        if operation == "send_as":
            return {"sendAs": [{"sendAsEmail": "alias@example.com"}]}
        if operation == "send":
            sent_bodies.append(request["kwargs"]["body"])
            return {
                "id": "sent-1",
                "threadId": source["threadId"],
                "labelIds": ["SENT"],
            }
        raise AssertionError(f"Unexpected operation: {operation}")

    return Service(), execute, sent_bodies


def _decode_raw_message(raw: str):
    padding = "=" * (-len(raw) % 4)
    return BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode((raw + padding).encode())
    )


def test_subject_supports_international_chars():
    subject = "Olá — Привет — こんにちは"
    encoded = encode_subject(subject)
    assert isinstance(encoded, str)
    assert encoded


def test_build_email_message_multipart():
    message = build_email_message(
        subject="Test",
        to=["a@example.com"],
        cc=[],
        bcc=[],
        text_body="plain",
        html_body="<p>html</p>",
        attachments=[],
    )
    assert message["Subject"]
    assert message.is_multipart()


def test_extract_message_bodies():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": "cGxhaW4gdGV4dA=="}},
            {"mimeType": "text/html", "body": {"data": "PHA+aHRtbDwvcD4="}},
        ],
    }
    bodies = extract_message_bodies(payload)
    assert "plain text" in bodies["text"]
    assert "<p>html</p>" in bodies["html"]


@pytest.mark.parametrize("accepted", [False, True])
def test_send_email_always_asks_before_sending(monkeypatch, accepted) -> None:
    """There is no flag to skip the question: every send is previewed and asked first."""
    assert "confirm_send" not in SendEmailRequest.model_fields
    service, execute, sent_bodies = _reply_api({"id": "unused", "threadId": "thread-new"})
    capture = _ToolCapture()
    gmail_messages.register(capture)  # type: ignore[arg-type]
    monkeypatch.setattr(gmail_messages, "gmail_service", lambda: service)
    monkeypatch.setattr(gmail_messages, "execute_google_request", execute)
    answers = answer_consent(monkeypatch, accepted)
    send = gated(capture.tools["send_email"], "gmail_send_email")

    result = anyio.run(
        lambda: send(subject="Test", to=["a@example.com"], text_body="Hello", ctx=None)
    )

    assert answers.asked == [
        (
            "send_email",
            "Send this email?\nTo: a@example.com\nCc: (none)\nBcc: (none)\nRecipients: 1 in total\n"
            "Subject: Test\nText: Hello\nAttachments: 0",
        )
    ]
    assert len(sent_bodies) == (1 if accepted else 0)
    assert result["status"] == ("sent" if accepted else "cancelled")


def test_reply_uses_gmail_thread_and_rfc_reply_headers(monkeypatch) -> None:
    source = {
        "id": "source-1",
        "threadId": "thread-1",
        "payload": {
            "headers": [
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "Reply-To", "value": "Team <team@example.com>"},
                {"name": "To", "value": "Me <me@example.com>"},
                {"name": "Subject", "value": "Project update"},
                {"name": "Message-ID", "value": "<source-1@example.com>"},
                {"name": "References", "value": "<root@example.com>"},
            ]
        },
    }
    service, execute, sent_bodies = _reply_api(source)
    capture = _ToolCapture()
    gmail_messages.register(capture)  # type: ignore[arg-type]
    monkeypatch.setattr(gmail_messages, "gmail_service", lambda: service)
    monkeypatch.setattr(gmail_messages, "execute_google_request", execute)

    async def call_tool() -> dict[str, Any]:
        return await capture.tools["reply"](
            "source-1", text_body="Thanks for the update."
        )

    result = anyio.run(call_tool)

    assert result["mode"] == "reply"
    assert result["to"] == ["team@example.com"]
    assert result["cc"] == []
    assert sent_bodies[0]["threadId"] == "thread-1"
    message = _decode_raw_message(sent_bodies[0]["raw"])
    assert str(message["Subject"]) == "Project update"
    assert str(message["To"]) == "team@example.com"
    assert message["Cc"] is None
    assert str(message["In-Reply-To"]) == "<source-1@example.com>"
    assert str(message["References"]) == "<root@example.com> <source-1@example.com>"


def test_reply_all_includes_participants_once_and_excludes_self(
    monkeypatch,
) -> None:
    source = {
        "id": "source-2",
        "threadId": "thread-2",
        "payload": {
            "headers": [
                {"name": "From", "value": "Alice <alice@example.com>"},
                {
                    "name": "To",
                    "value": "Me <me@example.com>, Bob <bob@example.com>, Alias <alias@example.com>",
                },
                {
                    "name": "Cc",
                    "value": "Carol <carol@example.com>, Alice <alice@example.com>",
                },
                {"name": "Subject", "value": "Planning"},
                {"name": "Message-ID", "value": "<source-2@example.com>"},
            ]
        },
    }
    service, execute, sent_bodies = _reply_api(source)
    capture = _ToolCapture()
    gmail_messages.register(capture)  # type: ignore[arg-type]
    monkeypatch.setattr(gmail_messages, "gmail_service", lambda: service)
    monkeypatch.setattr(gmail_messages, "execute_google_request", execute)

    async def call_tool() -> dict[str, Any]:
        return await capture.tools["reply"](
            "source-2", reply_all=True, html_body="<p>Works for me.</p>"
        )

    result = anyio.run(call_tool)

    assert result["mode"] == "reply_all"
    assert result["to"] == ["alice@example.com"]
    assert result["cc"] == ["bob@example.com", "carol@example.com"]
    assert sent_bodies[0]["threadId"] == "thread-2"
    message = _decode_raw_message(sent_bodies[0]["raw"])
    assert str(message["To"]) == "alice@example.com"
    assert str(message["Cc"]) == "bob@example.com, carol@example.com"
    assert str(message["In-Reply-To"]) == "<source-2@example.com>"
    assert str(message["References"]) == "<source-2@example.com>"


@pytest.mark.parametrize("reply_all", [False, True])
def test_declined_reply_sends_nothing(monkeypatch, reply_all) -> None:
    source = {
        "id": "source-cancelled",
        "threadId": "thread-cancelled",
        "payload": {
            "headers": [
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "To", "value": "Me <me@example.com>"},
                {"name": "Subject", "value": "Project update"},
                {"name": "Message-ID", "value": "<cancelled@example.com>"},
            ]
        },
    }
    service, execute, sent_bodies = _reply_api(source)
    capture = _ToolCapture()
    gmail_messages.register(capture)  # type: ignore[arg-type]
    monkeypatch.setattr(gmail_messages, "gmail_service", lambda: service)
    monkeypatch.setattr(gmail_messages, "execute_google_request", execute)

    answers = answer_consent(monkeypatch, False)
    reply = gated(capture.tools["reply"], "gmail_reply")

    async def call_tool() -> dict[str, Any]:
        return await reply(
            "source-cancelled",
            reply_all=reply_all,
            text_body="Thanks for the update.",
            ctx=None,
        )

    result = anyio.run(call_tool)

    assert result == {
        "status": "cancelled",
        "action": "reply_all_email" if reply_all else "reply_email",
        "reason": "The user declined; nothing was changed.",
    }
    # The preview names the real recipients, read from the source message.
    assert answers.asked == [
        (
            "reply_all_email" if reply_all else "reply_email",
            f"{'Reply all' if reply_all else 'Reply'} to this email?\nTo: alice@example.com\nCc: (none)\n"
            "Bcc: (none)\nRecipients: 1 in total\nSubject: Project update\nText: Thanks for the update.\n"
            "Attachments: 0",
        )
    ]
    assert sent_bodies == []


def test_envelope_classifies_and_cleans_a_message():
    message = {
        "id": "m1",
        "threadId": "t1",
        "labelIds": ["INBOX", "UNREAD", "CATEGORY_PROMOTIONS"],
        "payload": {
            "headers": [
                {"name": "From", "value": "News <no-reply@example.com>"},
                {"name": "Subject", "value": "Weekly update"},
                {"name": "Date", "value": "Wed, 9 Jul 2026 10:00:00 +0000"},
                {"name": "List-Unsubscribe", "value": "<https://example.com/unsubscribe>"},
            ],
            "parts": [{"mimeType": "text/plain", "body": {"data": "SGVsbG8gd29ybGQ="}}],
        },
    }

    result = envelope(message, account_timezone="America/Sao_Paulo")

    assert result["from"] == {"name": "News", "email": "no-reply@example.com"}
    assert result["category"] == "CATEGORY_PROMOTIONS"
    assert result["unread"] is True
    assert result["is_newsletter"] is True
    assert result["is_automated"] is True
    assert result["date"] == "2026-07-09T07:00:00-03:00"
    # The account zone is stated once per response, and the raw Date header
    # only under gmail_read_emails format=full (details.date_header).
    assert "date_timezone" not in result and "source_date" not in result


def test_clean_message_content_collapses_quote_and_truncates():
    text = "Current reply\n\nOn Tuesday someone wrote:\n> Earlier message"
    message = {"payload": {"parts": [{"mimeType": "text/plain", "body": {"data": "Q3VycmVudCByZXBseQoKT24gVHVlc2RheSBzb21lb25lIHdyb3RlOgo+IEVhcmxpZXIgbWVzc2FnZQ=="}}]}}

    result = clean_message_content(message, limit=10)

    # A 10-character page backs off to the word break instead of "Current re".
    cleaned = "Current reply\n\n[quoted: 2 earlier messages in thread]"
    assert text.startswith("Current reply")
    assert result["body"] == "Current "
    assert result["truncated"] is True
    assert result["next_offset"] == 8
    assert result["quoted_messages_collapsed"] == 2

    second = clean_message_content(message, offset=result["next_offset"], limit=10)
    assert second["body"] == "reply\n\n"
    assert second["next_offset"] == 15
    assert second["body_sha256"] == result["body_sha256"]
    assert second["body_chars"] == result["body_chars"] == len(cleaned)

    pages, offset = [], 0
    while offset is not None:
        page = clean_message_content(message, offset=offset, limit=10)
        pages.append(page["body"])
        offset = page["next_offset"]
    assert "".join(pages) == cleaned
    assert all(len(page) <= 10 for page in pages)


def test_invalid_grant_from_unknown_request_preserves_token_and_has_actionable_error():
    class FailedRequest:
        def execute(self):
            raise RuntimeError("invalid_grant: Bad Request")

    with pytest.raises(RuntimeError, match="reauth_required") as error:
        asyncio.run(execute_google_request(FailedRequest()))

    assert "OAuth consent" in str(error.value)


def test_html_placeholder_falls_back_to_html_and_snippet_hygiene():
    html = "<p>Hello ____ https://example.com?track=1</p>"
    message = {
        "payload": {
            "parts": [
                {"mimeType": "text/plain", "body": {"data": "VGhpcyBtZXNzYWdlIGNvbnRhaW5zIEhUTUwgY29udGVudC4="}},
                {"mimeType": "text/html", "body": {"data": base64.urlsafe_b64encode(html.encode()).decode()}},
            ]
        }
    }

    assert "HTML content" not in envelope(message, account_timezone="America/Sao_Paulo")["snippet"]
    assert "?track" not in envelope(message, account_timezone="America/Sao_Paulo")["snippet"]
    assert "____" not in envelope(message, account_timezone="America/Sao_Paulo")["snippet"]


def test_github_notification_mail_is_automated_even_with_a_personal_display_name():
    message = {
        "payload": {
            "headers": [
                {"name": "From", "value": "Guilherme Inácio <notifications@github.com>"},
                {"name": "Subject", "value": "[repo] Workflow run failed"},
                {"name": "X-GitHub-Reason", "value": "ci_activity"},
            ],
            "parts": [{"mimeType": "text/plain", "body": {"data": "V29ya2Zsb3cgdXBkYXRl"}}],
        }
    }

    result = envelope(message, account_timezone="America/Sao_Paulo")

    assert result["from"]["name"] == "Guilherme Inácio"
    assert result["is_automated"] is True


class _HistoryRequest:
    def __init__(self, kind: str, message_id: str | None = None) -> None:
        self.kind = kind
        self.message_id = message_id


class _HistoryMessages:
    def get(self, *, userId: str, id: str, format: str) -> _HistoryRequest:
        return _HistoryRequest("message", id)


class _HistoryList:
    def list(self, **kwargs) -> _HistoryRequest:
        return _HistoryRequest("history")


class _HistoryUsers:
    def history(self) -> _HistoryList:
        return _HistoryList()

    def messages(self) -> _HistoryMessages:
        return _HistoryMessages()


class _HistoryService:
    def users(self) -> _HistoryUsers:
        return _HistoryUsers()


def test_check_mail_updates_skips_deleted_history_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_timezone() -> str:
        return "UTC"

    async def fake_execute(request: _HistoryRequest) -> dict:
        if request.kind == "history":
            return {
                "historyId": "200",
                "history": [
                    {
                        "messagesAdded": [
                            {"message": {"id": "deleted"}},
                            {"message": {"id": "available"}},
                        ]
                    }
                ],
            }
        if request.message_id == "deleted":
            raise HttpError(Response({"status": "404"}), b'{"error":{"message":"Not found"}}')
        return {
            "id": "available",
            "threadId": "thread-1",
            "labelIds": ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "Sender <sender@example.com>"},
                    {"name": "Subject", "value": "Still available"},
                ]
            },
        }

    monkeypatch.setattr(gmail_history, "gmail_service", _HistoryService)
    monkeypatch.setattr(gmail_history, "resolve_user_timezone", fake_timezone)
    monkeypatch.setattr(gmail_history, "execute_google_request", fake_execute)

    async def scenario() -> dict:
        async with Client(gmail_mcp) as client:
            result = await client.call_tool(
                "check_mail_updates",
                {"since_history_id": "100", "max_results": 50},
            )
            return result.structured_content or result.data

    result = asyncio.run(scenario())

    assert result["count"] == 1
    assert result["next_history_id"] == "200"
    assert result["skipped_deleted_count"] == 1
    assert result["skipped_deleted_message_ids"] == ["deleted"]
    assert [item["id"] for item in result["messages"]] == ["available"]
    assert result["messages"][0]["direction"] == "received"
    assert "all_new_messages" not in result
    assert "highlights" not in result
    assert "counts_by_category" not in result


def test_read_emails_has_one_consistent_batch_shape_and_isolates_missing_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_timezone() -> str:
        return "UTC"

    async def fake_execute(request: _HistoryRequest) -> dict:
        if request.message_id == "deleted":
            raise HttpError(Response({"status": "404"}), b'{"error":{"message":"Not found"}}')
        return {
            "id": request.message_id,
            "threadId": "thread-1",
            "historyId": "200",
            "labelIds": ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "Sender <sender@example.com>"},
                    {"name": "To", "value": "me@example.com"},
                    {"name": "Subject", "value": "Still available"},
                ]
            },
        }

    monkeypatch.setattr(gmail_messages, "gmail_service", _HistoryService)
    monkeypatch.setattr(gmail_messages, "resolve_user_timezone", fake_timezone)
    monkeypatch.setattr(gmail_messages, "execute_google_request", fake_execute)

    async def scenario() -> dict:
        async with Client(gmail_mcp) as client:
            result = await client.call_tool(
                "read_emails",
                {
                    "message_ids": ["available", "deleted"],
                    "format": "metadata",
                },
            )
            return result.structured_content or result.data

    result = asyncio.run(scenario())

    assert result["format"] == "metadata"
    assert result["missing_count"] == 1
    assert result["missing_message_ids"] == ["deleted"]
    assert len(result["messages"]) == 1
    assert result["messages"][0]["id"] == "available"
    assert result["messages"][0]["to"] == "me@example.com"
    assert result["messages"][0]["bodies_omitted"] is True


def _labelled_message(message_id: str, labels: list[str], sender: str, subject: str) -> dict:
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "labelIds": labels,
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
            ],
            "parts": [{"mimeType": "text/plain", "body": {"data": "SGVsbG8gd29ybGQ="}}],
        },
    }


def test_envelope_flags_drafts_and_sent_messages():
    draft = _labelled_message("d1", ["DRAFT"], "Me <me@example.com>", "Work in progress")
    sent = _labelled_message("s1", ["SENT"], "Me <me@example.com>", "Delivered")
    received = _labelled_message("r1", ["INBOX", "UNREAD"], "Alice <alice@example.com>", "Hello")

    draft_env = envelope(draft, account_timezone="UTC")
    sent_env = envelope(sent, account_timezone="UTC")
    received_env = envelope(received, account_timezone="UTC")

    assert draft_env["is_draft"] is True
    assert draft_env["is_sent"] is False
    assert sent_env["is_sent"] is True
    assert sent_env["is_draft"] is False
    assert received_env["is_draft"] is False
    assert received_env["is_sent"] is False


def test_check_mail_updates_returns_received_and_sent_messages_without_triage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_timezone() -> str:
        return "UTC"

    async def fake_execute(request: _HistoryRequest) -> dict:
        if request.kind == "history":
            return {
                "historyId": "300",
                "history": [
                    {
                        "messagesAdded": [
                            {"message": {"id": "draft-1"}},
                            {"message": {"id": "sent-1"}},
                            {"message": {"id": "linkedin-1"}},
                            {"message": {"id": "direct-1"}},
                        ]
                    }
                ],
            }
        if request.message_id == "draft-1":
            return _labelled_message("draft-1", ["DRAFT"], "Me <me@example.com>", "Unsent draft")
        if request.message_id == "sent-1":
            return _labelled_message("sent-1", ["SENT"], "Me <me@example.com>", "Sent message")
        if request.message_id == "linkedin-1":
            message = _labelled_message(
                "linkedin-1",
                ["CATEGORY_SOCIAL", "Label_routed"],
                "LinkedIn <messages-noreply@linkedin.com>",
                "A recruiter sent you a message",
            )
            message["payload"]["headers"].extend(
                [
                    {"name": "Precedence", "value": "bulk"},
                    {"name": "List-Unsubscribe", "value": "<https://linkedin.com/unsubscribe>"},
                ]
            )
            return message
        return _labelled_message("direct-1", ["INBOX"], "Alice <alice@example.com>", "Actual mail")

    monkeypatch.setattr(gmail_history, "gmail_service", _HistoryService)
    monkeypatch.setattr(gmail_history, "resolve_user_timezone", fake_timezone)
    monkeypatch.setattr(gmail_history, "execute_google_request", fake_execute)

    async def scenario() -> dict:
        async with Client(gmail_mcp) as client:
            result = await client.call_tool(
                "check_mail_updates",
                {"since_history_id": "100", "max_results": 50},
            )
            return result.structured_content or result.data

    result = asyncio.run(scenario())

    assert result["count"] == 3
    assert [item["id"] for item in result["messages"]] == ["sent-1", "linkedin-1", "direct-1"]
    assert {item["id"]: item["direction"] for item in result["messages"]} == {
        "sent-1": "sent",
        "linkedin-1": "received",
        "direct-1": "received",
    }
    assert "all_new_messages" not in result
    assert "highlights" not in result
    assert "counts_by_category" not in result
    for message in result["messages"]:
        assert not {
            "category",
            "is_newsletter",
            "is_automated",
            "is_draft",
            "is_sent",
            "requires_response",
            "deadline_detected",
        } & message.keys()


def test_search_helper_filters_quote_and_escape_address_values() -> None:
    request = SearchEmailRequest(
        from_email='Jane "JJ" Doe <jane@example.com>',
        to_email="Support Team <support@example.com>",
        subject_contains='Quarterly "Review"',
    )

    query = gmail_search._build_search_query(request)

    assert query == (
        'from:"Jane \\"JJ\\" Doe <jane@example.com>" '
        'to:"Support Team <support@example.com>" '
        'subject:"Quarterly \\"Review\\""'
    )


class _SearchRequest:
    def __init__(self, kind: str, message_id: str | None = None, query: str | None = None) -> None:
        self.kind = kind
        self.message_id = message_id
        self.query = query


class _SearchMessages:
    def list(self, **kwargs) -> _SearchRequest:
        return _SearchRequest("list", query=kwargs.get("q"))

    def get(self, *, userId: str, id: str, format: str) -> _SearchRequest:
        return _SearchRequest("message", id)


class _SearchUsers:
    def messages(self) -> _SearchMessages:
        return _SearchMessages()


class _SearchService:
    def users(self) -> _SearchUsers:
        return _SearchUsers()


class _ConcurrentSearchMessages:
    def list(self, **kwargs) -> _SearchRequest:
        return _SearchRequest("list", query=kwargs.get("q"))

    def get(self, *, id: str, **kwargs) -> _SearchRequest:
        assert "fields" in kwargs  # search asks for the envelope mask, not bodies
        return _SearchRequest("message", id)


class _ConcurrentSearchService:
    def users(self) -> Any:
        return SimpleNamespace(messages=_ConcurrentSearchMessages)


def test_search_emails_fetches_details_concurrently_and_keeps_result_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = [f"m-{index}" for index in range(6)]
    in_flight = 0
    peak = 0

    async def fake_timezone() -> str:
        return "UTC"

    async def fake_execute(request: _SearchRequest) -> dict:
        nonlocal in_flight, peak
        if request.kind == "list":
            return {"messages": [{"id": message_id} for message_id in ids], "resultSizeEstimate": len(ids)}
        assert request.message_id is not None
        in_flight += 1
        peak = max(peak, in_flight)
        # Later hits finish first; the response must still follow Gmail's order.
        await anyio.sleep(0.02 if request.message_id == "m-0" else 0.001)
        in_flight -= 1
        return _labelled_message(request.message_id, ["INBOX"], "Alice <alice@example.com>", request.message_id)

    monkeypatch.setattr(gmail_search, "gmail_service", _ConcurrentSearchService)
    monkeypatch.setattr(gmail_search, "resolve_user_timezone", fake_timezone)
    monkeypatch.setattr(gmail_search, "execute_google_request", fake_execute)

    async def scenario() -> dict:
        async with Client(gmail_mcp) as client:
            result = await client.call_tool("search_emails", {"query": "from:alice"})
            return result.structured_content or result.data

    result = anyio.run(scenario)

    assert [message["id"] for message in result["messages"]] == ids
    assert result["count"] == len(ids)
    assert peak > 1, "message details were fetched one at a time"


def test_get_mail_digest_returns_received_and_sent_without_triage_or_thread_deduplication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_queries: list[str | None] = []
    messages = {
        "draft-1": _labelled_message("draft-1", ["DRAFT"], "Me <me@example.com>", "Unsent draft"),
        "sent-1": _labelled_message("sent-1", ["SENT"], "Me <me@example.com>", "Sent message"),
        "linkedin-1": _labelled_message(
            "linkedin-1",
            ["CATEGORY_SOCIAL", "Label_routed"],
            "LinkedIn <messages-noreply@linkedin.com>",
            "A recruiter sent you a message",
        ),
        "person-1": _labelled_message("person-1", ["INBOX", "UNREAD"], "Alice <alice@example.com>", "First"),
        "person-2": _labelled_message("person-2", ["INBOX"], "Alice <alice@example.com>", "Follow-up"),
    }
    messages["linkedin-1"]["payload"]["headers"].extend(
        [
            {"name": "Precedence", "value": "bulk"},
            {"name": "List-Unsubscribe", "value": "<https://linkedin.com/unsubscribe>"},
        ]
    )
    messages["person-1"]["threadId"] = "shared-thread"
    messages["person-2"]["threadId"] = "shared-thread"

    async def fake_timezone() -> str:
        return "UTC"

    async def fake_execute(request: _SearchRequest) -> dict:
        if request.kind == "list":
            seen_queries.append(request.query)
            return {"messages": [{"id": message_id} for message_id in messages]}
        assert request.message_id is not None
        return messages[request.message_id]

    monkeypatch.setattr(gmail_search, "gmail_service", _SearchService)
    monkeypatch.setattr(gmail_search, "resolve_user_timezone", fake_timezone)
    monkeypatch.setattr(gmail_search, "execute_google_request", fake_execute)

    async def scenario() -> dict:
        async with Client(gmail_mcp) as client:
            result = await client.call_tool("get_mail_digest", {"window": "3d"})
            return result.structured_content or result.data

    result = asyncio.run(scenario())

    assert result["count"] == 4
    assert [item["id"] for item in result["messages"]] == ["sent-1", "linkedin-1", "person-1", "person-2"]
    assert {item["id"]: item["direction"] for item in result["messages"]} == {
        "sent-1": "sent",
        "linkedin-1": "received",
        "person-1": "received",
        "person-2": "received",
    }
    assert "people" not in result
    assert "automated" not in result
    # The digest is not a checkpoint source (gmail_check_mail_updates is); no always-null cursor.
    assert "next_history_id" not in result
    for message in result["messages"]:
        assert not {
            "category",
            "is_newsletter",
            "is_automated",
            "is_draft",
            "is_sent",
            "requires_response",
            "deadline_detected",
        } & message.keys()
    assert seen_queries and "-in:draft" in (seen_queries[0] or "")
    assert "-in:sent" not in (seen_queries[0] or "")


def test_gather_in_order_preserves_order_and_bounds_concurrency() -> None:
    concurrent = 0
    peak = 0

    async def worker(n: int) -> int:
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        # Later items finish sooner, so completion order is the reverse of input.
        await anyio.sleep((12 - n) * 0.001)
        concurrent -= 1
        return n * 10

    async def scenario() -> list[int]:
        return await gather_in_order(list(range(12)), worker, limit=3)

    result = asyncio.run(scenario())

    assert result == [n * 10 for n in range(12)]
    assert peak <= 3


def test_gather_in_order_surfaces_the_original_worker_error() -> None:
    async def worker(n: int) -> int:
        if n == 2:
            raise ValueError("boom")
        return n

    async def scenario() -> list[int]:
        return await gather_in_order([1, 2, 3], worker)

    # The lone failure must not arrive wrapped in an ExceptionGroup, or the
    # error middleware would lose the exception type it maps to envelopes.
    with pytest.raises(ValueError, match="boom"):
        asyncio.run(scenario())


def test_gather_in_order_surfaces_one_error_when_several_workers_fail() -> None:
    """Parallel fetches hit by the same 429 still classify as ``rate_limited``."""

    # Google requests run in worker threads that a cancelled task group still
    # waits for, so every in-flight fetch reports its own failure. The barrier
    # makes all three in flight before any fails.
    barrier = threading.Barrier(3, timeout=5)

    def fetch() -> int:
        barrier.wait()
        raise HttpError(Response({"status": "429"}), b'{"error": {"code": 429, "message": "Too many requests"}}')

    async def worker(n: int) -> int:
        return await anyio.to_thread.run_sync(fetch)

    async def scenario() -> list[int]:
        return await gather_in_order([1, 2, 3], worker)

    with pytest.raises(HttpError) as raised:
        asyncio.run(scenario())
    rpc_code, envelope = classify_error(raised.value)
    assert rpc_code is None
    assert envelope["code"] == "rate_limited"


@pytest.mark.parametrize(
    "arguments",
    [
        {"message_ids": ["m1"]},
        {"thread_id": "t1"},
        {"message_ids": ["m1", "m2"], "add_labels": []},
    ],
)
def test_modify_messages_rejects_a_call_that_changes_nothing(arguments: dict) -> None:
    # Empty label lists and no flags must raise, not silently no-op.
    async def scenario() -> None:
        async with Client(gmail_mcp) as client:
            await client.call_tool("modify_messages", arguments)

    with pytest.raises(ToolError, match="Nothing to change"):
        asyncio.run(scenario())
