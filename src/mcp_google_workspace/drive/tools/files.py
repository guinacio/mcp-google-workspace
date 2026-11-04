"""Drive tools for files/folders/content operations."""

from __future__ import annotations

import logging

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP

from ...auth.google_auth import LazyGoogleRequest, materialize_google_request
from ...common.async_ops import execute_google_request, run_blocking
from ...common.confirmation import confirm_destructive_action
from ...common.repeat_safety import track_provider_call
from ...common.timezone import resolve_user_timezone, user_now
from ...file_uploads import require_local_filesystem, workspace_file_upload
from ...common.downloads import stream_google_download
from ..client import drive_service, media_bytes_upload, media_file_upload
from ..schemas import (
    CopyFileRequest,
    CreateFileMetadataRequest,
    CreateFolderRequest,
    DeleteFileRequest,
    DownloadFileRequest,
    ExportGoogleFileRequest,
    GetFileContentCapabilitiesRequest,
    GetFileRequest,
    ListFilesRequest,
    MoveFileRequest,
    UpdateFileContentRequest,
    UpdateFileMetadataRequest,
    UploadFileRequest,
)
from ..presentation import file_envelope

LOGGER = logging.getLogger(__name__)


# Shared drives are always in scope (supportsAllDrives=true on every call), and
# each call requests the fields its normalizer reads; neither is a tool input.
_LIST_FILES_FIELDS = "nextPageToken,files(id,name,mimeType,parents,modifiedTime,createdTime,size,driveId,webViewLink,description,shared,starred,trashed,ownedByMe,owners(displayName,emailAddress),lastModifyingUser(displayName,emailAddress),capabilities(canEdit,canDownload))"
_FILE_FIELDS = "id,name,mimeType,parents,modifiedTime,createdTime,size,driveId,webViewLink,description,shared,starred,trashed,ownedByMe,owners(displayName,emailAddress),lastModifyingUser(displayName,emailAddress),capabilities(canEdit,canDownload)"
_CREATED_FILE_FIELDS = "id,name,mimeType,parents,driveId,webViewLink"
_UPLOADED_FILE_FIELDS = "id,name,mimeType,size,parents,driveId,webViewLink"
_UPDATED_METADATA_FIELDS = "id,name,mimeType,parents,modifiedTime,size,driveId,webViewLink"
_UPDATED_CONTENT_FIELDS = "id,name,mimeType,size,parents,driveId,webViewLink,modifiedTime"
_MOVED_FILE_FIELDS = "id,name,parents,driveId,webViewLink"


def _build_metadata_body(request: CreateFileMetadataRequest) -> dict[str, Any]:
    body: dict[str, Any] = {"name": request.name}
    if request.mime_type:
        body["mimeType"] = request.mime_type
    if request.parent_ids:
        body["parents"] = request.parent_ids
    if request.description is not None:
        body["description"] = request.description
    if request.app_properties is not None:
        body["appProperties"] = request.app_properties
    if request.properties is not None:
        body["properties"] = request.properties
    return body


def _escape_drive_query_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _find_existing_file_by_name(
    service: Any,
    *,
    name: str,
    parent_ids: list[str],
) -> dict[str, Any] | None:
    safe_name = _escape_drive_query_string(name)
    query_parts = [f"name = '{safe_name}'", "trashed = false"]
    if parent_ids:
        query_parts.append(f"'{parent_ids[0]}' in parents")
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


