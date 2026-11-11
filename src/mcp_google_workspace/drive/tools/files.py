"""Drive tools for files/folders/content operations."""

from __future__ import annotations

import logging

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from pydantic import Field

import pytz
from fastmcp import Context, FastMCP

from ...auth.google_auth import LazyGoogleRequest, materialize_google_request
from ...common.async_ops import execute_google_request, run_blocking
from ...common.repeat_safety import track_provider_call
from ...common.timezone import resolve_user_timezone, user_now
from ...file_uploads import require_local_filesystem, workspace_file_upload
from ...common.downloads import stream_google_download
from ..client import GOOGLE_EXPORT_MIME_DEFAULTS, drive_service, media_bytes_upload, media_file_upload
from ..schemas import (
    CopyFileRequest,
    CreateFolderRequest,
    DeleteFileRequest,
    DownloadFileRequest,
    FileKind,
    GetFileRequest,
    ListFilesRequest,
    MoveFileRequest,
    UpdateFileMetadataRequest,
    UploadFileRequest,
)
from ..presentation import file_envelope, file_summary, files_page
from ...common.errors import RecoverableToolError
from ...common.resources import clean_web_url
from ...common.text_paging import paged_text
from ...common.timezone import in_account_timezone
from ...gmail.presentation import html_to_text, remove_invisible_characters

LOGGER = logging.getLogger(__name__)


# Shared drives are always in scope (supportsAllDrives=true on every call), and
# each call requests the fields its normalizer reads; neither is a tool input.
_LIST_FILES_FIELDS = "nextPageToken,files(id,name,mimeType,parents,modifiedTime,createdTime,size,driveId,webViewLink,description,shared,starred,trashed,ownedByMe,owners(displayName,emailAddress),lastModifyingUser(displayName,emailAddress),capabilities(canEdit,canDownload))"
_FILE_FIELDS = "id,name,mimeType,parents,modifiedTime,createdTime,size,driveId,webViewLink,description,shared,starred,trashed,ownedByMe,owners(displayName,emailAddress),lastModifyingUser(displayName,emailAddress),capabilities(canEdit,canDownload),exportLinks"
_CREATED_FILE_FIELDS = "id,name,mimeType,parents,driveId,webViewLink"
_UPLOADED_FILE_FIELDS = "id,name,mimeType,size,parents,driveId,webViewLink"
_UPDATED_METADATA_FIELDS = "id,name,mimeType,parents,modifiedTime,size,driveId,webViewLink"
_UPDATED_CONTENT_FIELDS = "id,name,mimeType,size,parents,driveId,webViewLink,modifiedTime"
_MOVED_FILE_FIELDS = "id,name,parents,driveId,webViewLink"
_DOWNLOAD_META_FIELDS = "id,name,mimeType"

_GOOGLE_NATIVE_PREFIX = "application/vnd.google-apps."
_FOLDER_MIME = "application/vnd.google-apps.folder"
# drive_list_files kind filter -> Drive q condition, matching presentation.file_kind.
_KIND_CONDITIONS: dict[str, str] = {
    "folder": f"mimeType = '{_FOLDER_MIME}'",
    "document": "mimeType = 'application/vnd.google-apps.document'",
    "spreadsheet": "mimeType = 'application/vnd.google-apps.spreadsheet'",
    "presentation": "mimeType = 'application/vnd.google-apps.presentation'",
    "form": "mimeType = 'application/vnd.google-apps.form'",
    "image": "mimeType contains 'image/'",
    "video": "mimeType contains 'video/'",
    "audio": "mimeType contains 'audio/'",
}


_READ_FILE_FIELDS = "id,name,mimeType,size,modifiedTime,webViewLink,shortcutDetails(targetId,targetMimeType)"
_SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
# Stored text files are read whole before paging; larger ones are refused.
_READ_FILE_MAX_BYTES = 5 * 1024 * 1024
_GOOGLE_EXPORTS = {
    "application/vnd.google-apps.document": {"text": "text/plain", "markdown": "text/markdown"},
    "application/vnd.google-apps.presentation": {"text": "text/plain", "markdown": "text/plain"},
    # Google exports only the first sheet as CSV.
    "application/vnd.google-apps.spreadsheet": {"text": "text/csv", "markdown": "text/csv"},
}
_TEXT_APPLICATION_TYPES = frozenset(
    {
        "application/csv",
        "application/javascript",
        "application/json",
        "application/ld+json",
        "application/sql",
        "application/toml",
        "application/x-javascript",
        "application/x-ndjson",
        "application/x-sh",
        "application/x-yaml",
        "application/xml",
        "application/yaml",
    }
)


def _export_type(mime_type: str, format: str) -> str | None:
    exports = _GOOGLE_EXPORTS.get(mime_type)
    return exports[format] if exports else None


