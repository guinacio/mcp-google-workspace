import asyncio

import pytest

from mcp_google_workspace.common.async_ops import execute_google_request
from mcp_google_workspace.gmail.mime_utils import build_email_message, encode_subject, extract_message_bodies
from mcp_google_workspace.gmail.presentation import clean_message_content, envelope
from mcp_google_workspace.gmail.schemas import SendEmailRequest


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


def test_send_email_defaults_to_no_confirmation():
    request = SendEmailRequest(
        recipients={"to": ["a@example.com"]},
        subject="Test",
    )

    assert request.confirm_send is False


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

    result = envelope(message)

    assert result["from"] == {"name": "News", "email": "no-reply@example.com"}
    assert result["category"] == "CATEGORY_PROMOTIONS"
    assert result["unread"] is True
    assert result["is_newsletter"] is True
    assert result["is_automated"] is True


def test_clean_message_content_collapses_quote_and_truncates():
    text = "Current reply\n\nOn Tuesday someone wrote:\n> Earlier message"
    message = {"payload": {"parts": [{"mimeType": "text/plain", "body": {"data": "Q3VycmVudCByZXBseQoKT24gVHVlc2RheSBzb21lb25lIHdyb3RlOgo+IEVhcmxpZXIgbWVzc2FnZQ=="}}]}}

    result = clean_message_content(message, limit=10)

    assert result["body"] == text[:10]
    assert result["truncated"] is True


def test_invalid_grant_has_an_actionable_error():
    class FailedRequest:
        def execute(self):
            raise RuntimeError("invalid_grant: Bad Request")

    with pytest.raises(RuntimeError, match="reauth_required") as error:
        asyncio.run(execute_google_request(FailedRequest()))

    assert "Reconnect Google Workspace" in str(error.value)
