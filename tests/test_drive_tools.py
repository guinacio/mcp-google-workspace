from __future__ import annotations

import re

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import anyio
import pytest
from pydantic import ValidationError

from mcp_google_workspace.drive.server import drive_mcp
from mcp_google_workspace.drive.presentation import file_envelope
from mcp_google_workspace.drive.tools import drives as drive_drives
from mcp_google_workspace.drive.tools import files as drive_files
from mcp_google_workspace.drive.tools import permissions as drive_permissions

from consent_testing import ConsentAnswers, answer_consent, gated_tools


async def _list_drive_tools() -> dict[str, Any]:
    tools = await drive_mcp.list_tools(run_middleware=False)
    return {t.name: t for t in tools}


def test_drive_server_registers_the_consolidated_tools():
    tools = anyio.run(_list_drive_tools)
    assert set(tools) == {
        "list_files",
        "get_file",
        "create_folder",
        "upload_file",
        "update_file_metadata",
        "move_file",
        "copy_file",
        "delete_file",
        "download_file",
        "read_file",
        "list_permissions",
        "share_file",
        "unshare_file",
        "list_drives",
        "update_drive",
    }
    list_files = tools["list_files"].parameters["properties"]
    assert "corpora" not in list_files and "spaces" not in list_files
    assert {"query", "folder_id", "kind", "modified_after", "full_text", "order_by", "drive_id"} <= set(list_files)
    assert tools["upload_file"].task_config.supports_tasks()
    assert tools["download_file"].task_config.supports_tasks()
    assert tools["share_file"].parameters["required"] == ["file_id"]
    assert tools["update_drive"].parameters["required"] == ["drive_id", "hidden"]


def test_file_envelope_surfaces_owner_state_and_capabilities():
    result = file_envelope(
        {
            "id": "file-1", "name": "Plan", "mimeType": "application/vnd.google-apps.document",
            "owners": [{"displayName": "Ada", "emailAddress": "ada@example.com"}],
            "capabilities": {"canEdit": True, "canDownload": True}, "shared": True,
        },
        account_timezone="America/Sao_Paulo",
    )
    assert result["kind"] == "document"
    assert result["owners"][0]["email"] == "ada@example.com"
    assert result["can_edit"] is True


def test_file_envelope_reports_unfetched_fields_as_null():
    """A field absent from the Drive payload (not requested, or not applicable)
    must be null, never a made-up ``false`` or ``[]``."""
    result = file_envelope({"id": "file-1", "name": "Plan"}, account_timezone="UTC")

    for field in ("shared", "starred", "trashed", "owned_by_me", "owners", "parent_ids", "last_modified_by", "can_edit", "can_download", "mime_type"):
        assert result[field] is None, field
    assert result["kind"] == "file"


def test_file_envelope_keeps_fetched_false_and_empty_values():
    result = file_envelope(
        {
            "id": "file-1", "name": "Plan", "shared": False, "starred": False, "trashed": False, "ownedByMe": True,
            "owners": [], "parents": ["folder-1"],
            "lastModifyingUser": {"displayName": "Ada", "emailAddress": "ada@example.com"},
        },
        account_timezone="UTC",
    )

    assert result["shared"] is False
    assert result["starred"] is False
    assert result["trashed"] is False
    assert result["owned_by_me"] is True
    assert result["owners"] == []
    assert result["parent_ids"] == ["folder-1"]
    assert result["last_modified_by"] == {"name": "Ada", "email": "ada@example.com"}


# ---------------------------------------------------------------------------
# Fakes: the raw tool functions over a recording Drive service
# ---------------------------------------------------------------------------


class _ToolCapture:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., Any]] = {}

    def tool(self, *, name: str, task: bool = False):
        def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
            self.tools[name] = function
            return function

        return decorator


class _Request:
    def __init__(self, result: Any) -> None:
        self.result = result

    def execute(self) -> Any:
        return self.result


class _Resource:
    def __init__(self, service: "_FakeDrive", name: str) -> None:
        self._service = service
        self._name = name

    def __getattr__(self, method: str) -> Callable[..., _Request]:
        def call(**kwargs: Any) -> _Request:
            key = f"{self._name}.{method}"
            self._service.calls.append((key, kwargs))
            response = self._service.responses.get(key, {})
            return _Request(response(kwargs) if callable(response) else response)

        return call