def _is_text_type(mime_type: str) -> bool:
    base = mime_type.split(";", 1)[0].strip().lower()
    return (
        base.startswith("text/")
        or base in _TEXT_APPLICATION_TYPES
        or base.endswith(("+json", "+xml"))
    )


def _refusal_reason(mime_type: str) -> str:
    if mime_type == "application/vnd.google-apps.folder":
        return "it is a folder; list its files with drive_list_files"
    if mime_type == "application/vnd.google-apps.form":
        return "it is a Google Form; read it with forms_get_form"
    if mime_type.startswith("application/vnd.google-apps."):
        return f"Google files of type {mime_type} have no text export"
    return f"it is a binary file ({mime_type or 'unknown type'}), not text"


def _unreadable(metadata: dict[str, Any], reason: str) -> RecoverableToolError:
    name = metadata.get("name") or metadata.get("id")
    return RecoverableToolError(
        "invalid_input",
        f"drive_read_file cannot read '{name}': {reason}. It reads Google Docs, Sheets and Slides and "
        "stored text files; drive_download_file saves or exports other files on a locally running server.",
        required_action={"tool": "drive_get_file", "arguments": {"file_id": metadata.get("id")}},
        details={"file_id": metadata.get("id"), "mime_type": metadata.get("mimeType")},
    )


def _file_text(raw: bytes, *, mime_type: str) -> str:
    """Decode a downloaded or exported file into clean, pageable text."""
    text = raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    if mime_type.split(";", 1)[0].strip().lower() in {"text/html", "application/xhtml+xml"}:
        return html_to_text(text).strip()
    return remove_invisible_characters(text).strip("\n")


def _escape_drive_query_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _drive_time_bound(value: str, account_timezone: str) -> str:
    """An RFC3339 time or YYYY-MM-DD date as the UTC time Drive's q syntax compares.

    A date, or a time without an offset, is read in the account time zone.
    """
    text = value.strip()
    try:
        if len(text) == 10:
            parsed = datetime.fromisoformat(f"{text}T00:00:00")
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        raise ValueError(
            f"modified_after must be an RFC3339 time such as '2026-11-01T09:00:00-03:00' or a date "
            f"such as '2026-11-01', not {value!r}."
        ) from None
    if parsed.tzinfo is None:
        parsed = pytz.timezone(account_timezone).localize(parsed)
    # Drive reads a time without an offset as UTC.
    return parsed.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _list_files_query(
    *,
    query: str | None,
    folder_id: str | None,
    kind: FileKind | None,
    modified_after_bound: str | None,
    full_text: str | None,
) -> str | None:
    """AND the raw query with the task-shaped filters into one Drive q string."""
    filters: list[str] = []
    if folder_id is not None:
        if not folder_id.strip():
            raise ValueError("folder_id must not be blank; omit it to search every folder.")
        filters.append(f"'{_escape_drive_query_string(folder_id.strip())}' in parents")
    if kind is not None:
        filters.append(_KIND_CONDITIONS[kind])
    if modified_after_bound is not None:
        filters.append(f"modifiedTime > '{modified_after_bound}'")
    if full_text is not None:
        if not full_text.strip():
            raise ValueError("full_text must not be blank; omit it to skip the content search.")
        filters.append(f"fullText contains '{_escape_drive_query_string(full_text.strip())}'")
    if not filters:
        return query
    if query is not None and query.strip():
        filters.insert(0, f"({query.strip()})")
    return " and ".join(filters)


def _find_existing_file_by_name(
    service: Any,
    *,
    name: str,
    parent_ids: list[str],
) -> dict[str, Any] | None:
    safe_name = _escape_drive_query_string(name)
    # Only the folder the upload lands in: My Drive's root when no parent is
    # given, so overwrite can never replace a same-named file elsewhere.
    parent = _escape_drive_query_string(parent_ids[0]) if parent_ids else "root"
    query_parts = [f"name = '{safe_name}'", "trashed = false", f"'{parent}' in parents"]
    result = service.files().list(
        q=" and ".join(query_parts),
        pageSize=1,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        fields="files(id,name,parents,mimeType,driveId,webViewLink)",
    ).execute()
    files = result.get("files", [])
    return files[0] if files else None


def _renamed_filename(original_name: str, *, now: datetime) -> str:
    stem = Path(original_name).stem
    suffix = Path(original_name).suffix
    ts = now.strftime("%Y%m%d-%H%M%S")
    return f"{stem} (upload {ts}){suffix}"


async def _execute_resumable_upload_with_progress(
    request: Any,
    ctx: Context | None,
    progress_message: str,
) -> dict[str, Any]:
    response: dict[str, Any] | None = None
    method_id = "drive.files.upload"
    params: dict[str, Any] = {}
    if isinstance(request, LazyGoogleRequest):
        # A lazy request records the call; next_chunk needs the built one.
        method_id, params = request.method_id(), request.call_params()
        request = await run_blocking(materialize_google_request, request)
    # The whole chunk loop is one provider call for repeat safety: a create
    # interrupted mid-upload may or may not have produced the file.
    with track_provider_call(method_id, params):
        while response is None:
            status, response = await run_blocking(request.next_chunk)
            if status is not None and ctx is not None:
                await ctx.report_progress(
                    int(status.progress() * 100),
                    100,
                    progress_message,
                )
    if ctx is not None:
        await ctx.report_progress(100, 100, f"{progress_message} completed")
    return response


