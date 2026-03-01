"""MIME encoding/decoding helpers for Gmail messages."""

from __future__ import annotations

import base64
from email import policy
from email.header import Header, decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Any


def encode_subject(subject: str) -> str:
    """Ensure full UTF-8 support for internationalized subjects."""
    return str(Header(subject, "utf-8"))


def build_email_message(
    subject: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    text_body: str | None,
    html_body: str | None,
    attachments: list[dict[str, str]],
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = encode_subject(subject)
    if to:
        msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)

    if text_body and html_body:
        msg.set_content(text_body, charset="utf-8")
        msg.add_alternative(html_body, subtype="html", charset="utf-8")
    elif html_body:
        msg.set_content("This message contains HTML content.", charset="utf-8")
        msg.add_alternative(html_body, subtype="html", charset="utf-8")
    else:
        msg.set_content(text_body or "", charset="utf-8")

    for attachment in attachments:
        file_path = Path(attachment["path"])
        file_name = attachment.get("filename") or file_path.name
        content_type = attachment.get("mime_type") or "application/octet-stream"
        maintype, subtype = content_type.split("/", 1) if "/" in content_type else ("application", "octet-stream")
        payload = file_path.read_bytes()
        msg.add_attachment(payload, maintype=maintype, subtype=subtype, filename=file_name)

    return msg


def email_to_gmail_raw(message: EmailMessage) -> str:
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    return raw


def decode_rfc2047(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def decode_part_data(data: str | None) -> str:
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")
    except Exception:
        return ""


def flatten_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    queue = [payload]
    while queue:
        item = queue.pop(0)
        parts.append(item)
        for sub in item.get("parts", []):
            queue.append(sub)
    return parts


def extract_message_bodies(payload: dict[str, Any]) -> dict[str, str]:
    text_body = ""
    html_body = ""
    for part in flatten_parts(payload):
        mime_type = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        decoded = decode_part_data(data)
        if mime_type == "text/plain" and decoded and not text_body:
            text_body = decoded
        elif mime_type == "text/html" and decoded and not html_body:
            html_body = decoded
    return {"text": text_body, "html": html_body}


def parse_raw_message(raw_base64: str) -> dict[str, Any]:
    data = base64.urlsafe_b64decode(raw_base64.encode("utf-8"))
    parsed = BytesParser(policy=policy.default).parsebytes(data)
    attachments: list[dict[str, Any]] = []
    text = ""
    html = ""

    for part in parsed.walk():
        content_disposition = part.get_content_disposition()
        content_type = part.get_content_type()
        if content_disposition == "attachment":
            payload = part.get_payload(decode=True) or b""
            attachments.append(
                {
                    "filename": part.get_filename(),
                    "content_type": content_type,
                    "size": len(payload),
                }
            )
        elif content_type == "text/plain" and not text:
            text = part.get_content()
        elif content_type == "text/html" and not html:
            html = part.get_content()

    return {"subject": decode_rfc2047(parsed.get("Subject")), "text": text, "html": html, "attachments": attachments}