class _FakeDrive:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: dict[str, Any] = {}

    def files(self) -> _Resource:
        return _Resource(self, "files")

    def permissions(self) -> _Resource:
        return _Resource(self, "permissions")

    def drives(self) -> _Resource:
        return _Resource(self, "drives")

    def called(self, key: str) -> list[dict[str, Any]]:
        return [kwargs for name, kwargs in self.calls if name == key]


async def _execute(request: _Request) -> Any:
    return request.execute()


async def _timezone() -> str:
    return "America/Sao_Paulo"


@pytest.fixture
def drive(monkeypatch: pytest.MonkeyPatch) -> _FakeDrive:
    fake = _FakeDrive()
    for module in (drive_files, drive_permissions, drive_drives):
        monkeypatch.setattr(module, "drive_service", lambda: fake)
        monkeypatch.setattr(module, "execute_google_request", _execute)
    monkeypatch.setattr(drive_files, "resolve_user_timezone", _timezone)
    return fake


def _tools(module: Any) -> dict[str, Callable[..., Any]]:
    """The module's tools behind the consent gate every registered tool gets."""
    capture = _ToolCapture()
    module.register(capture)
    return gated_tools(capture.tools, "drive")


def _run(function: Callable[..., Any], **arguments: Any) -> Any:
    async def call() -> Any:
        return await function(**arguments, ctx=None)

    return anyio.run(call)


def _confirm(monkeypatch: pytest.MonkeyPatch, answer: bool) -> ConsentAnswers:
    return answer_consent(monkeypatch, answer)


# ---------------------------------------------------------------------------
# drive_list_files
# ---------------------------------------------------------------------------


def test_list_files_ands_the_task_filters_with_the_raw_query_across_all_drives(drive: _FakeDrive) -> None:
    drive.responses["files.list"] = {"files": [{"id": "f1", "name": "Budget", "mimeType": "application/vnd.google-apps.spreadsheet"}]}
    result = _run(
        _tools(drive_files)["list_files"],
        query="starred = true or name contains 'x'",
        folder_id="folder-1",
        kind="spreadsheet",
        modified_after="2026-11-01",
        full_text="Q4 'final'",
    )

    (call,) = drive.called("files.list")
    assert call["q"] == (
        "(starred = true or name contains 'x') and 'folder-1' in parents"
        " and mimeType = 'application/vnd.google-apps.spreadsheet'"
        " and modifiedTime > '2026-11-01T03:00:00'"
        " and fullText contains 'Q4 \\'final\\''"
    )
    assert call["corpora"] == "allDrives" and call["driveId"] is None
    assert call["includeItemsFromAllDrives"] is True and call["supportsAllDrives"] is True
    assert "spaces" not in call
    assert result["count"] == 1 and result["files"][0]["kind"] == "spreadsheet"
    assert result["next_page_token"] is None and result["account_timezone"] == "America/Sao_Paulo"


@pytest.mark.parametrize(
    ("arguments", "expected_q", "expected_corpora"),
    [
        ({}, None, "allDrives"),
        ({"query": "trashed = false"}, "trashed = false", "allDrives"),
        ({"kind": "folder"}, "mimeType = 'application/vnd.google-apps.folder'", "allDrives"),
        ({"kind": "image", "drive_id": "drive-9"}, "mimeType contains 'image/'", "drive"),
        ({"modified_after": "2026-11-01T09:00:00"}, "modifiedTime > '2026-11-01T12:00:00'", "allDrives"),
        ({"modified_after": "2026-11-01T09:00:00+01:00"}, "modifiedTime > '2026-11-01T08:00:00'", "allDrives"),
        ({"modified_after": "2026-11-01T09:00:00Z"}, "modifiedTime > '2026-11-01T09:00:00'", "allDrives"),
    ],
)
def test_list_files_derives_q_and_corpora(
    drive: _FakeDrive, arguments: dict[str, Any], expected_q: str | None, expected_corpora: str
) -> None:
    _run(_tools(drive_files)["list_files"], **arguments)
    (call,) = drive.called("files.list")
    assert call["q"] == expected_q
    assert call["corpora"] == expected_corpora
    assert call["driveId"] == arguments.get("drive_id")


