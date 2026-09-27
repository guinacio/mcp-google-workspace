from __future__ import annotations

import base64
from email import policy
from hashlib import sha256
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
    LocalUploadStore,
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
    assert "ui/resourceUri" not in picker.meta  # W6: flat alias removed

    resource = next(item for item in resources if str(item.uri) == uri)
    assert resource.mime_type == "text/html;profile=mcp-app"
    assert contents
    assert "prefab" in contents[0].text.lower()
    assert entry.is_error is False
    assert backend.is_error is False

    # Backend storage is callable from the app through its hashed address. FastMCP 4
    # (per the MCP Apps spec, which puts visibility filtering on the host) now lists
    # app-only tools in tools/list, so the catalog must mark it app-only rather than
    # omit it. W2: previously asserted absence from tools/list.
    store = tools["files_store_files"]
    assert store.meta["ui"]["visibility"] == ["app"]
    assert store.annotations is not None and store.annotations.read_only_hint is False

    # The picker result carries the FastMCP 4 Prefab envelope, including
    # _meta.fastmcp.toolNames, and validates against the declared closed schema.
    Draft202012Validator.check_schema(picker.output_schema)
    validator = Draft202012Validator(picker.output_schema)
    payload = entry.structured_content
    assert set(payload) == {"$prefab", "view", "state", "_meta"}
    assert payload["_meta"]["fastmcp"]["toolNames"]
    assert list(validator.iter_errors(payload)) == []
    assert list(validator.iter_errors({**payload, "unexpected": {}}))
    assert list(
        validator.iter_errors({**payload, "_meta": {**payload["_meta"], "other": {}}})
    )


def test_store_files_callback_input_is_documented_and_bounded() -> None:
    tools, _, _, _, _, _ = anyio.run(_file_picker_contract)
    schema = tools["files_store_files"].input_schema
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    item = schema["properties"]["files"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"name", "size", "type", "data"}
    assert all(field.get("description") for field in item["properties"].values())
    assert item["properties"]["size"]["maximum"] == 25 * 1024 * 1024
    assert item["properties"]["data"]["maxLength"] >= 25 * 1024 * 1024 * 4 // 3
    valid = {"files": [{"name": "a.txt", "size": 1, "type": "text/plain", "data": "YQ=="}]}
    assert list(validator.iter_errors(valid)) == []
    assert list(validator.iter_errors({"files": [{**valid["files"][0], "extra": 1}]}))


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
    item_properties = tools["files_list_files"].output_schema["properties"]["result"][
        "items"
    ]["properties"]
    assert {
        "upload_id",
        "display_name",
        "checksum_sha256",
        "expires_at",
        "remaining_quota_bytes",
    } <= set(item_properties)


