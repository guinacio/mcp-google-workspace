import asyncio
import base64

import pytest

from mcp_google_workspace.common.async_ops import execute_google_request
from mcp_google_workspace.gmail.mime_utils import build_email_message, encode_subject, extract_message_bodies
from mcp_google_workspace.gmail.presentation import (
    clean_message_content,
    detect_deadline,
    envelope,
    first_meaningful_sentence,
    requires_response,
)
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

    result = envelope(message, account_timezone="America/Sao_Paulo")

    assert result["from"] == {"name": "News", "email": "no-reply@example.com"}
    assert result["category"] == "CATEGORY_PROMOTIONS"
    assert result["unread"] is True
    assert result["is_newsletter"] is True
    assert result["is_automated"] is True
    assert result["date"] == "2026-07-09T07:00:00-03:00"
    assert result["date_timezone"] == "America/Sao_Paulo"
    assert result["source_date"] == "Wed, 9 Jul 2026 10:00:00 +0000"


def test_clean_message_content_collapses_quote_and_truncates():
    text = "Current reply\n\nOn Tuesday someone wrote:\n> Earlier message"
    message = {"payload": {"parts": [{"mimeType": "text/plain", "body": {"data": "Q3VycmVudCByZXBseQoKT24gVHVlc2RheSBzb21lb25lIHdyb3RlOgo+IEVhcmxpZXIgbWVzc2FnZQ=="}}]}}

    result = clean_message_content(message, limit=10)

    assert result["body"] == text[:10]
    assert result["truncated"] is True


def test_invalid_grant_from_unknown_request_preserves_token_and_has_actionable_error():
    class FailedRequest:
        def execute(self):
            raise RuntimeError("invalid_grant: Bad Request")

    with pytest.raises(RuntimeError, match="reauth_required") as error:
        asyncio.run(execute_google_request(FailedRequest()))

    assert "OAuth consent" in str(error.value)


def test_deadline_detection_requires_a_real_date_or_time_and_supports_portuguese():
    text = "Aguardo sua devolutiva até segunda-feira, dia 13/07, às 18h."

    assert detect_deadline(text, date_header="Thu, 9 Jul 2026 10:00:00 -0300") == "2026-07-13T18:00-03:00"
    assert detect_deadline(
        text,
        date_header="Thu, 9 Jul 2026 10:00:00 +0000",
        account_timezone="America/Sao_Paulo",
    ) == "2026-07-13T18:00-03:00"
    assert detect_deadline("Built by Docker and maintained by LangChain.") is None


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


def test_response_detection_excludes_newsletters_and_automated_messages():
    assert requires_response("What do you do now?", is_automated=True, is_newsletter=False) is False
    assert requires_response("Can you reply today?", is_automated=False, is_newsletter=False) is True


def test_gist_skips_a_salutation_for_the_first_substantive_sentence():
    text = "Olá, Guilherme!\n\nA proposta atualizada está pronta para sua revisão antes de sexta-feira."

    assert first_meaningful_sentence(text) == "A proposta atualizada está pronta para sua revisão antes de sexta-feira."


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