async def _send_upload(
    request: Any,
    *,
    resumable: bool,
    ctx: Context | None,
    progress_message: str,
) -> dict[str, Any]:
    """Send a create/update carrying media, in chunks when resumable."""
    if resumable:
        return await _execute_resumable_upload_with_progress(request, ctx, progress_message)
    result: dict[str, Any] = await execute_google_request(request)
    if ctx is not None:
        await ctx.report_progress(100, 100, f"{progress_message} completed")
    return result


def register(server: FastMCP) -> None:
    @server.tool(name="list_files")
    async def list_files(
        query: Annotated[
            str | None,
            (
                "Raw Drive search query in Drive's q syntax, terms joined with and/or, e.g. "
                "\"name contains 'budget' and trashed = false\" or \"starred = true\"; combined by AND "
                "with folder_id, kind, modified_after and full_text when those are given. Omit it and "
                "every filter to list every file the user can access, trashed files included."
            ),
        ] = None,
        folder_id: Annotated[
            str | None,
            (
                "Only the direct children of this folder (not files in its subfolders); a folder ID "
                "from drive_list_files with kind 'folder', or a shared drive ID for its top level."
            ),
        ] = None,
        kind: Annotated[
            FileKind | None,
            (
                "Only files of this kind, the same values as each result's kind: folder, document "
                "(Google Doc), spreadsheet (Google Sheet), presentation (Google Slides), form "
                "(Google Form), or image, video or audio (any stored file of that media type)."
            ),
        ] = None,
        modified_after: Annotated[
            str | None,
            (
                "Only files last modified after this time: RFC3339 such as '2026-11-01T09:00:00-03:00', "
                "or a date such as '2026-11-01' (midnight); a date or a time without an offset is read "
                "in the account time zone."
            ),
        ] = None,
        full_text: Annotated[
            str | None,
            (
                "Only files whose name, description or content contains these words (Drive's fullText "
                "search); quotes in the text are escaped for you."
            ),
        ] = None,
        page_size: Annotated[int, Field(ge=1, le=1000, description="Files per page; next_page_token continues the list.")] = 25,
        page_token: str | None = None,
        order_by: Annotated[
            str | None,
            (
                "Comma-separated sort keys, each optionally followed by ' desc': createdTime, folder, "
                "modifiedByMeTime, modifiedTime, name, name_natural, quotaBytesUsed, recency, "
                "sharedWithMeTime, starred, viewedByMeTime; e.g. 'folder,modifiedTime desc'. "
                "Omit for Google's default order."
            ),
        ] = None,
        drive_id: Annotated[
            str | None,
            (
                "Shared drive ID from drive_list_drives: search only that shared drive. Omit to search "
                "My Drive, files shared with the user and every shared drive they belong to."
            ),
        ] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Search or list Drive files in My Drive, shared with the user and in shared drives, by folder, kind, modification time, full text or a raw Drive query, all combined with AND. For a quick name lookup across Drive, contacts and Gmail at once use search_workspace, and for one known file use drive_get_file. Returns files (id, name, kind, mime_type, modified_at, parent_ids, owners, web_url, trashed, can_edit), count and next_page_token for the next page; trashed files are included unless the query says trashed = false."""
        request = ListFilesRequest(
            query=query,
            folder_id=folder_id,
            kind=kind,
            modified_after=modified_after,
            full_text=full_text,
            page_size=page_size,
            page_token=page_token,
            order_by=order_by,
            drive_id=drive_id,
        )
        service = drive_service()
        account_timezone = await resolve_user_timezone()
        q = _list_files_query(
            query=request.query,
            folder_id=request.folder_id,
            kind=request.kind,
            modified_after_bound=(
                _drive_time_bound(request.modified_after, account_timezone)
                if request.modified_after is not None
                else None
            ),
            full_text=request.full_text,
        )
        LOGGER.debug("Listing Google Drive files.")
        result = await execute_google_request(
            service.files()
            .list(
                q=q,
                pageSize=request.page_size,
                pageToken=request.page_token,
                orderBy=request.order_by,
                corpora="drive" if request.drive_id else "allDrives",
                driveId=request.drive_id,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields=_LIST_FILES_FIELDS,
            )
        )
        files = result.get("files", [])
        if ctx is not None:
            await ctx.report_progress(len(files), request.page_size, "Drive files page loaded")
        return files_page(result, account_timezone=account_timezone)

    @server.tool(name="get_file")
    async def get_file(
        file_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Get the metadata of one Drive file or folder by ID, including how its content can be retrieved. To find files by name or filter use drive_list_files; to read a file's text use drive_read_file, and to save or export it drive_download_file. Returns file (id, name, kind, mime_type, size (Drive's string) and size_bytes (the number), created_at, modified_at, owners, last_modified_by, parent_ids, drive_id, web_url, shared, trashed, can_edit, can_download, is_google_native, export_formats: the MIME types a Google-native file can be exported to)."""
        request = GetFileRequest(
            file_id=file_id,
        )
        service = drive_service()
        account_timezone = await resolve_user_timezone()
        LOGGER.debug(f"Fetching Drive file {request.file_id}.")
        file_obj = await execute_google_request(
            service.files()
            .get(
                fileId=request.file_id,
                supportsAllDrives=True,
                fields=_FILE_FIELDS,
            )
        )
        mime_type = file_obj.get("mimeType")
        file = file_envelope(file_obj, account_timezone=account_timezone)
        file["is_google_native"] = (
            str(mime_type).startswith(_GOOGLE_NATIVE_PREFIX) if mime_type is not None else None
        )
        # Drive returns exportLinks only for Google-native files that can be exported.
        file["export_formats"] = sorted(file_obj.get("exportLinks") or {})
        return {
            "file": file,
            "account_timezone": account_timezone,
        }

    @server.tool(name="create_folder")
    async def create_folder(
        name: Annotated[str, "Folder name as shown in Drive; Drive allows duplicate names."],
        parent_ids: Annotated[
            list[str] | None,
            (
                "One parent folder ID (Drive allows a single parent), or a shared drive ID to create "
                "it at that drive's top level; omit to create it in the root of My Drive."
            ),
        ] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create a new Drive folder. For other empty files, such as a blank Google Doc, use drive_upload_file with only name and mime_type; to put an existing file into a folder use drive_move_file. Returns status 'ok' and file (id, name, kind, mime_type, size, size_bytes, parent_ids, drive_id, web_url, trashed; null where the change does not report it); use file.id as a parent ID in later calls."""
        request = CreateFolderRequest(
            name=name,
            parent_ids=parent_ids or [],
        )
        service = drive_service()
        body: dict[str, Any] = {
            "name": request.name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if request.parent_ids:
            body["parents"] = request.parent_ids
        LOGGER.debug(f"Creating Drive folder '{request.name}'.")
        created = await execute_google_request(
            service.files()
            .create(
                body=body,
                supportsAllDrives=True,
                fields=_CREATED_FILE_FIELDS,
            )
        )
        return {"status": "ok", "file": file_summary(created)}

    @server.tool(name="upload_file", task=True)
    async def upload_file(
        local_path: Annotated[
            str | None,
            (
                "Path of a file on the machine running the server; accepted only when the server runs "
                "locally, and rejected with picker_required over remote MCP. Give at most one of "
                "local_path or uploaded_file; omit both to create an empty file from metadata."
            ),
        ] = None,
        uploaded_file: Annotated[
            str | None,
            (
                "Upload ID (upl_...) of a file the user added with the Workspace Files picker "
                "(uploads_file_manager) or listed by uploads_list_files; the way to send a file over "
                "remote MCP. Give at most one of local_path or uploaded_file."
            ),
        ] = None,
        file_id: Annotated[
            str | None,
            (
                "Existing Drive file whose content is replaced by local_path or uploaded_file, keeping "
                "its ID, name, folder and sharing; Drive stores it as a new revision. name, parent_ids, "
                "if_exists and description are rejected with it. Omit to create a new file."
            ),
        ] = None,
        name: Annotated[
            str | None,
            (
                "Name to store in Drive, including any extension; omit to keep the source file's "
                "original name. Required when creating an empty file from metadata."
            ),
        ] = None,
        parent_ids: Annotated[
            list[str] | None,
            (
                "One parent folder ID (Drive allows a single parent), or a shared drive ID for its top "
                "level; omit for the root of My Drive. The if_exists name check looks only in that "
                "folder (the root of My Drive when omitted)."
            ),
        ] = None,
        mime_type: Annotated[
            str | None,
            (
                "For an upload, the content's MIME type, e.g. 'application/pdf', 'image/png' or "
                "'text/csv'; the file is stored as-is, not converted, and omitted it is the picker's "
                "recorded type or guessed from the extension. For an empty file, its type, e.g. "
                "'application/vnd.google-apps.document' (blank Google Doc), "
                "'application/vnd.google-apps.spreadsheet' (blank Sheet) or "
                "'application/vnd.google-apps.presentation' (blank Slides deck); omit to let Drive choose."
            ),
        ] = None,
        description: Annotated[
            str | None,
            "Description text shown in the Drive details of the new or overwritten file.",
        ] = None,
        if_exists: Annotated[
            Literal["rename", "overwrite", "skip"] | None,
            (
                "For a new upload, what to do when a non-trashed file with the same name exists in the "
                "target folder: "
                "'rename' (the default) uploads a new file with a timestamp before the extension, e.g. "
                "'report (upload 20261103-142500).pdf', 'overwrite' replaces that file's content as a "
                "new revision, 'skip' uploads nothing and returns the existing file."
            ),
        ] = None,
        keep_revision_forever: Annotated[
            bool | None,
            (
                "With file_id only: true keeps the new revision forever instead of letting Drive purge "
                "it as an old revision; applies to binary (non-Google) files only."
            ),
        ] = None,
        resumable: Annotated[
            bool,
            (
                "Upload content in chunks with progress reports; false sends it in a single request. "
                "An empty file has no content to upload."
            ),
        ] = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Upload a file to Drive from the Workspace Files picker or a local path, as a new file or, with file_id, as new content of an existing file (same ID, name, folder and sharing). With neither source it creates an empty file from name and mime_type, such as a blank Google Doc in a chosen folder; for folders use drive_create_folder and to rename or set properties drive_update_file_metadata. Returns status 'ok', mode ('create', 'overwrite', 'skipped', 'replace' or 'metadata_only') and file (id, name, kind, mime_type, size, size_bytes, parent_ids, drive_id, web_url, trashed; null where not reported). Mode 'skipped' returns the same-named file that already exists."""
        request = UploadFileRequest(
            local_path=local_path,
            uploaded_file=uploaded_file,
            file_id=file_id,
            name=name,
            parent_ids=parent_ids or [],
            mime_type=mime_type,
            description=description,
            if_exists=if_exists,
            keep_revision_forever=keep_revision_forever,
            resumable=resumable,
        )
        service = drive_service()
        if not (request.local_path or request.uploaded_file):
            body: dict[str, Any] = {"name": request.name}
            if request.mime_type:
                body["mimeType"] = request.mime_type
            if request.parent_ids:
                body["parents"] = request.parent_ids
            if request.description is not None:
                body["description"] = request.description
            LOGGER.debug(f"Creating Drive metadata-only file '{request.name}'.")
            created = await execute_google_request(
                service.files().create(body=body, supportsAllDrives=True, fields=_CREATED_FILE_FIELDS)
            )
            return {"status": "ok", "mode": "metadata_only", "file": file_summary(created)}

        replacing = request.file_id is not None
        if request.uploaded_file:
            uploaded = workspace_file_upload.get_file(request.uploaded_file, ctx)
            source_name = uploaded.name
            media = media_bytes_upload(
                uploaded.data, request.mime_type or uploaded.mime_type, request.resumable
            )
        else:
            require_local_filesystem("Drive content update" if replacing else "Drive upload")
            if request.local_path is None:  # pragma: no cover - schema invariant
                raise ValueError("local_path is required for a local Drive upload.")
            src = Path(request.local_path)
            if not await run_blocking(src.exists):
                raise FileNotFoundError(f"Local file not found: {src}")
            source_name = src.name
            media = media_file_upload(request.local_path, request.mime_type, request.resumable)

        if request.file_id is not None:
            LOGGER.debug(f"Uploading replacement content for file {request.file_id}.")
            replaced = await _send_upload(
                service.files().update(
                    fileId=request.file_id,
                    media_body=media,
                    keepRevisionForever=request.keep_revision_forever,
                    supportsAllDrives=True,
                    fields=_UPDATED_CONTENT_FIELDS,
                ),
                resumable=request.resumable,
                ctx=ctx,
                progress_message="Uploading replacement Drive content",
            )
            return {"status": "ok", "mode": "replace", "file": file_summary(replaced)}

        if_exists_mode = request.if_exists or "rename"
        requested_name = request.name or source_name
        existing_file = await run_blocking(
            _find_existing_file_by_name,
            service,
            name=requested_name,
            parent_ids=request.parent_ids,
        )
        if existing_file and if_exists_mode == "skip":
            LOGGER.debug(
                f"Skipping upload: file '{requested_name}' already exists as {existing_file.get('id')}."
            )
            return {
                "status": "ok",
                "mode": "skipped",
                "reason": "file_exists",
                "file": file_summary(existing_file),
            }
        target_name = requested_name
        if existing_file and if_exists_mode == "rename":
            target_name = _renamed_filename(
                requested_name,
                now=user_now(await resolve_user_timezone()),
            )
            LOGGER.debug(f"File exists; renaming upload target to '{target_name}'.")
        body = {"name": target_name}
        if request.description is not None:
            body["description"] = request.description
        if existing_file and if_exists_mode == "overwrite":
            LOGGER.debug(f"Overwriting existing Drive file {existing_file.get('id')}.")
            updated = await _send_upload(
                service.files().update(
                    fileId=existing_file["id"],
                    body=body,
                    media_body=media,
                    supportsAllDrives=True,
                    fields=_UPLOADED_FILE_FIELDS,
                ),
                resumable=request.resumable,
                ctx=ctx,
                progress_message="Overwriting Drive file",
            )
            return {"status": "ok", "mode": "overwrite", "file": file_summary(updated)}
        if request.parent_ids:
            body["parents"] = request.parent_ids
        LOGGER.debug(f"Uploading file '{source_name}' to Drive.")
        created = await _send_upload(
            service.files().create(
                body=body,
                media_body=media,
                supportsAllDrives=True,
                fields=_UPLOADED_FILE_FIELDS,
            ),
            resumable=request.resumable,
            ctx=ctx,
            progress_message="Uploading Drive file",
        )
        return {"status": "ok", "mode": "create", "file": file_summary(created)}

    @server.tool(name="update_file_metadata")
    async def update_file_metadata(
        file_id: str,
        name: Annotated[str | None, "New file name; omit to keep the current name."] = None,
        description: Annotated[
            str | None, "New description text; an empty string clears it, omit to keep it."
        ] = None,
        app_properties: Annotated[
            dict[str, Any] | None,
            (
                "Private key/value pairs (string values) to add or change, visible only to requests "
                "made with this server's OAuth client; keys not listed are kept."
            ),
        ] = None,
        properties: Annotated[
            dict[str, Any] | None,
            "Public key/value pairs (string values) to add or change; keys not listed are kept.",
        ] = None,
        remove_property_keys: Annotated[
            list[str] | None, "Public property keys to delete from the file."
        ] = None,
        remove_app_property_keys: Annotated[
            list[str] | None, "Private app property keys to delete from the file."
        ] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Rename a Drive file or change its description and custom properties. Only the fields you pass change. To move it between folders use drive_move_file, and to replace its content use drive_upload_file with file_id. Returns status 'ok' and file (id, name, kind, mime_type, size, size_bytes, parent_ids, drive_id, web_url, trashed; null where the change does not report it)."""
        request = UpdateFileMetadataRequest(
            file_id=file_id,
            name=name,
            description=description,
            app_properties=app_properties,
            properties=properties,
            remove_property_keys=remove_property_keys or [],
            remove_app_property_keys=remove_app_property_keys or [],
        )
        service = drive_service()
        body: dict[str, Any] = {}
        if request.name is not None:
            body["name"] = request.name
        if request.description is not None:
            body["description"] = request.description
        if request.properties is not None:
            body["properties"] = request.properties
        if request.app_properties is not None:
            body["appProperties"] = request.app_properties
        if request.remove_property_keys:
            body.setdefault("properties", {})
            for key in request.remove_property_keys:
                body["properties"][key] = None
        if request.remove_app_property_keys:
            body.setdefault("appProperties", {})
            for key in request.remove_app_property_keys:
                body["appProperties"][key] = None
        LOGGER.debug(f"Updating metadata for Drive file {request.file_id}.")
        updated = await execute_google_request(
            service.files()
            .update(
                fileId=request.file_id,
                body=body,
                supportsAllDrives=True,
                fields=_UPDATED_METADATA_FIELDS,
            )
        )
        return {"status": "ok", "file": file_summary(updated)}

    @server.tool(name="move_file")
    async def move_file(
        file_id: str,
        add_parent_ids: Annotated[
            list[str] | None,
            "Destination folder ID (or a shared drive ID for its top level), as a one-element list.",
        ] = None,
        remove_parent_ids: Annotated[
            list[str] | None,
            "Current parent folder ID to take the file out of, from parent_ids in drive_get_file.",
        ] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Move a Drive file or folder to another folder. Drive allows one parent per file, so a move passes the destination in add_parent_ids and the current parent (from drive_get_file) in remove_parent_ids. To rename use drive_update_file_metadata, and to duplicate use drive_copy_file. Returns status 'ok' and file (id, name, kind, mime_type, size, size_bytes, parent_ids, drive_id, web_url, trashed; null where the change does not report it)."""
        request = MoveFileRequest(
            file_id=file_id,
            add_parent_ids=add_parent_ids or [],
            remove_parent_ids=remove_parent_ids or [],
        )
        service = drive_service()
        LOGGER.debug(f"Moving Drive file {request.file_id}.")
        updated = await execute_google_request(
            service.files()
            .update(
                fileId=request.file_id,
                addParents=",".join(request.add_parent_ids) if request.add_parent_ids else None,
                removeParents=",".join(request.remove_parent_ids) if request.remove_parent_ids else None,
                supportsAllDrives=True,
                fields=_MOVED_FILE_FIELDS,
            )
        )
        return {"status": "ok", "file": file_summary(updated)}

    @server.tool(name="copy_file")
    async def copy_file(
        file_id: str,
        name: Annotated[str | None, "Name of the copy; omit to let Drive name it."] = None,
        parent_ids: Annotated[
            list[str] | None,
            (
                "One destination folder ID (or a shared drive ID for its top level); omit to place the "
                "copy in the source file's folder."
            ),
        ] = None,
        description: Annotated[str | None, "Optional description to set on the new copy."] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Make a copy of a Drive file, such as duplicating a template Doc or Sheet. To relocate the original instead use drive_move_file, and to rename it drive_update_file_metadata. Returns status 'ok' and file (id, name, kind, mime_type, size, size_bytes, parent_ids, drive_id, web_url, trashed; null where the change does not report it) of the new copy; the source file is unchanged."""
        request = CopyFileRequest(
            file_id=file_id,
            name=name,
            parent_ids=parent_ids or [],
            description=description,
        )
        service = drive_service()
        body: dict[str, Any] = {}
        if request.name is not None:
            body["name"] = request.name
        if request.parent_ids:
            body["parents"] = request.parent_ids
        if request.description is not None:
            body["description"] = request.description
        LOGGER.debug(f"Copying Drive file {request.file_id}.")
        copied = await execute_google_request(
            service.files()
            .copy(
                fileId=request.file_id,
                body=body,
                supportsAllDrives=True,
                fields=_CREATED_FILE_FIELDS,
            )
        )
        return {"status": "ok", "file": file_summary(copied)}

    @server.tool(name="delete_file")
    async def delete_file(
        file_id: str,
        delete_mode: Annotated[
            Literal["trash", "permanent"],
            (
                "'trash' moves the file to Drive's trash, where it can be restored; 'permanent' deletes it "
                "irreversibly after asking the user to confirm."
            ),
        ] = "trash",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Delete a Drive file or folder: move it to the trash (default) or delete it permanently. Prefer the default trash mode, which is reversible from Drive's trash; to only revoke someone's access use drive_unshare_file instead. Returns status 'ok' with mode 'trash' and file (id, name, kind, mime_type, size, size_bytes, parent_ids, drive_id, web_url, trashed; null where the change does not report it), or mode 'permanent' and file_id. A permanent delete cannot be undone, asks the user to confirm first and returns status 'cancelled' if they decline."""
        request = DeleteFileRequest(
            file_id=file_id,
            delete_mode=delete_mode,
        )
        service = drive_service()
        if request.delete_mode == "trash":
            LOGGER.debug(f"Moving Drive file {request.file_id} to trash.")
            updated = await execute_google_request(
                service.files().update(
                    fileId=request.file_id,
                    body={"trashed": True},
                    supportsAllDrives=True,
                    fields="id,name,trashed,driveId,webViewLink",
                )
            )
            return {"status": "ok", "mode": "trash", "file": file_summary(updated)}
        LOGGER.debug(f"Permanently deleting Drive file {request.file_id}.")
        await execute_google_request(
            service.files().delete(
                fileId=request.file_id,
                supportsAllDrives=True,
            )
        )
        return {"status": "ok", "mode": "permanent", "file_id": request.file_id}

    @server.tool(name="download_file", task=True)
    async def download_file(
        file_id: str,
        output_path: Annotated[
            str,
            (
                "Destination file path on the machine running the server, e.g. "
                "'/tmp/report.pdf'; missing parent directories are created. Files over the server's "
                "MCP_MAX_DOWNLOAD_BYTES limit (250 MiB by default) fail."
            ),
        ],
        export_mime_type: Annotated[
            str | None,
            (
                "Format a Google-native file is exported to, which must suit its type, e.g. "
                "'application/pdf', 'text/plain' or "
                "'application/vnd.openxmlformats-officedocument.wordprocessingml.document' for Docs, "
                "'text/csv' (first sheet only) or "
                "'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' for Sheets; "
                "drive_get_file lists the valid ones as export_formats. Omit for PDF (PNG for "
                "Drawings); ignored for stored files, which are saved as they are."
            ),
        ] = None,
        overwrite: Annotated[
            bool, "Replace a file already at output_path; when false the call fails if it exists."
        ] = False,
        acknowledge_abuse: Annotated[
            bool,
            (
                "Download a stored file even though Google flagged it as malware or abusive; Google "
                "allows this only for the file's owner or an organizer of its shared drive."
            ),
        ] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Save a Drive file to the server's disk: stored files such as PDFs or images are downloaded as they are, and Google Docs, Sheets, Slides and Drawings are exported (PDF by default, PNG for Drawings, or export_mime_type). Calendar event attachments are Drive files, so download them here by their file_id; to read text without saving a file use drive_read_file. Returns status 'ok', file_id, name, mode ('download' or 'export'), mime_type, export_mime_type, saved_to and bytes_written. It writes to the server's filesystem, so it works only when the server runs locally and is rejected with picker_required over remote MCP; Google limits exports to 10 MB."""
        request = DownloadFileRequest(
            file_id=file_id,
            output_path=output_path,
            export_mime_type=export_mime_type,
            overwrite=overwrite,
            acknowledge_abuse=acknowledge_abuse,
        )
        require_local_filesystem("Drive download")
        service = drive_service()
        metadata = await execute_google_request(
            service.files().get(fileId=request.file_id, supportsAllDrives=True, fields=_DOWNLOAD_META_FIELDS)
        )
        mime_type = str(metadata.get("mimeType") or "")
        out = Path(request.output_path)
        export_mime: str | None = None
        if mime_type.startswith(_GOOGLE_NATIVE_PREFIX):
            if mime_type == _FOLDER_MIME:
                raise ValueError(
                    f"Drive file {request.file_id} is a folder and has no content; list its files with "
                    "drive_list_files (folder_id)."
                )
            export_mime = request.export_mime_type or GOOGLE_EXPORT_MIME_DEFAULTS.get(mime_type)
            if not export_mime:
                raise ValueError(
                    f"Drive file {request.file_id} is a Google-native file of type {mime_type} with no "
                    "default export format: pass export_mime_type, one of the export_formats drive_get_file "
                    "lists for it."
                )
            LOGGER.debug(f"Exporting Google file {request.file_id} as {export_mime}.")
            media_req = service.files().export_media(fileId=request.file_id, mimeType=export_mime)
            mode = "export"
            progress_label = "Exporting Google-native file"
        else:
            media_req = service.files().get_media(
                fileId=request.file_id,
                acknowledgeAbuse=request.acknowledge_abuse,
                supportsAllDrives=True,
            )
            mode = "download"
            progress_label = "Downloading Drive file"
        size = await stream_google_download(
            media_req,
            out,
            overwrite=request.overwrite,
            progress_label=progress_label,
            ctx=ctx,
        )
        return {
            "status": "ok",
            "file_id": request.file_id,
            "name": metadata.get("name"),
            "mode": mode,
            "mime_type": mime_type or None,
            "export_mime_type": export_mime,
            "saved_to": str(out),
            "bytes_written": size,
        }

    @server.tool(name="read_file")
    async def read_file(
        file_id: str,
        format: Annotated[
            Literal["text", "markdown"],
            (
                "'text' (default) returns plain text; 'markdown' returns a Google Doc as Markdown "
                "(headings, lists, tables, links). Other files read the same in both formats."
            ),
        ] = "text",
        offset: Annotated[
            int,
            (
                "Character offset into the file's text to resume from: the next_offset of a truncated "
                "result. Omit (0) for the first page."
            ),
        ] = 0,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read the text of one Drive file without saving it, a page of about 8,000 characters at a time: Google Docs and Slides export as text (Docs also as Markdown), Sheets as CSV of the first sheet, and stored text files up to 5 MB (text, Markdown, CSV, JSON, XML, HTML) are downloaded. Binary files, folders, forms and drawings fail with invalid_input; use docs_read_document for a Doc's tabs, chips and footnotes (it renders the document itself, so its body_chars differ from this export's). Returns file_id, name, mime_type, format, exported_as, modified_at, web_url and the page as body, truncated, next_offset (pass it back as offset), body_chars and body_sha256 (length and hash of the whole text)."""
        service = drive_service()
        account_timezone = await resolve_user_timezone()
        metadata = await execute_google_request(
            service.files().get(fileId=file_id, supportsAllDrives=True, fields=_READ_FILE_FIELDS)
        )
        if metadata.get("mimeType") == _SHORTCUT_MIME:
            target = (metadata.get("shortcutDetails") or {}).get("targetId")
            if not target:
                raise _unreadable(metadata, "it is a shortcut whose target is not available")
            metadata = await execute_google_request(
                service.files().get(fileId=target, supportsAllDrives=True, fields=_READ_FILE_FIELDS)
            )
        mime_type = str(metadata.get("mimeType") or "")
        exported_as = _export_type(mime_type, format)
        if exported_as is not None:
            data = await execute_google_request(
                service.files().export(fileId=metadata.get("id"), mimeType=exported_as)
            )
        elif _is_text_type(mime_type):
            size = int(metadata.get("size") or 0)
            if size > _READ_FILE_MAX_BYTES:
                raise _unreadable(
                    metadata,
                    f"it is {size:,} bytes, above the {_READ_FILE_MAX_BYTES:,}-byte read limit",
                )
            data = await execute_google_request(
                service.files().get_media(fileId=metadata.get("id"), supportsAllDrives=True)
            )
        else:
            raise _unreadable(metadata, _refusal_reason(mime_type))
        raw = data if isinstance(data, bytes) else str(data).encode("utf-8")
        if b"\x00" in raw[:8192]:
            raise _unreadable(metadata, "its content is binary, not text")
        text = _file_text(raw, mime_type=exported_as or mime_type)
        if ctx is not None:
            await ctx.report_progress(1, 1, "Drive file read")
        return {
            "file_id": metadata.get("id"),
            "name": metadata.get("name"),
            "mime_type": mime_type,
            "format": format,
            "exported_as": exported_as,
            "modified_at": in_account_timezone(metadata.get("modifiedTime"), account_timezone),
            "web_url": clean_web_url(metadata.get("webViewLink")),
            **paged_text(text, offset=offset),
        }