def test_uploaded_files_are_isolated_by_principal_not_by_session(monkeypatch) -> None:
    # W3: local uploads are scoped to the (trusted-local) principal and keyed by
    # opaque upload IDs; the transport session no longer partitions them.
    picker = WorkspaceFileUpload(local_store=LocalUploadStore())
    ctx = _Context("session-1")
    alice = Principal(issuer="https://issuer.example", subject="alice")
    bob = Principal(issuer="https://issuer.example", subject="bob")
    monkeypatch.setattr("mcp_google_workspace.file_uploads.current_principal", lambda: alice)

    stored = picker.on_store(
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
    upload_id = stored[0]["upload_id"]
    assert upload_id.startswith("upl_") and stored[0]["display_name"] == "report.txt"
    uploaded = picker.get_file(upload_id, ctx)  # type: ignore[arg-type]
    assert uploaded.data == b"hello"
    assert uploaded.mime_type == "text/plain"
    assert uploaded.checksum_sha256 == sha256(b"hello").hexdigest()
    # Another request on another (or no) transport session still sees it.
    assert picker.get_file(upload_id, _Context("session-2")).data == b"hello"  # type: ignore[arg-type]
    assert picker.get_file(upload_id).data == b"hello"
    # The original filename is display-only, not a handle.
    with pytest.raises(RecoverableToolError, match="not found or has expired"):
        picker.get_file("report.txt", ctx)  # type: ignore[arg-type]

    monkeypatch.setattr("mcp_google_workspace.file_uploads.current_principal", lambda: bob)
    with pytest.raises(RecoverableToolError, match="not found or has expired"):
        picker.get_file(upload_id, ctx)  # type: ignore[arg-type]
    assert picker.on_list(ctx) == []  # type: ignore[arg-type]
    assert picker._trusted_local_store().delete(bob.storage_key, upload_id) is False

    monkeypatch.setattr("mcp_google_workspace.file_uploads.current_principal", lambda: alice)
    assert picker.get_file(upload_id, ctx).data == b"hello"  # type: ignore[arg-type]


def test_local_upload_store_applies_ttl_quota_and_content_checks() -> None:
    now = [1_000.0]
    store = LocalUploadStore(ttl_seconds=60, quota_bytes=10, clock=lambda: now[0])

    def item(name: str, content: bytes, mime: str = "text/plain") -> dict[str, str]:
        return {"name": name, "type": mime, "data": base64.b64encode(content).decode()}

    first = store.store("alice", [item("a.txt", b"12345")], 10)
    upload_id = first[0]["upload_id"]
    assert first[0]["remaining_quota_bytes"] == 5
    assert first[0]["expires_at"] == 1_060
    with pytest.raises(RecoverableToolError, match="quota"):
        store.store("alice", [item("b.txt", b"123456")], 10)
    with pytest.raises(ValueError, match="per-file limit"):
        store.store("alice", [item("c.txt", b"x" * 11)], 10)
    with pytest.raises(ValueError, match="does not match declared type"):
        store.store("alice", [item("d.png", b"plain text", "image/png")], 10)
    with pytest.raises(ValueError, match="invalid encoded data"):
        store.store("alice", [{"name": "e.txt", "type": "text/plain", "data": "!!"}], 10)
    assert store.store("bob", [item("b.txt", b"0123456789")], 10)[0]["remaining_quota_bytes"] == 0

    page = store.list("alice", limit=1, offset=0)
    assert [entry["upload_id"] for entry in page] == [upload_id]
    assert store.list("alice", limit=1, offset=1) == []

    now[0] = 1_061.0
    assert store.list("alice") == []
    with pytest.raises(RecoverableToolError, match="expired"):
        store.get("alice", upload_id)
    assert store.store("alice", [item("f.txt", b"1234567890")], 10)


async def _local_upload_round_trips() -> tuple[str, list, dict, dict, list]:
    store_address = f"{hash_tool('Workspace Files', 'store_files')}_store_files"

    async def call(name: str, arguments: dict):
        # Each call is an independent MCP 2026-07-28 request on a new connection.
        async with Client(workspace_mcp) as client:
            assert client.protocol_version == "2026-07-28"
            return await client.call_tool(name, arguments)

    stored = await call(
        store_address,
        {
            "files": [
                {
                    "name": "persist.txt",
                    "size": 7,
                    "type": "text/plain",
                    "data": base64.b64encode(b"persist").decode(),
                }
            ]
        },
    )
    upload_id = next(
        entry["upload_id"]
        for entry in stored.structured_content["result"]
        if entry["display_name"] == "persist.txt"
    )
    listed = (await call("files_list_files", {})).structured_content["result"]
    read = (await call("files_read_file", {"name": upload_id})).structured_content["result"]
    deleted = (await call("files_delete_file", {"name": upload_id})).structured_content
    after = (await call("files_list_files", {})).structured_content["result"]
    return upload_id, listed, read, deleted, after


def test_local_uploads_persist_across_independent_modern_requests() -> None:
    upload_id, listed, read, deleted, after = anyio.run(_local_upload_round_trips)
    assert upload_id in {entry["upload_id"] for entry in listed}
    assert read["content"] == "persist" and read["name"] == "persist.txt"
    assert deleted == {"status": "deleted", "name": upload_id}
    assert upload_id not in {entry["upload_id"] for entry in after}


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
