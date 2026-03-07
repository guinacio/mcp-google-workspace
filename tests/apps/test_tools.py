from __future__ import annotations

import base64
from datetime import date

from mcp_google_workspace.apps.schemas import DashboardState
from mcp_google_workspace.apps.tools import _compute_window, _fetch_email_attachment


def test_compute_window_for_week_with_weekend_uses_sunday_start() -> None:
    state = DashboardState(
        session_id="apps-tools-test",
        view="week",
        anchor_date=date(2026, 3, 4),
        timezone="UTC",
        include_weekend=True,
    )

    time_min, time_max = _compute_window(state)

    assert time_min == "2026-03-01T00:00:00+00:00"
    assert time_max == "2026-03-08T00:00:00+00:00"


def test_compute_window_for_weekday_only_uses_monday_start() -> None:
    state = DashboardState(
        session_id="apps-tools-test",
        view="week",
        anchor_date=date(2026, 3, 4),
        timezone="UTC",
        include_weekend=False,
    )

    time_min, time_max = _compute_window(state)

    assert time_min == "2026-03-02T00:00:00+00:00"
    assert time_max == "2026-03-07T00:00:00+00:00"


def test_fetch_email_attachment_falls_back_to_safe_filename(monkeypatch) -> None:
    attachment_bytes = b"pdf-bytes"
    gmail_encoded = base64.urlsafe_b64encode(attachment_bytes).decode("ascii")

    class _Execute:
        def __init__(self, payload):
            self._payload = payload

        def execute(self):
            return self._payload

    class _AttachmentsApi:
        def get(self, *, userId: str, messageId: str, id: str):
            assert userId == "me"
            assert messageId == "msg-1"
            assert id == "att-1"
            return _Execute({"data": gmail_encoded})

    class _MessagesApi:
        def get(self, *, userId: str, id: str, format: str):
            assert userId == "me"
            assert id == "msg-1"
            assert format == "full"
            return _Execute(
                {
                    "payload": {
                        "parts": [
                            {
                                "mimeType": "application/pdf",
                                "filename": "",
                                "body": {"attachmentId": "att-1", "size": len(attachment_bytes)},
                            }
                        ]
                    }
                }
            )

        def attachments(self):
            return _AttachmentsApi()

    class _UsersApi:
        def messages(self):
            return _MessagesApi()

    class _Service:
        def users(self):
            return _UsersApi()

    monkeypatch.setattr(
        "mcp_google_workspace.apps.tools.build_gmail_service",
        lambda: _Service(),
    )

    payload = _fetch_email_attachment("msg-1", "att-1")

    assert payload["filename"] == "attachment.pdf"
    assert payload["mime_type"] == "application/pdf"
    assert payload["blob_base64"] == base64.b64encode(attachment_bytes).decode("ascii")
