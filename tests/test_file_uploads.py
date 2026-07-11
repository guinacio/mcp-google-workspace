from __future__ import annotations

import base64
from email import policy
from email.parser import BytesParser

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from mcp_google_workspace.auth.identity import Principal
from mcp_google_workspace.drive.schemas import UploadFileRequest
from mcp_google_workspace.file_uploads import (
    EncryptedUploadStore,
    WorkspaceFileUpload,
    require_local_filesystem,
)
from mcp_google_workspace.common.errors import RecoverableToolError
from mcp_google_workspace.gmail.mime_utils import build_email_message
from mcp_google_workspace.gmail.schemas import AttachmentInput
from mcp_google_workspace.gemini.schemas import AnalyzeAudioRequest


class _Context:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


def test_uploaded_files_are_isolated_by_principal_and_session(monkeypatch) -> None:
    picker = WorkspaceFileUpload()
    ctx = _Context("session-1")
    alice = Principal(issuer="https://issuer.example", subject="alice")
    bob = Principal(issuer="https://issuer.example", subject="bob")
    monkeypatch.setattr("mcp_google_workspace.file_uploads.current_principal", lambda: alice)

    picker.on_store(
        [
            {
                "name": "report.txt",
                "size": 5,
                "type": "text/plain",
                "data": base64.b64encode(b"hello").decode(),
            }
        ],
        ctx,  # type: ignore[arg-type]
    )
    uploaded = picker.get_file("report.txt", ctx)  # type: ignore[arg-type]
    assert uploaded.data == b"hello"
    assert uploaded.mime_type == "text/plain"

    monkeypatch.setattr("mcp_google_workspace.file_uploads.current_principal", lambda: bob)
    with pytest.raises(FileNotFoundError):
        picker.get_file("report.txt", ctx)  # type: ignore[arg-type]

    monkeypatch.setattr("mcp_google_workspace.file_uploads.current_principal", lambda: alice)
    with pytest.raises(FileNotFoundError):
        picker.get_file("report.txt", _Context("session-2"))  # type: ignore[arg-type]


def test_remote_principal_cannot_use_server_local_paths(monkeypatch) -> None:
    monkeypatch.setattr(
        "mcp_google_workspace.file_uploads.get_access_token",
        lambda: object(),
    )
    with pytest.raises(RecoverableToolError, match="Workspace Files picker"):
        require_local_filesystem("Drive upload")


def test_remote_upload_store_is_encrypted_shared_and_principal_scoped(tmp_path) -> None:
    database = tmp_path / "uploads.sqlite3"
    key = Fernet.generate_key().decode()
    first = EncryptedUploadStore(database, key, quota_bytes=10)
    stored = first.store(
        "alice",
        [{"name": "a.txt", "type": "text/plain", "data": base64.b64encode(b"secret").decode()}],
        10,
    )

    second = EncryptedUploadStore(database, key, quota_bytes=10)
    upload_id = stored[0]["upload_id"]
    assert upload_id.startswith("upl_")
    assert second.get("alice", upload_id).data == b"secret"
    with pytest.raises(RecoverableToolError):
        second.get("bob", upload_id)
    assert b"secret" not in database.read_bytes()
    assert second.delete("alice", upload_id)
    assert not second.delete("alice", upload_id)
    first.store(
        "alice",
        [{"name": "a.txt", "type": "text/plain", "data": base64.b64encode(b"secret").decode()}],
        10,
    )
    with pytest.raises(RecoverableToolError, match="quota"):
        second.store(
            "alice",
            [{"name": "b.txt", "type": "text/plain", "data": base64.b64encode(b"12345").decode()}],
            10,
        )


def test_file_source_schemas_require_exactly_one_source() -> None:
    assert AttachmentInput(uploaded_file="a.txt").uploaded_file == "a.txt"
    assert UploadFileRequest(uploaded_file="a.txt").uploaded_file == "a.txt"
    assert AnalyzeAudioRequest(uploaded_file="a.wav").uploaded_file == "a.wav"
    with pytest.raises(ValidationError):
        AttachmentInput()
    with pytest.raises(ValidationError):
        UploadFileRequest(local_path="a.txt", uploaded_file="a.txt")


def test_email_builder_accepts_picker_bytes_without_a_local_path() -> None:
    message = build_email_message(
        subject="Uploaded",
        to=["person@example.com"],
        cc=[],
        bcc=[],
        text_body="See attachment.",
        html_body=None,
        attachments=[
            {
                "data": b"picker bytes",
                "filename": "notes.txt",
                "mime_type": "text/plain",
            }
        ],
    )
    parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
    attachment = next(parsed.iter_attachments())
    assert attachment.get_filename() == "notes.txt"
    assert attachment.get_payload(decode=True) == b"picker bytes"