def register(server: FastMCP) -> None:
    @server.tool(name="list_files")
    async def list_files(
        query: Annotated[
            str | None,
            (
                "Drive search query in Drive's q syntax, terms joined with and/or, e.g. "
                "\"name contains 'budget' and trashed = false\", "
                "\"'<folderId>' in parents and mimeType = 'application/vnd.google-apps.folder'\", "
                "\"fullText contains 'invoice' and modifiedTime > '2026-01-01T00:00:00'\". "
                "Omit to list every file the user can access, trashed files included."
            ),
        ] = None,
        page_size: Annotated[int, "Files per page, 1 to 1000."] = 25,
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
        corpora: Annotated[
            Literal["user", "drive", "domain", "allDrives"] | None,
            (
                "Which collection to search: 'user' (files the user created, opened or had shared "
                "directly with them; Google's default), 'drive' (one shared drive, requires drive_id), "
                "'domain' (files shared to the user's domain), or 'allDrives' (the user's files plus "
                "every shared drive they belong to)."
            ),
        ] = None,
        drive_id: Annotated[
            str | None,
            "Shared drive ID from drive_list_drives; required when corpora is 'drive', otherwise omit.",
        ] = None,
        spaces: Annotated[
            Literal["drive", "appDataFolder"] | None,
            (
                "Storage space to search: 'drive' (regular Drive files, Google's default) or "
                "'appDataFolder' (the hidden per-app data folder)."
            ),
        ] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Search or list Drive files with Drive's query syntax, shared-drive files included. Use it for precise filters (folder contents, MIME type, dates, full text); for a quick name lookup across Drive, contacts and Gmail at once use search_workspace, and for one known file use drive_get_file. Returns files (id, name, kind, mime_type, modified_at, parent_ids, owners, web_url, trashed, can_edit), count and next_page_token for the next page."""
        request = ListFilesRequest(
            query=query,
            page_size=page_size,
            page_token=page_token,
            order_by=order_by,
            corpora=corpora,
            drive_id=drive_id,
            spaces=spaces,
        )
        service = drive_service()
        account_timezone = await resolve_user_timezone()
        LOGGER.debug("Listing Google Drive files.")
        result = await execute_google_request(
            service.files()
            .list(
                q=request.query,
                pageSize=request.page_size,
                pageToken=request.page_token,
                orderBy=request.order_by,
                corpora=request.corpora,
                driveId=request.drive_id,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                spaces=request.spaces,
                fields=_LIST_FILES_FIELDS,
            )
        )
        files = result.get("files", [])
        if ctx is not None:
            await ctx.report_progress(len(files), request.page_size, "Drive files page loaded")
        return {
            "files": [file_envelope(file, account_timezone=account_timezone) for file in files],
            "next_page_token": result.get("nextPageToken"),
            "count": len(files),
            "account_timezone": account_timezone,
        }

    @server.tool(name="get_file")
    async def get_file(
        file_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Get the metadata of one Drive file or folder by ID. To find files by name or filter use drive_list_files; to read a file's content use drive_download_file, drive_export_google_file or the Docs, Sheets and Slides tools. Returns file (id, name, kind, mime_type, size, created_at, modified_at, owners, last_modified_by, parent_ids, drive_id, web_url, shared, trashed, can_edit, can_download)."""
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
        return {
            "file": file_envelope(file_obj, account_timezone=account_timezone),
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
        """Create a new Drive folder. For other empty files, such as a blank Google Doc, use drive_create_file_metadata; to put an existing file into a folder use drive_move_file. Returns status 'ok' and file (id, name, mimeType, parents, driveId, webViewLink); use file.id as a parent ID in later calls."""
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
        return {"status": "ok", "file": created}

    @server.tool(name="create_file_metadata")
    async def create_file_metadata(
        name: Annotated[str, "File name as shown in Drive, including any extension you want."],
        mime_type: Annotated[
            str | None,
            (
                "Type of the new empty file, e.g. 'application/vnd.google-apps.document' (blank Google "
                "Doc), 'application/vnd.google-apps.spreadsheet' (blank Sheet), "
                "'application/vnd.google-apps.presentation' (blank Slides deck) or 'text/plain'; omit "
                "to let Drive choose."
            ),
        ] = None,
        parent_ids: Annotated[
            list[str] | None,
            (
                "One parent folder ID (Drive allows a single parent), or a shared drive ID for its top "
                "level; omit to create the file in the root of My Drive."
            ),
        ] = None,
        description: Annotated[str | None, "Description text shown in the file's Drive details."] = None,
        app_properties: Annotated[
            dict[str, Any] | None,
            (
                "Private key/value pairs (string values) stored on the file, visible only to requests "
                "made with this server's OAuth client."
            ),
        ] = None,
        properties: Annotated[
            dict[str, Any] | None,
            "Public custom key/value pairs (string values) stored on the file, visible to any app.",
        ] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create an empty Drive file from metadata alone, with no uploaded content. Use it for a blank native Google file placed in a chosen folder, or a placeholder; to store bytes use drive_upload_file, for folders drive_create_folder, and docs_create_document, sheets_create_spreadsheet or slides_create_presentation when no folder is needed. Returns status 'ok' and file (id, name, mimeType, parents, driveId, webViewLink)."""
        request = CreateFileMetadataRequest(
            name=name,
            mime_type=mime_type,
            parent_ids=parent_ids or [],
            description=description,
            app_properties=app_properties,
            properties=properties,
        )
        service = drive_service()
        body = _build_metadata_body(request)
        LOGGER.debug(f"Creating Drive metadata-only file '{request.name}'.")
        created = await execute_google_request(
            service.files()
            .create(
                body=body,
                supportsAllDrives=True,
                fields=_CREATED_FILE_FIELDS,
            )
        )
        return {"status": "ok", "file": created}

    @server.tool(name="upload_file", task=True)
    async def upload_file(
        local_path: Annotated[
            str | None,
            (
                "Path of a file on the machine running the server; accepted only when the server runs "
                "locally, and rejected with picker_required over remote MCP. Give exactly one of "
                "local_path or uploaded_file."
            ),
        ] = None,
        uploaded_file: Annotated[
            str | None,
            (
                "Upload ID (upl_...) of a file the user added with the Workspace Files picker "
                "(files_file_manager) or listed by files_list_files; the way to send a file over remote "
                "MCP. Give exactly one of local_path or uploaded_file."
            ),
        ] = None,
        name: Annotated[
            str | None, "Name to store in Drive; omit to keep the source file's original name."
        ] = None,
        parent_ids: Annotated[
            list[str] | None,
            (
                "One parent folder ID (Drive allows a single parent), or a shared drive ID for its top "
                "level; omit for the root of My Drive. The if_exists name check looks only in the first "
                "parent, or anywhere the user can access when omitted."
            ),
        ] = None,
        mime_type: Annotated[
            str | None,
            (
                "MIME type of the content, e.g. 'application/pdf', 'image/png' or 'text/csv'; the file "
                "is stored as-is, not converted to a Google format. Omit to use the picker's recorded "
                "type, or the type guessed from a local file's extension."
            ),
        ] = None,
        resumable: Annotated[
            bool, "Upload in chunks with progress reports; false sends the file in a single request."
        ] = True,
        if_exists: Annotated[
            Literal["rename", "overwrite", "skip"],
            (
                "What to do when a non-trashed file with the same name exists: 'rename' uploads a new "
                "file with a timestamp before the extension, e.g. 'report (upload 20261103-142500).pdf', "
                "'overwrite' replaces that file's content "
                "as a new revision, 'skip' uploads nothing and returns the existing file."
            ),
        ] = "rename",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Upload a file to Drive as a new file, from the Workspace Files picker or a local path. To replace the content of a file you already have the ID of use drive_update_file_content, and for an empty file or native Google file use drive_create_file_metadata. Returns status 'ok' with mode 'create' or 'overwrite' and file (id, name, mimeType, size, parents, driveId, webViewLink), or status 'skipped' with existing_file."""
        request = UploadFileRequest(
            local_path=local_path,
            uploaded_file=uploaded_file,
            name=name,
            parent_ids=parent_ids or [],
            mime_type=mime_type,
            resumable=resumable,
            if_exists=if_exists,
        )
        service = drive_service()
        if request.uploaded_file:
            uploaded = workspace_file_upload.get_file(request.uploaded_file, ctx)
            source_name = uploaded.name
            media = media_bytes_upload(
                uploaded.data, request.mime_type or uploaded.mime_type, request.resumable
            )
        else:
            require_local_filesystem("Drive upload")
            if request.local_path is None:  # pragma: no cover - schema invariant
                raise ValueError("local_path is required for a local Drive upload.")
            src = Path(request.local_path)
            if not await run_blocking(src.exists):
                raise FileNotFoundError(f"Local file not found: {src}")
            source_name = src.name
            media = media_file_upload(request.local_path, request.mime_type, request.resumable)
        requested_name = request.name or source_name
        existing_file = await run_blocking(
            _find_existing_file_by_name,
            service,
            name=requested_name,
            parent_ids=request.parent_ids,
        )
        if existing_file and request.if_exists == "skip":
            LOGGER.debug(
                f"Skipping upload: file '{requested_name}' already exists as {existing_file.get('id')}."
            )
            return {
                "status": "skipped",
                "reason": "file_exists",
                "existing_file": existing_file,
            }
        target_name = requested_name
        if existing_file and request.if_exists == "rename":
            target_name = _renamed_filename(
                requested_name,
                now=user_now(await resolve_user_timezone()),
            )
            LOGGER.debug(f"File exists; renaming upload target to '{target_name}'.")
        body: dict[str, Any] = {"name": target_name}
        if request.parent_ids:
            body["parents"] = request.parent_ids
        if existing_file and request.if_exists == "overwrite":
            LOGGER.debug(f"Overwriting existing Drive file {existing_file.get('id')}.")
            update_request = (
                service.files().update(
                    fileId=existing_file["id"],
                    body={"name": target_name},
                    media_body=media,
                    supportsAllDrives=True,
                    fields=_UPLOADED_FILE_FIELDS,
                )
            )
            if request.resumable:
                updated = await _execute_resumable_upload_with_progress(
                    update_request,
                    ctx,
                    "Overwriting Drive file",
                )
            else:
                updated = await execute_google_request(update_request)
                if ctx is not None:
                    await ctx.report_progress(100, 100, "Overwriting Drive file completed")
            return {"status": "ok", "mode": "overwrite", "file": updated}
        LOGGER.debug(f"Uploading file '{source_name}' to Drive.")
        create_request = (
            service.files()
            .create(
                body=body,
                media_body=media,
                supportsAllDrives=True,
                fields=_UPLOADED_FILE_FIELDS,
            )
        )
        if request.resumable:
            created = await _execute_resumable_upload_with_progress(
                create_request,
                ctx,
                "Uploading Drive file",
            )
        else:
            created = await execute_google_request(create_request)
            if ctx is not None:
                await ctx.report_progress(100, 100, "Uploading Drive file completed")
        return {"status": "ok", "mode": "create", "file": created}

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
        """Rename a Drive file or change its description and custom properties. Only the fields you pass change. To move it between folders use drive_move_file, and to replace its content use drive_update_file_content. Returns status 'ok' and file (id, name, mimeType, parents, modifiedTime, size, driveId, webViewLink)."""
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
        return {"status": "ok", "file": updated}

    @server.tool(name="update_file_content")
    async def update_file_content(
        file_id: str,
        local_path: Annotated[
            str | None,
            (
                "Path of the replacement file on the machine running the server; accepted only when the "
                "server runs locally, and rejected with picker_required over remote MCP. Give exactly "
                "one of local_path or uploaded_file."
            ),
        ] = None,
        uploaded_file: Annotated[
            str | None,
            (
                "Upload ID (upl_...) of the replacement file from the Workspace Files picker "
                "(files_file_manager) or files_list_files; the way to send a file over remote MCP. Give "
                "exactly one of local_path or uploaded_file."
            ),
        ] = None,
        mime_type: Annotated[
            str | None,
            (
                "MIME type of the new content, e.g. 'application/pdf' or 'text/plain'; omit to use the "
                "picker's recorded type, or the type guessed from a local file's extension."
            ),
        ] = None,
        resumable: Annotated[
            bool, "Upload in chunks with progress reports; false sends the file in a single request."
        ] = True,
        keep_revision_forever: Annotated[
            bool | None,
            (
                "True keeps the new revision forever instead of letting Drive purge it as an old "
                "revision; applies to binary (non-Google) files only."
            ),
        ] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Replace the content of an existing Drive file, keeping its ID, name, folder and sharing. Use it to publish a new version of a file; to add a separate file use drive_upload_file, and to rename it use drive_update_file_metadata. Returns status 'ok' and file (id, name, mimeType, size, parents, driveId, webViewLink, modifiedTime). Drive stores the new content as a new revision of the file."""
        request = UpdateFileContentRequest(
            file_id=file_id,
            local_path=local_path,
            uploaded_file=uploaded_file,
            mime_type=mime_type,
            resumable=resumable,
            keep_revision_forever=keep_revision_forever,
        )
        service = drive_service()
        if request.uploaded_file:
            uploaded = workspace_file_upload.get_file(request.uploaded_file, ctx)
            media = media_bytes_upload(
                uploaded.data, request.mime_type or uploaded.mime_type, request.resumable
            )
        else:
            require_local_filesystem("Drive content update")
            if request.local_path is None:  # pragma: no cover - schema invariant
                raise ValueError("local_path is required for a local Drive content update.")
            media = media_file_upload(request.local_path, request.mime_type, request.resumable)
        LOGGER.debug(f"Uploading replacement content for file {request.file_id}.")
        update_request = (
            service.files()
            .update(
                fileId=request.file_id,
                media_body=media,
                keepRevisionForever=request.keep_revision_forever,
                supportsAllDrives=True,
                fields=_UPDATED_CONTENT_FIELDS,
            )
        )
        if request.resumable:
            updated = await _execute_resumable_upload_with_progress(
                update_request,
                ctx,
                "Uploading replacement Drive content",
            )
        else:
            updated = await execute_google_request(update_request)
            if ctx is not None:
                await ctx.report_progress(100, 100, "Uploading replacement Drive content completed")
        return {"status": "ok", "file": updated}

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
        """Move a Drive file or folder to another folder. Drive allows one parent per file, so a move passes the destination in add_parent_ids and the current parent (from drive_get_file) in remove_parent_ids. To rename use drive_update_file_metadata, and to duplicate use drive_copy_file. Returns status 'ok' and file (id, name, parents, driveId, webViewLink)."""
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
        return {"status": "ok", "file": updated}

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
        """Make a copy of a Drive file, such as duplicating a template Doc or Sheet. To relocate the original instead use drive_move_file, and to rename it drive_update_file_metadata. Returns status 'ok' and file (id, name, mimeType, parents, driveId, webViewLink) of the new copy; the source file is unchanged."""
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
        return {"status": "ok", "file": copied}

    @server.tool(name="delete_file")
    async def delete_file(
        file_id: str,
        delete_mode: Annotated[
            Literal["trash", "permanent"],
            (
                "'trash' moves the file to Drive's trash, where it can be restored, with no "
                "confirmation; 'permanent' deletes it irreversibly after asking the user to confirm."
            ),
        ] = "trash",
        confirm_permanent: Annotated[
            bool,
            (
                "Must stay true for delete_mode='permanent' (false is rejected); a permanent "
                "delete is always confirmed interactively. Ignored for delete_mode='trash'."
            ),
        ] = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Delete a Drive file or folder: move it to the trash (default) or delete it permanently. Prefer the default trash mode, which is reversible from Drive's trash; to only revoke someone's access use drive_delete_permission instead. Returns status 'ok' with mode 'trash' and file (id, name, trashed, driveId, webViewLink), or mode 'permanent' and file_id. A permanent delete cannot be undone, asks the user to confirm first and returns status 'cancelled' if they decline."""
        request = DeleteFileRequest(
            file_id=file_id,
            delete_mode=delete_mode,
            confirm_permanent=confirm_permanent,
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
            return {"status": "ok", "mode": "trash", "file": updated}
        if not request.confirm_permanent:
            raise ValueError(
                "Permanent deletion requires confirm_permanent=true and interactive confirmation."
            )
        if not await confirm_destructive_action(
            ctx,
            "delete_file",
            f"Permanently delete Drive file {request.file_id}? This cannot be undone.",
        ):
            return {"status": "cancelled", "mode": "permanent"}
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
        overwrite: Annotated[
            bool, "Replace a file already at output_path; when false the call fails if it exists."
        ] = False,
        acknowledge_abuse: Annotated[
            bool,
            (
                "Download even though Google flagged the file as malware or abusive; Google allows "
                "this only for the file's owner or an organizer of its shared drive."
            ),
        ] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Download a stored (non-Google) Drive file, such as a PDF or image, to the server's disk. Google Docs, Sheets and Slides cannot be downloaded directly: convert them with drive_export_google_file, and check which applies with drive_get_file_content_capabilities. Returns status 'ok', file_id, saved_to and bytes_written. It writes to the server's filesystem, so it works only when the server runs locally and is rejected with picker_required over remote MCP."""
        request = DownloadFileRequest(
            file_id=file_id,
            output_path=output_path,
            overwrite=overwrite,
            acknowledge_abuse=acknowledge_abuse,
        )
        require_local_filesystem("Drive download")
        service = drive_service()
        out = Path(request.output_path)
        media_req = service.files().get_media(
            fileId=request.file_id,
            acknowledgeAbuse=request.acknowledge_abuse,
            supportsAllDrives=True,
        )
        size = await stream_google_download(
            media_req,
            out,
            overwrite=request.overwrite,
            progress_label="Downloading Drive file",
            ctx=ctx,
        )
        return {
            "status": "ok",
            "file_id": request.file_id,
            "saved_to": str(out),
            "bytes_written": size,
        }

    @server.tool(name="export_google_file", task=True)
    async def export_google_file(
        file_id: str,
        mime_type: Annotated[
            str,
            (
                "Target format, which must suit the source type, e.g. for Docs 'application/pdf', "
                "'text/plain' or 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'; "
                "for Sheets 'text/csv' (first sheet only) or "
                "'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'; for Slides "
                "'application/pdf' or 'application/vnd.openxmlformats-officedocument.presentationml.presentation'. "
                "The export_links keys of drive_get_file_content_capabilities list the valid ones."
            ),
        ],
        output_path: Annotated[
            str,
            (
                "Destination file path on the machine running the server, e.g. '/tmp/notes.pdf'; "
                "missing parent directories are created."
            ),
        ],
        overwrite: Annotated[
            bool, "Replace a file already at output_path; when false the call fails if it exists."
        ] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Export a Google Doc, Sheet, Slides deck or Drawing to a file format on the server's disk. For stored files such as PDFs or images use drive_download_file; to read a Doc or Sheet without saving a file use docs_get_document or sheets_get_sheet_values. Returns status 'ok', file_id, mime_type, saved_to and bytes_written. It writes to the server's filesystem, so it works only when the server runs locally and is rejected with picker_required over remote MCP; Google limits exports to 10 MB."""
        request = ExportGoogleFileRequest(
            file_id=file_id,
            mime_type=mime_type,
            output_path=output_path,
            overwrite=overwrite,
        )
        require_local_filesystem("Drive export")
        service = drive_service()
        LOGGER.debug(f"Exporting Google file {request.file_id} as {request.mime_type}.")
        media_req = service.files().export_media(fileId=request.file_id, mimeType=request.mime_type)
        path = Path(request.output_path)
        size = await stream_google_download(
            media_req,
            path,
            overwrite=request.overwrite,
            progress_label="Exporting Google-native file",
            ctx=ctx,
        )
        return {
            "status": "ok",
            "file_id": request.file_id,
            "mime_type": request.mime_type,
            "saved_to": str(path),
            "bytes_written": size,
        }

    @server.tool(name="get_file_content_capabilities")
    async def get_file_content_capabilities(
        file_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Check how a Drive file's content can be retrieved before downloading or exporting it. Call it when unsure whether to use drive_download_file (stored files) or drive_export_google_file (native Google files). Returns file_id, name, mime_type, is_google_native, can_download, web_content_link and export_links, a map from each export MIME type the file supports to its download URL; nothing is written to disk."""
        request = GetFileContentCapabilitiesRequest(
            file_id=file_id,
        )
        service = drive_service()
        LOGGER.debug(f"Inspecting content capabilities for file {request.file_id}.")
        file_obj = await execute_google_request(
            service.files()
            .get(
                fileId=request.file_id,
                supportsAllDrives=True,
                fields="id,name,mimeType,size,capabilities(canDownload,canEdit),webContentLink,exportLinks",
            )
        )
        mime_type = file_obj.get("mimeType")
        is_google_native = str(mime_type).startswith("application/vnd.google-apps.")
        return {
            "file_id": file_obj.get("id"),
            "name": file_obj.get("name"),
            "mime_type": mime_type,
            "is_google_native": is_google_native,
            "can_download": file_obj.get("capabilities", {}).get("canDownload"),
            "web_content_link": file_obj.get("webContentLink"),
            "export_links": file_obj.get("exportLinks", {}),
        }