@pytest.mark.parametrize(
    "arguments",
    [{"modified_after": "last tuesday"}, {"full_text": "  "}, {"folder_id": ""}, {"kind": "pdf"}],
)
def test_list_files_rejects_bad_filters_before_calling_drive(drive: _FakeDrive, arguments: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _run(_tools(drive_files)["list_files"], **arguments)
    assert drive.called("files.list") == []


# ---------------------------------------------------------------------------
# drive_get_file (absorbs get_file_content_capabilities)
# ---------------------------------------------------------------------------


def test_get_file_reports_content_capabilities(drive: _FakeDrive) -> None:
    payloads = {
        "doc": {
            "id": "doc", "name": "Plan", "mimeType": "application/vnd.google-apps.document",
            "capabilities": {"canDownload": True, "canEdit": False},
            "exportLinks": {"text/plain": "https://x/1", "application/pdf": "https://x/2"},
        },
        "pdf": {"id": "pdf", "name": "Invoice.pdf", "mimeType": "application/pdf", "capabilities": {"canDownload": False}},
    }
    drive.responses["files.get"] = lambda kwargs: payloads[kwargs["fileId"]]
    get_file = _tools(drive_files)["get_file"]

    doc = _run(get_file, file_id="doc")["file"]
    assert doc["is_google_native"] is True
    assert doc["can_download"] is True
    assert doc["export_formats"] == ["application/pdf", "text/plain"]
    assert "export_links" not in doc and "web_content_link" not in doc
    assert "exportLinks" in drive.called("files.get")[0]["fields"]

    pdf = _run(get_file, file_id="pdf")["file"]
    assert pdf["is_google_native"] is False
    assert pdf["can_download"] is False
    assert pdf["export_formats"] == []


# ---------------------------------------------------------------------------
# drive_upload_file (absorbs update_file_content and create_file_metadata)
# ---------------------------------------------------------------------------


@pytest.fixture
def upload(monkeypatch: pytest.MonkeyPatch, drive: _FakeDrive) -> list[tuple[str, Any]]:
    media: list[tuple[str, Any]] = []
    picked = SimpleNamespace(name="report.pdf", data=b"%PDF", mime_type="application/pdf")
    monkeypatch.setattr(
        drive_files, "workspace_file_upload", SimpleNamespace(get_file=lambda upload_id, ctx: picked)
    )
    monkeypatch.setattr(
        drive_files,
        "media_bytes_upload",
        lambda data, mime_type, resumable: media.append(("bytes", (data, mime_type))) or "MEDIA",
    )
    monkeypatch.setattr(
        drive_files,
        "media_file_upload",
        lambda path, mime_type, resumable: media.append(("file", (path, mime_type))) or "MEDIA",
    )
    drive.responses["files.create"] = lambda kwargs: {"id": "new", "name": kwargs["body"]["name"], "mimeType": "application/pdf"}
    drive.responses["files.update"] = lambda kwargs: {"id": kwargs["fileId"], "name": "report.pdf", "mimeType": "application/pdf"}
    return media


def _upload(**arguments: Any) -> Any:
    return _run(_tools(drive_files)["upload_file"], resumable=False, **arguments)


def test_upload_creates_a_new_file_with_parent_and_description(drive: _FakeDrive, upload: list[Any]) -> None:
    drive.responses["files.list"] = {"files": []}
    result = _upload(uploaded_file="upl_1", parent_ids=["folder-1"], description="Q4")

    assert result["status"] == "ok" and result["mode"] == "create"
    assert result["file"]["id"] == "new"
    (create,) = drive.called("files.create")
    assert create["body"] == {"name": "report.pdf", "description": "Q4", "parents": ["folder-1"]}
    assert create["media_body"] == "MEDIA"
    assert upload == [("bytes", (b"%PDF", "application/pdf"))]
    assert "'folder-1' in parents" in drive.called("files.list")[0]["q"]


def test_upload_renames_on_a_name_clash_by_default(drive: _FakeDrive, upload: list[Any]) -> None:
    drive.responses["files.list"] = {"files": [{"id": "old", "name": "report.pdf"}]}
    result = _upload(uploaded_file="upl_1")

    assert result["mode"] == "create"
    name = drive.called("files.create")[0]["body"]["name"]
    assert name.startswith("report (upload ") and name.endswith(").pdf")


def test_upload_overwrites_the_same_named_file(drive: _FakeDrive, upload: list[Any]) -> None:
    drive.responses["files.list"] = {"files": [{"id": "old", "name": "report.pdf"}]}
    result = _upload(uploaded_file="upl_1", if_exists="overwrite")

    assert result == {"status": "ok", "mode": "overwrite", "file": result["file"]}
    assert result["file"]["id"] == "old"
    (update,) = drive.called("files.update")
    assert update["fileId"] == "old" and update["body"] == {"name": "report.pdf"} and update["media_body"] == "MEDIA"
    assert drive.called("files.create") == []


def test_overwrite_without_a_parent_never_replaces_a_same_named_file_elsewhere(
    drive: _FakeDrive, upload: list[Any]
) -> None:
    """Without parent_ids the upload lands in My Drive's root, so only the root is searched."""
    elsewhere = {"id": "unrelated", "name": "report.pdf", "parents": ["someone-elses-folder"]}

    def list_files(kwargs: dict[str, Any]) -> dict[str, Any]:
        # Drive's query semantics for this fake's one file: a parent clause must match.
        clauses = re.findall(r"'([^']+)' in parents", kwargs["q"])
        return {"files": [elsewhere] if all(c in elsewhere["parents"] for c in clauses) else []}

    drive.responses["files.list"] = list_files
    result = _upload(uploaded_file="upl_1", if_exists="overwrite")

    assert "'root' in parents" in drive.called("files.list")[0]["q"]
    assert drive.called("files.update") == []
    assert result["mode"] == "create" and result["file"]["id"] == "new"


def test_upload_skips_when_the_same_named_file_exists(drive: _FakeDrive, upload: list[Any]) -> None:
    drive.responses["files.list"] = {"files": [{"id": "old", "name": "report.pdf", "mimeType": "application/pdf"}]}
    result = _upload(uploaded_file="upl_1", if_exists="skip")

    assert result["status"] == "ok" and result["mode"] == "skipped" and result["reason"] == "file_exists"
    assert result["file"]["id"] == "old"
    assert drive.called("files.create") == [] and drive.called("files.update") == []


def test_upload_with_file_id_replaces_that_files_content(
    drive: _FakeDrive, upload: list[Any], tmp_path: Any
) -> None:
    source = tmp_path / "v2.pdf"
    source.write_bytes(b"%PDF-2")
    result = _upload(file_id="file-7", local_path=str(source), keep_revision_forever=True)

    assert result["status"] == "ok" and result["mode"] == "replace"
    assert result["file"]["id"] == "file-7"
    (update,) = drive.called("files.update")
    assert update["fileId"] == "file-7"
    assert update["keepRevisionForever"] is True
    assert update["media_body"] == "MEDIA" and "body" not in update
    assert upload == [("file", (str(source), None))]
    assert drive.called("files.list") == []  # no name check: the target is already known


def test_upload_without_content_creates_an_empty_file_from_metadata(drive: _FakeDrive, upload: list[Any]) -> None:
    result = _upload(
        name="Notes", mime_type="application/vnd.google-apps.document", parent_ids=["folder-1"], description="Draft"
    )

    assert result["status"] == "ok" and result["mode"] == "metadata_only"
    (create,) = drive.called("files.create")
    assert create["body"] == {
        "name": "Notes",
        "mimeType": "application/vnd.google-apps.document",
        "parents": ["folder-1"],
        "description": "Draft",
    }
    assert "media_body" not in create
    assert upload == [] and drive.called("files.list") == []


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"file_id": "f", "uploaded_file": "upl_1", "name": "x"}, "name cannot be combined with file_id"),
        ({"file_id": "f", "uploaded_file": "upl_1", "parent_ids": ["p"]}, "parent_ids cannot be combined"),
        ({"file_id": "f", "uploaded_file": "upl_1", "if_exists": "skip"}, "if_exists cannot be combined"),
        ({"file_id": "f"}, "give local_path or uploaded_file"),
        ({"uploaded_file": "upl_1", "keep_revision_forever": True}, "keep_revision_forever applies only"),
        ({"mime_type": "application/vnd.google-apps.document"}, "name is required"),
        ({"name": "x", "if_exists": "overwrite"}, "if_exists applies only to content uploads"),
        ({"uploaded_file": "upl_1", "local_path": "/tmp/a"}, "at most one of local_path or uploaded_file"),
    ],
)
def test_upload_rejects_invalid_combinations(
    drive: _FakeDrive, upload: list[Any], arguments: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _upload(**arguments)
    assert drive.calls == []


# ---------------------------------------------------------------------------
# drive_download_file (absorbs export_google_file)
# ---------------------------------------------------------------------------


@pytest.fixture
def saved(monkeypatch: pytest.MonkeyPatch, drive: _FakeDrive) -> list[dict[str, Any]]:
    writes: list[dict[str, Any]] = []

    async def stream(request: Any, path: Any, *, overwrite: bool, progress_label: str, ctx: Any) -> int:
        writes.append({"path": str(path), "overwrite": overwrite, "label": progress_label})
        return 42

    monkeypatch.setattr(drive_files, "stream_google_download", stream)
    types = {
        "pdf": "application/pdf",
        "doc": "application/vnd.google-apps.document",
        "sheet": "application/vnd.google-apps.spreadsheet",
        "drawing": "application/vnd.google-apps.drawing",
        "form": "application/vnd.google-apps.form",
        "folder": "application/vnd.google-apps.folder",
    }
    drive.responses["files.get"] = lambda kwargs: {"id": kwargs["fileId"], "name": kwargs["fileId"].title(), "mimeType": types[kwargs["fileId"]]}
    return writes


def _download(**arguments: Any) -> Any:
    return _run(_tools(drive_files)["download_file"], **arguments)


def test_download_saves_a_stored_file_as_is(drive: _FakeDrive, saved: list[Any]) -> None:
    result = _download(file_id="pdf", output_path="/tmp/out.pdf", overwrite=True, acknowledge_abuse=True, export_mime_type="text/plain")

    assert result == {
        "status": "ok", "file_id": "pdf", "name": "Pdf", "mode": "download", "mime_type": "application/pdf",
        "export_mime_type": None, "saved_to": "/tmp/out.pdf", "bytes_written": 42,
    }
    (media,) = drive.called("files.get_media")
    assert media == {"fileId": "pdf", "acknowledgeAbuse": True, "supportsAllDrives": True}
    assert drive.called("files.export_media") == []
    assert saved == [{"path": "/tmp/out.pdf", "overwrite": True, "label": "Downloading Drive file"}]


@pytest.mark.parametrize(
    ("file_id", "export_mime_type", "expected"),
    [
        ("doc", None, "application/pdf"),
        ("sheet", None, "application/pdf"),
        ("drawing", None, "image/png"),
        ("sheet", "text/csv", "text/csv"),
        ("form", "application/zip", "application/zip"),
    ],
)
def test_download_exports_google_native_files(
    drive: _FakeDrive, saved: list[Any], file_id: str, export_mime_type: str | None, expected: str
) -> None:
    result = _download(file_id=file_id, output_path="/tmp/out", export_mime_type=export_mime_type)

    assert result["mode"] == "export" and result["export_mime_type"] == expected
    assert result["mime_type"].startswith("application/vnd.google-apps.")
    assert drive.called("files.export_media") == [{"fileId": file_id, "mimeType": expected}]
    assert drive.called("files.get_media") == []
    assert saved[0]["label"] == "Exporting Google-native file"


@pytest.mark.parametrize(("file_id", "message"), [("folder", "is a folder"), ("form", "pass export_mime_type")])
def test_download_refuses_what_it_cannot_save(drive: _FakeDrive, saved: list[Any], file_id: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _download(file_id=file_id, output_path="/tmp/out")
    assert saved == []


# ---------------------------------------------------------------------------
# drive_share_file (create_permission + update_permission)
# ---------------------------------------------------------------------------


def test_share_file_creates_a_grant_after_the_create_permission_confirmation(
    monkeypatch: pytest.MonkeyPatch, drive: _FakeDrive
) -> None:
    confirmations = _confirm(monkeypatch, True)
    drive.responses["permissions.create"] = {"id": "perm-1", "type": "user", "role": "writer"}
    result = _run(
        _tools(drive_permissions)["share_file"],
        file_id="file-1",
        type="user",
        role="writer",
        email_address="ada@example.com",
        expiration_time="2026-12-31T00:00:00Z",
        send_notification_email=False,
        email_message="Here you go",
    )

    assert confirmations.asked == [
        (
            "create_permission",
            "Share Drive file file-1 with user ada@example.com as writer? "
            "Access expires 2026-12-31T00:00:00Z. No notification email.",
        )
    ]
    assert result == {"status": "ok", "mode": "created", "permission": {"id": "perm-1", "type": "user", "role": "writer"}}
    (create,) = drive.called("permissions.create")
    assert create["body"] == {"type": "user", "role": "writer", "emailAddress": "ada@example.com", "expirationTime": "2026-12-31T00:00:00Z"}
    assert create["sendNotificationEmail"] is False and create["emailMessage"] == "Here you go"
    assert create["useDomainAdminAccess"] is False and create["supportsAllDrives"] is True


def test_share_file_create_declined_changes_nothing(monkeypatch: pytest.MonkeyPatch, drive: _FakeDrive) -> None:
    confirmations = _confirm(monkeypatch, False)
    result = _run(
        _tools(drive_permissions)["share_file"],
        file_id="file-1",
        type="anyone",
        role="reader",
        allow_file_discovery=False,
    )

    assert confirmations.asked == [
        ("create_permission", "Share Drive file file-1 with anyone with the link (no sign-in needed) as reader?")
    ]
    assert result == {
        "status": "cancelled",
        "action": "create_permission",
        "reason": "The user declined; nothing was changed.",
    }
    assert drive.calls == []


def test_share_file_with_permission_id_updates_after_the_update_permission_confirmation(
    monkeypatch: pytest.MonkeyPatch, drive: _FakeDrive
) -> None:
    confirmations = _confirm(monkeypatch, True)
    drive.responses["permissions.update"] = {"id": "perm-1", "role": "commenter"}
    result = _run(
        _tools(drive_permissions)["share_file"],
        file_id="file-1",
        permission_id="perm-1",
        role="commenter",
        allow_file_discovery=True,
        expiration_time="2026-12-01T00:00:00Z",
        remove_expiration=True,
    )

    assert confirmations.asked == [
        (
            "update_permission",
            "Change permission perm-1 on Drive file file-1: role commenter, expires 2026-12-01T00:00:00Z, "
            "no expiration, discoverable True?",
        )
    ]
    assert result == {"status": "ok", "mode": "updated", "permission": {"id": "perm-1", "role": "commenter"}}
    (update,) = drive.called("permissions.update")
    assert update["permissionId"] == "perm-1"
    assert update["body"] == {"role": "commenter", "allowFileDiscovery": True, "expirationTime": None}


def test_share_file_update_transfers_ownership_and_cancels(monkeypatch: pytest.MonkeyPatch, drive: _FakeDrive) -> None:
    confirmations = _confirm(monkeypatch, False)
    result = _run(
        _tools(drive_permissions)["share_file"],
        file_id="file-1",
        permission_id="perm-1",
        role="owner",
        transfer_ownership=True,
    )

    assert confirmations.asked == [
        (
            "update_permission",
            "Change permission perm-1 on Drive file file-1: role owner? This transfers ownership of the file.",
        )
    ]
    assert result == {
        "status": "cancelled",
        "action": "update_permission",
        "reason": "The user declined; nothing was changed.",
    }
    assert drive.calls == []


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"permission_id": "perm-1", "type": "user"}, "type cannot be given with permission_id"),
        ({"permission_id": "perm-1", "email_address": "a@example.com"}, "email_address cannot be given"),
        ({"permission_id": "perm-1", "domain": "example.com"}, "domain cannot be given"),
        ({"permission_id": "perm-1", "email_message": "hi"}, "email_message cannot be given"),
        ({"permission_id": "perm-1", "send_notification_email": False}, "send_notification_email cannot be given"),
        ({"type": "user", "role": "reader", "remove_expiration": True}, "remove_expiration applies only with permission_id"),
        ({"role": "reader"}, "needs type to create"),
        ({"type": "user"}, "needs role to create"),
        ({}, "needs type and role"),
    ],
)
def test_share_file_rejects_mode_mismatches_without_changing_anything(
    monkeypatch: pytest.MonkeyPatch, drive: _FakeDrive, arguments: dict[str, Any], message: str
) -> None:
    # Every share asks first (the consent gate runs before the tool body), so
    # the mismatch is reported after the question; nothing reaches Google.
    confirmations = _confirm(monkeypatch, True)
    with pytest.raises(ValueError, match=message):
        _run(_tools(drive_permissions)["share_file"], file_id="file-1", **arguments)
    assert len(confirmations.asked) == 1 and drive.calls == []


