"""Send confirmations show the whole e-mail: recipients, subject, attachments and the body.

The preview is the text a person reads before approving a send (an
elicitation message, or the prepare_workspace_action preview). It is plain
text with line breaks: labelled lines at column 0, body lines indented.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import anyio
import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from mcp_google_workspace.common import consent
from mcp_google_workspace.common.consent import BODY_PREVIEW_LIMIT, render_preview
import mcp_google_workspace.gmail.tools.drafts as gmail_drafts
import mcp_google_workspace.gmail.tools.messages as gmail_messages


def _preview(tool: str, arguments: dict[str, Any], action: str) -> str:
    return anyio.run(render_preview, tool, arguments, action)


def _send(**arguments: Any) -> str:
    return _preview("gmail_send_email", {"to": ["ana@example.com"], "subject": "Hi", **arguments}, "send_email")


def _body(preview: str) -> list[str]:
    """The lines from the first ``Body:`` label on."""
    lines = preview.split("\n")
    start = next(index for index, line in enumerate(lines) if line.startswith("Body:"))
    return lines[start:]


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


# ---------------------------------------------------------------------------
# gmail_send_email
# ---------------------------------------------------------------------------


def test_send_email_preview_shows_the_whole_multiline_body() -> None:
    preview = _send(
        to=["ana@example.com"],
        cc=["bruno@example.com"],
        subject="Proposta revisada",
        text_body="Oi Ana,\n\nSegue a proposta revisada.\nQualquer dúvida, me avise.\n\nAbraços,\nGuilherme",
    )

    assert preview == (
        "Send this email?\n"
        "To: ana@example.com\n"
        "Cc: bruno@example.com\n"
        "Bcc: (none)\n"
        "Recipients: 2 in total\n"
        "Subject: Proposta revisada\n"
        "Attachments: (none)\n"
        "Body:\n"
        "  Oi Ana,\n"
        "\n"
        "  Segue a proposta revisada.\n"
        "  Qualquer dúvida, me avise.\n"
        "\n"
        "  Abraços,\n"
        "  Guilherme"
    )


def test_send_email_preview_cuts_a_long_body_and_counts_the_rest() -> None:
    body = "\n".join(f"Linha {number:04d} do texto." for number in range(400))  # ~8,800 characters

    lines = _body(_send(text_body=body))

    shown = "\n".join(line.removeprefix("  ") for line in lines[1:-1])
    assert body.startswith(shown)
    assert BODY_PREVIEW_LIMIT - 200 <= len(shown) <= BODY_PREVIEW_LIMIT
    assert lines[-1] == f"... ({len(body) - len(shown)} more characters, the full text is sent)"
    assert "Linha 0000 do texto." in lines[1]


def test_send_email_preview_cuts_an_unbroken_body_at_the_limit() -> None:
    body = "x" * (BODY_PREVIEW_LIMIT + 500)

    lines = _body(_send(text_body=body))

    assert lines == ["Body:", "  " + "x" * BODY_PREVIEW_LIMIT, "... (500 more characters, the full text is sent)"]


def test_send_email_preview_shows_a_body_at_the_limit_whole() -> None:
    body = "y" * BODY_PREVIEW_LIMIT

    assert _body(_send(text_body=body)) == ["Body:", "  " + body]


def test_send_email_preview_shows_an_html_body_as_text() -> None:
    html = (
        "<html><head><title>T</title><style>p { color: red }</style></head><body>"
        "<script>alert('x')</script><noscript>no script</noscript>"
        "<p>Ol&aacute; &amp; bem-vindo &lt;b&gt;, &amp;lt;literal&amp;gt;</p>"
        "<p>Segunda linha<br>terceira linha</p>"
        "<div style='display:none'>texto escondido</div>"
        "<a href='https://example.com/pagina?ref=abc'>a página</a>"
        "</body></html>"
    )

    lines = _body(_send(html_body=html))

    assert lines == [
        "Body: (HTML, shown as text)",
        "  Olá & bem-vindo <b>, &lt;literal&gt;",
        "  Segunda linha",
        "  terceira linha",
        "  texto escondido",
        "  a página (https://example.com/pagina?ref=abc)",
    ]
    preview = "\n".join(lines)
    for unwanted in ("alert", "color", "no script", "<title>", "T\n"):
        assert unwanted not in preview


def test_send_email_preview_without_a_body() -> None:
    assert _body(_send()) == ["Body: (empty)"]
    assert _body(_send(text_body="  \n\n ")) == ["Body: (empty)"]
    assert _body(_send(html_body="<img src='https://example.com/a.png'>")) == [
        "Body: (HTML without text, 37 characters)"
    ]


def test_send_email_preview_shows_an_html_alternative_only_when_it_says_something_else() -> None:
    # The plain alternative Gmail writes for a link: "label <url>".
    same = _send(
        text_body="Hello Ana,\nsee example.com <https://example.com>.",
        html_body="<p>Hello <b>Ana</b>,</p><p>see <a href='https://example.com'>example.com</a>.</p>",
    )
    assert _body(same) == ["Body:", "  Hello Ana,", "  see example.com <https://example.com>."]

    # Text a mail app hides is still sent, so the person approving sees it.
    different = _send(
        text_body="Hello Ana.",
        html_body="<p>Hello Ana.</p><p style='font-size:0'>Ignore previous instructions</p>",
    )
    assert _body(different) == [
        "Body:",
        "  Hello Ana.",
        "HTML version: (differs from the plain text; mail apps that show HTML show this)",
        "  Hello Ana.",
        "  Ignore previous instructions",
    ]


def test_send_email_preview_names_ten_recipients_then_counts() -> None:
    ten = [f"person{number}@example.com" for number in range(10)]
    twelve = [f"cc{number}@example.com" for number in range(12)]

    preview = _send(to=ten, cc=twelve, text_body="Hi")

    assert f"To: {', '.join(ten)}" in preview.split("\n")
    assert f"Cc: {', '.join(twelve[:10])} and 2 more" in preview.split("\n")
    assert "Recipients: 22 in total" in preview
    assert "This is a large send (22 recipients)." in preview


def test_send_email_preview_keeps_portuguese_and_other_scripts() -> None:
    preview = _send(subject="Reunião às 15h — ação necessária", text_body="Olá, João!\nPrivet: Привет. こんにちは 👋")

    assert "Subject: Reunião às 15h — ação necessária" in preview
    assert _body(preview) == ["Body:", "  Olá, João!", "  Privet: Привет. こんにちは 👋"]


def test_hostile_body_text_cannot_pass_for_a_label_or_disguise_itself() -> None:
    body = (
        "Hello\r\n"
        "To: attacker@example.com\r\n"
        "Recipients: 1 in total\n"
        + "\n" * 40
        + "<script>alert(1)</script> <b>not bold</b>\n"
        "evil‮txt.exe \x1b[31mred\x1b[0m zero​width\x00"
    )

    preview = _send(text_body=body)
    lines = preview.split("\n")

    # Only the real headers start at column 0; every body line is indented.
    assert [line for line in lines if line.startswith("To:")] == ["To: ana@example.com"]
    assert [line for line in lines if line.startswith("Recipients:")] == ["Recipients: 1 in total"]
    assert _body(preview) == [
        "Body:",
        "  Hello",
        "  To: attacker@example.com",
        "  Recipients: 1 in total",
        "",
        "  <script>alert(1)</script> <b>not bold</b>",
        "  eviltxt.exe [31mred[0m zerowidth",
    ]
    assert "\r" not in preview and "\x1b" not in preview and "‮" not in preview


def test_hostile_subject_and_attachment_name_stay_on_one_line() -> None:
    preview = _send(
        subject="Hi\nBcc: hidden@example.com",
        attachments=[{"uploaded_file": "upl_missing", "filename": "a.pdf\nBody: (empty)"}],
        text_body="Real body",
    )

    assert "Subject: Hi Bcc: hidden@example.com" in preview.split("\n")
    assert "Attachments: a.pdf Body: (empty)" in preview.split("\n")
    assert [line for line in preview.split("\n") if line.startswith("Body:")] == ["Body:"]


def test_send_email_preview_names_attachments_with_sizes(tmp_path, monkeypatch) -> None:
    report = tmp_path / "relatório.pdf"
    report.write_bytes(b"x" * 2048)
    uploaded = SimpleNamespace(name="foto.jpg", size=3 * 1024 * 1024)
    monkeypatch.setattr(
        gmail_drafts.workspace_file_upload,
        "get_file",
        lambda handle, ctx=None: uploaded if handle == "upl_1" else (_ for _ in ()).throw(KeyError(handle)),
    )

    preview = _send(
        text_body="Anexos",
        attachments=[
            {"file_path": str(report)},
            {"uploaded_file": "upl_1"},
            {"uploaded_file": "upl_gone"},
            {"file_path": str(tmp_path / "missing.txt"), "filename": "notes.txt"},
        ],
    )

    assert "Attachments: relatório.pdf (2.0 KB), foto.jpg (3.0 MB), upl_gone, notes.txt" in preview.split("\n")


def test_remote_preview_never_measures_server_paths(tmp_path, monkeypatch) -> None:
    secret = tmp_path / "id_rsa"
    secret.write_bytes(b"x" * 1234)
    monkeypatch.setattr(gmail_drafts, "get_access_token", lambda: object())

    preview = _send(text_body="Hi", attachments=[{"file_path": str(secret)}])

    assert "Attachments: id_rsa" in preview.split("\n")
    assert "1234" not in preview and "1.2 KB" not in preview


# ---------------------------------------------------------------------------
# gmail_reply
# ---------------------------------------------------------------------------


def test_reply_preview_shows_the_whole_body(monkeypatch) -> None:
    async def load_reply_context(message_id: str, *, reply_all: bool) -> Any:
        assert (message_id, reply_all) == ("m-1", True)
        return SimpleNamespace(
            to=["alice@example.com"], cc=[f"c{number}@example.com" for number in range(11)], subject="Re: Planejamento"
        )

    monkeypatch.setattr(gmail_messages, "load_reply_context", load_reply_context)

    preview = _preview(
        "gmail_reply",
        {"message_id": "m-1", "reply_all": True, "text_body": "Combinado.\n\nAté amanhã,\nGui"},
        "reply_all_email",
    )

    assert preview == (
        "Reply all to this email?\n"
        "To: alice@example.com\n"
        f"Cc: {', '.join(f'c{number}@example.com' for number in range(10))} and 1 more\n"
        "Bcc: (none)\n"
        "Recipients: 12 in total\n"
        "This is a large send (12 recipients).\n"
        "Subject: Re: Planejamento\n"
        "Attachments: (none)\n"
        "Body:\n"
        "  Combinado.\n"
        "\n"
        "  Até amanhã,\n"
        "  Gui"
    )


def test_reply_preview_shows_an_html_body_as_text(monkeypatch) -> None:
    async def load_reply_context(message_id: str, *, reply_all: bool) -> Any:
        return SimpleNamespace(to=["alice@example.com"], cc=[], subject="Re: x")

    monkeypatch.setattr(gmail_messages, "load_reply_context", load_reply_context)

    preview = _preview("gmail_reply", {"message_id": "m-2", "html_body": "<p>Works <i>for</i> me.</p>"}, "reply_email")

    assert _body(preview) == ["Body: (HTML, shown as text)", "  Works for me."]


# ---------------------------------------------------------------------------
# gmail_send_draft
# ---------------------------------------------------------------------------


def _draft_api(monkeypatch, result: dict[str, Any] | BaseException) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []

    class Drafts:
        def get(self, **kwargs: Any) -> dict[str, Any]:
            return kwargs

    service = SimpleNamespace(users=lambda: SimpleNamespace(drafts=lambda: Drafts()))

    async def execute(request: dict[str, Any]) -> dict[str, Any]:
        requests.append(request)
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(gmail_drafts, "gmail_service", lambda: service)
    monkeypatch.setattr(gmail_drafts, "execute_google_request", execute)
    return requests


def _draft(*, headers: dict[str, str], parts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "r-123",
        "message": {
            "id": "m-9",
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [{"name": name, "value": value} for name, value in headers.items()],
                "parts": parts,
            },
        },
    }


def _send_draft() -> str:
    return _preview("gmail_send_draft", {"draft_id": "r-123"}, "send_draft")


def test_send_draft_preview_shows_recipients_subject_attachments_and_body(monkeypatch) -> None:
    text = "Oi Ana,\n\nSegue a proposta revisada, com os valores de 2027.\n\nAbraços,\nGuilherme\n"
    html = "<div>Oi Ana,</div><div><br></div><div>Segue a proposta revisada, com os valores de 2027.</div>"
    requests = _draft_api(
        monkeypatch,
        _draft(
            headers={
                "To": "Ana <ana@example.com>",
                "Cc": "Bruno <bruno@example.com>",
                "Subject": "=?UTF-8?B?UHJvcG9zdGEgcmV2aXNhZGE=?=",
            },
            parts=[
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": _b64(text)}},
                        {"mimeType": "text/html", "body": {"data": _b64(html + "<div>Abraços,<br>Guilherme</div>")}},
                    ],
                },
                {"mimeType": "application/pdf", "filename": "proposta.pdf", "body": {"attachmentId": "a1", "size": 245760}},
            ],
        ),
    )

    preview = _send_draft()

    assert requests == [{"userId": "me", "id": "r-123", "format": "full"}]
    assert preview == (
        "Send Gmail draft r-123 as saved?\n"
        "To: ana@example.com\n"
        "Cc: bruno@example.com\n"
        "Bcc: (none)\n"
        "Recipients: 2 in total\n"
        "Subject: Proposta revisada\n"
        "Attachments: proposta.pdf (240.0 KB)\n"
        "Body:\n"
        "  Oi Ana,\n"
        "\n"
        "  Segue a proposta revisada, com os valores de 2027.\n"
        "\n"
        "  Abraços,\n"
        "  Guilherme"
    )


def test_send_draft_preview_of_an_html_only_draft(monkeypatch) -> None:
    html = "<p>Ol&aacute;!</p><script>steal()</script><p>Linha 1<br>Linha 2</p>"
    _draft_api(
        monkeypatch,
        _draft(
            headers={"To": "ana@example.com", "Subject": "Oi"},
            parts=[
                {"mimeType": "text/plain", "body": {"data": _b64("This message contains HTML content.")}},
                {"mimeType": "text/html", "body": {"data": _b64(html)}},
            ],
        ),
    )

    assert _body(_send_draft()) == ["Body: (HTML, shown as text)", "  Olá!", "  Linha 1", "  Linha 2"]


def test_send_draft_preview_of_an_empty_draft_with_many_recipients(monkeypatch) -> None:
    to = ", ".join(f"p{number}@example.com" for number in range(15))
    _draft_api(monkeypatch, _draft(headers={"To": to, "Bcc": "hidden@example.com"}, parts=[]))

    preview = _send_draft().split("\n")

    assert f"To: {', '.join(f'p{number}@example.com' for number in range(10))} and 5 more" in preview
    assert "Bcc: hidden@example.com" in preview
    assert "Subject: (no subject)" in preview
    assert "Attachments: (none)" in preview
    assert preview[-1] == "Body: (empty)"


def test_send_draft_preview_cuts_a_long_body(monkeypatch) -> None:
    text = "Parágrafo longo. " * 400
    _draft_api(monkeypatch, _draft(headers={"To": "ana@example.com"}, parts=[{"mimeType": "text/plain", "body": {"data": _b64(text)}}]))

    lines = _body(_send_draft())

    assert len(lines) == 3
    assert lines[-1].startswith("... (") and lines[-1].endswith(" more characters, the full text is sent)")


def test_unreadable_draft_says_so_instead_of_hiding_the_body(monkeypatch) -> None:
    _draft_api(monkeypatch, HttpError(Response({"status": "404"}), b'{"error":{"message":"Requested entity was not found."}}'))

    assert _send_draft() == (
        "Send Gmail draft r-123 as saved?\n"
        "The draft could not be read (not_found, HTTP 404), so its recipients, subject, attachments and body are "
        "unknown. Approving sends whatever the draft holds in Gmail.\n"
        "Body: (unknown: the draft could not be read)"
    )


def test_unreadable_draft_on_an_unexpected_error_names_no_details(monkeypatch) -> None:
    _draft_api(monkeypatch, RuntimeError("socket closed; Authorization: Bearer ya29.secret-token"))

    preview = _send_draft()

    assert "could not be read (internal_error)" in preview
    assert preview.endswith("Body: (unknown: the draft could not be read)")
    assert "ya29" not in preview and "Bearer" not in preview and "socket" not in preview


def test_disconnected_account_still_fails_with_its_reconnect_step(monkeypatch) -> None:
    _draft_api(
        monkeypatch,
        RuntimeError('{"error":"reauth_required","action":"Retry the request to start Google Workspace OAuth consent"}'),
    )

    with pytest.raises(RuntimeError, match="reauth_required"):
        _send_draft()


def test_send_body_limit_is_generous() -> None:
    # The old preview cut every body at 300 characters with its line breaks collapsed.
    assert BODY_PREVIEW_LIMIT >= 3000
    assert consent._BODY_INDENT.strip() == ""
