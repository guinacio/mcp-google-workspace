from mcp_google_workspace.gmail.mime_utils import build_email_message, encode_subject, extract_message_bodies


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