# ---------------------------------------------------------------------------
# drive_unshare_file, drive_list_permissions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("answer", [True, False])
def test_unshare_file_confirms_with_delete_permission(
    monkeypatch: pytest.MonkeyPatch, drive: _FakeDrive, answer: bool
) -> None:
    confirmations = _confirm(monkeypatch, answer)
    result = _run(_tools(drive_permissions)["unshare_file"], file_id="file-1", permission_id="perm-1")

    assert confirmations.asked == [("delete_permission", "Remove permission perm-1 from Drive file file-1?")]
    if answer:
        assert result == {"status": "ok", "file_id": "file-1", "permission_id": "perm-1"}
    else:
        assert result == {
            "status": "cancelled",
            "action": "delete_permission",
            "reason": "The user declined; nothing was changed.",
        }
    expected = [{"fileId": "file-1", "permissionId": "perm-1", "supportsAllDrives": True, "useDomainAdminAccess": False}]
    assert drive.called("permissions.delete") == (expected if answer else [])


def test_list_permissions_lists_or_returns_one_permission(drive: _FakeDrive) -> None:
    drive.responses["permissions.list"] = {"permissions": [{"id": "a"}, {"id": "b"}], "nextPageToken": "next"}
    drive.responses["permissions.get"] = {"id": "b", "role": "reader"}
    list_permissions = _tools(drive_permissions)["list_permissions"]

    page = _run(list_permissions, file_id="file-1", page_size=2)
    assert page == {"permissions": [{"id": "a"}, {"id": "b"}], "next_page_token": "next", "count": 2}
    assert drive.called("permissions.list")[0]["pageSize"] == 2

    one = _run(list_permissions, file_id="file-1", permission_id="b", page_token="ignored")
    assert one == {"permissions": [{"id": "b", "role": "reader"}], "next_page_token": None, "count": 1}
    (get,) = drive.called("permissions.get")
    assert get["fileId"] == "file-1" and get["permissionId"] == "b" and "expirationTime" in get["fields"]
    assert len(drive.called("permissions.list")) == 1


