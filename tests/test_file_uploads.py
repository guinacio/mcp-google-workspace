from __future__ import annotations

import base64
from email import policy
from email.parser import BytesParser

import pytest
import anyio
from cryptography.fernet import Fernet
from fastmcp import Client
from fastmcp.server.providers.addressing import hash_tool
from pydantic import ValidationError
from jsonschema import Draft202012Validator

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
from mcp_google_workspace.server import workspace_mcp


class _Context:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


async def _file_picker_contract():
    async with Client(workspace_mcp) as client:
        tools = await client.list_tools()
        picker = next(tool for tool in tools if tool.name == "files_file_manager")
        assert picker.annotations is not None
        uri = picker.meta["ui"]["resourceUri"]
        resources = await client.list_resources()
        contents = await client.read_resource(uri)
        entry = await client.call_tool("files_file_manager", {})
        backend = await client.call_tool(
            f"{hash_tool('Workspace Files', 'store_files')}_store_files",
            {"files": []},
        )
    return {tool.name: tool for tool in tools}, uri, resources, contents, entry, backend


def test_file_picker_uses_the_standard_mcp_apps_contract() -> None:
    tools, uri, resources, contents, entry, backend = anyio.run(_file_picker_contract)

    picker = tools["files_file_manager"]
    assert picker.annotations is not None
    assert uri.startswith("ui://prefab/tool/")
    assert uri.endswith("/renderer.html")
    assert picker.meta["ui"]["visibility"] == ["model"]
    assert picker.meta["ui/resourceUri"] == uri

    resource = next(item for item in resources if str(item.uri) == uri)
    assert resource.mimeType == "text/html;profile=mcp-app"
    assert contents
    assert "prefab" in contents[0].text.lower()
    assert entry.is_error is False
    assert backend.is_error is False

    # Backend storage is callable from the app through its hashed address but
    # remains hidden from the model-visible catalog.
    assert "files_store_files" not in tools


async def _run_apps_self_test():
    async with Client(workspace_mcp) as client:
        return await client.call_tool(
            "get_mcp_apps_diagnostics", {"run_self_test": True}
        )


def test_file_picker_diagnostics_exercise_hidden_store_and_delete_callbacks() -> None:
    result = anyio.run(_run_apps_self_test)
    assert result.is_error is False
    assert result.structured_content["self_test"]["status"] == "passed"
    assert result.structured_content["self_test"]["delete_result"]["status"] == "deleted"


def test_file_tool_schema_describes_remote_upload_handles() -> None:
    tools, _, _, _, _, _ = anyio.run(_file_picker_contract)
    item_properties = tools["files_list_files"].outputSchema["properties"]["result"][
        "items"
    ]["properties"]
    assert {
        "upload_id",
        "display_name",
        "checksum_sha256",
        "expires_at",
        "remaining_quota_bytes",
    } <= set(item_properties)


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


def test_remote_upload_listing_is_paginated_with_account_level_quota(tmp_path) -> None:
    store = EncryptedUploadStore(
        tmp_path / "uploads.sqlite3",
        Fernet.generate_key().decode(),
        quota_bytes=100,
    )
    store.store(
        "alice",
        [
            {
                "name": f"{index}.txt",
                "type": "text/plain",
                "data": base64.b64encode(str(index).encode()).decode(),
            }
            for index in range(3)
        ],
        10,
    )
    all_stored = store.list("alice")
    list_tool = anyio.run(lambda: workspace_mcp.get_tool("files_list_files"))
    assert list_tool is not None
    assert not list(
        Draft202012Validator(list_tool.output_schema).iter_errors({"result": all_stored})
    )

    first = store.list("alice", limit=1, offset=0)
    second = store.list("alice", limit=1, offset=1)

    assert len(first) == len(second) == 1
    assert first[0]["upload_id"] != second[0]["upload_id"]
    assert first[0]["remaining_quota_bytes"] == 97
    assert second[0]["remaining_quota_bytes"] == 97


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