# ---------------------------------------------------------------------------
# drive_list_drives, drive_update_drive
# ---------------------------------------------------------------------------


def test_list_drives_lists_or_returns_one_drive(drive: _FakeDrive) -> None:
    drive.responses["drives.list"] = {"drives": [{"id": "d1"}], "nextPageToken": None}
    drive.responses["drives.get"] = {"id": "d2", "name": "Finance", "orgUnitId": "ou-1"}
    list_drives = _tools(drive_drives)["list_drives"]

    page = _run(list_drives, query="hidden = true")
    assert page == {"drives": [{"id": "d1"}], "next_page_token": None, "count": 1}
    assert drive.called("drives.list")[0]["q"] == "hidden = true"

    one = _run(list_drives, drive_id="d2")
    assert one == {"drives": [{"id": "d2", "name": "Finance", "orgUnitId": "ou-1"}], "next_page_token": None, "count": 1}
    (get,) = drive.called("drives.get")
    assert get["driveId"] == "d2" and "orgUnitId" in get["fields"] and get["useDomainAdminAccess"] is False


@pytest.mark.parametrize(("hidden", "method"), [(True, "drives.hide"), (False, "drives.unhide")])
def test_update_drive_hides_and_unhides(drive: _FakeDrive, hidden: bool, method: str) -> None:
    drive.responses[method] = {"id": "d1", "hidden": hidden}
    result = _run(_tools(drive_drives)["update_drive"], drive_id="d1", hidden=hidden)

    assert result == {"status": "ok", "drive": {"id": "d1", "hidden": hidden}}
    assert drive.calls == [(method, {"driveId": "d1"})]
