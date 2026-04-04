"""Drive tools for files/folders/content operations."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastmcp import Context, FastMCP
from googleapiclient.http import MediaIoBaseDownload

from ...common.async_ops import execute_google_request, run_blocking, write_bytes_file
from ..client import drive_service, media_file_upload, write_bytes_to_path
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
    supports_all_drives: bool,
) -> dict[str, Any] | None:
    safe_name = _escape_drive_query_string(name)
    query_parts = [f"name = '{safe_name}'", "trashed = false"]
    if parent_ids:
        query_parts.append(f"'{parent_ids[0]}' in parents")
    result = service.files().list(
        q=" and ".join(query_parts),
        pageSize=1,
        supportsAllDrives=supports_all_drives,
        includeItemsFromAllDrives=True,
        fields="files(id,name,parents,mimeType,driveId,webViewLink)",
    ).execute()
    files = result.get("files", [])
    return files[0] if files else None


def _renamed_filename(original_name: str) -> str:
    stem = Path(original_name).stem
    suffix = Path(original_name).suffix
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stem} (upload {ts}){suffix}"


async def _execute_resumable_upload_with_progress(
    request: Any,
    ctx: Context | None,
    progress_message: str,
) -> dict[str, Any]:
    response: dict[str, Any] | None = None
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
        query: str | None = None,
        page_size: int = 25,
        page_token: str | None = None,
        order_by: str | None = None,
        corpora: str | None = None,
        drive_id: str | None = None,
        include_items_from_all_drives: bool = True,
        supports_all_drives: bool = True,
        spaces: str | None = None,
        fields: str = "nextPageToken, files(id,name,mimeType,parents,modifiedTime,size,driveId,webViewLink)",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List/search files with Shared Drives-aware query options."""
        request = ListFilesRequest(
            query=query,
            page_size=page_size,
            page_token=page_token,
            order_by=order_by,
            corpora=corpora,
            drive_id=drive_id,
            include_items_from_all_drives=include_items_from_all_drives,
            supports_all_drives=supports_all_drives,
            spaces=spaces,
            fields=fields,
        )
        service = drive_service()
        if ctx is not None:
            await ctx.info("Listing Google Drive files.")
        result = await execute_google_request(
            service.files()
            .list(
                q=request.query,
                pageSize=request.page_size,
                pageToken=request.page_token,
                orderBy=request.order_by,
                corpora=request.corpora,
                driveId=request.drive_id,
                includeItemsFromAllDrives=request.include_items_from_all_drives,
                supportsAllDrives=request.supports_all_drives,
                spaces=request.spaces,
                fields=request.fields,
            )
        )
        files = result.get("files", [])
        if ctx is not None:
            await ctx.report_progress(len(files), request.page_size, "Drive files page loaded")
        return {
            "files": files,
            "next_page_token": result.get("nextPageToken"),
            "count": len(files),
        }

    @server.tool(name="get_file")
    async def get_file(
        file_id: str,
        supports_all_drives: bool = True,
        fields: str = "id,name,mimeType,parents,modifiedTime,size,driveId,webViewLink,capabilities",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Get metadata for a single Drive file."""
        request = GetFileRequest(
            file_id=file_id,
            supports_all_drives=supports_all_drives,
            fields=fields,
        )
        service = drive_service()
        if ctx is not None:
            await ctx.info(f"Fetching Drive file {request.file_id}.")
        file_obj = await execute_google_request(
            service.files()
            .get(
                fileId=request.file_id,
                supportsAllDrives=request.supports_all_drives,
                fields=request.fields,
            )
        )
        return {"file": file_obj}

    @server.tool(name="create_folder")
    async def create_folder(
        name: str,
        parent_ids: list[str] | None = None,
        supports_all_drives: bool = True,
        fields: str = "id,name,mimeType,parents,driveId,webViewLink",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create a folder in Drive."""
        request = CreateFolderRequest(
            name=name,
            parent_ids=parent_ids or [],
            supports_all_drives=supports_all_drives,
            fields=fields,
        )
        service = drive_service()
        body: dict[str, Any] = {
            "name": request.name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if request.parent_ids:
            body["parents"] = request.parent_ids
        if ctx is not None:
            await ctx.info(f"Creating Drive folder '{request.name}'.")
        created = await execute_google_request(
            service.files()
            .create(
                body=body,
                supportsAllDrives=request.supports_all_drives,
                fields=request.fields,
            )
        )
        return {"status": "ok", "file": created}

    @server.tool(name="create_file_metadata")
    async def create_file_metadata(
        name: str,
        mime_type: str | None = None,
        parent_ids: list[str] | None = None,
        description: str | None = None,
        app_properties: dict[str, Any] | None = None,
        properties: dict[str, Any] | None = None,
        supports_all_drives: bool = True,
        fields: str = "id,name,mimeType,parents,driveId,webViewLink",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create a Drive file shell (metadata only, no media upload)."""
        request = CreateFileMetadataRequest(
            name=name,
            mime_type=mime_type,
            parent_ids=parent_ids or [],
            description=description,
            app_properties=app_properties,
            properties=properties,
            supports_all_drives=supports_all_drives,
            fields=fields,
        )
        service = drive_service()
        body = _build_metadata_body(request)
        if ctx is not None:
            await ctx.info(f"Creating Drive metadata-only file '{request.name}'.")
        created = await execute_google_request(
            service.files()
            .create(
                body=body,
                supportsAllDrives=request.supports_all_drives,
                fields=request.fields,
            )
        )
        return {"status": "ok", "file": created}

    @server.tool(name="upload_file")
    async def upload_file(
        local_path: str,
        name: str | None = None,
        parent_ids: list[str] | None = None,
        mime_type: str | None = None,
        resumable: bool = True,
        if_exists: Literal["rename", "overwrite", "skip"] = "rename",
        supports_all_drives: bool = True,
        fields: str = "id,name,mimeType,size,parents,driveId,webViewLink",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Upload a local file to Drive."""
        request = UploadFileRequest(
            local_path=local_path,
            name=name,
            parent_ids=parent_ids or [],
            mime_type=mime_type,
            resumable=resumable,
            if_exists=if_exists,
            supports_all_drives=supports_all_drives,
            fields=fields,
        )
        service = drive_service()
        src = Path(request.local_path)
        if not await run_blocking(src.exists):
            raise FileNotFoundError(f"Local file not found: {src}")
        requested_name = request.name or src.name
        existing_file = await run_blocking(
            _find_existing_file_by_name,
            service,
            name=requested_name,
            parent_ids=request.parent_ids,
            supports_all_drives=request.supports_all_drives,
        )
        if existing_file and request.if_exists == "skip":
            if ctx is not None:
                await ctx.info(
                    f"Skipping upload: file '{requested_name}' already exists as {existing_file.get('id')}."
                )
            return {
                "status": "skipped",
                "reason": "file_exists",
                "existing_file": existing_file,
            }
        target_name = requested_name
        if existing_file and request.if_exists == "rename":
            target_name = _renamed_filename(requested_name)
            if ctx is not None:
                await ctx.info(f"File exists; renaming upload target to '{target_name}'.")
        body: dict[str, Any] = {"name": target_name}
        if request.parent_ids:
            body["parents"] = request.parent_ids
        media = media_file_upload(request.local_path, request.mime_type, request.resumable)
        if existing_file and request.if_exists == "overwrite":
            if ctx is not None:
                await ctx.info(f"Overwriting existing Drive file {existing_file.get('id')}.")
            update_request = (
                service.files().update(
                    fileId=existing_file["id"],
                    body={"name": target_name},
                    media_body=media,
                    supportsAllDrives=request.supports_all_drives,
                    fields=request.fields,
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
        if ctx is not None:
            await ctx.info(f"Uploading local file '{src.name}' to Drive.")
        create_request = (
            service.files()
            .create(
                body=body,
                media_body=media,
                supportsAllDrives=request.supports_all_drives,
                fields=request.fields,
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
        name: str | None = None,
        description: str | None = None,
        app_properties: dict[str, Any] | None = None,
        properties: dict[str, Any] | None = None,
        remove_property_keys: list[str] | None = None,
        remove_app_property_keys: list[str] | None = None,
        supports_all_drives: bool = True,
        fields: str = "id,name,mimeType,parents,modifiedTime,size,driveId,webViewLink",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Patch file metadata (name/description/properties)."""
        request = UpdateFileMetadataRequest(
            file_id=file_id,
            name=name,
            description=description,
            app_properties=app_properties,
            properties=properties,
            remove_property_keys=remove_property_keys or [],
            remove_app_property_keys=remove_app_property_keys or [],
            supports_all_drives=supports_all_drives,
            fields=fields,
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
        if ctx is not None:
            await ctx.info(f"Updating metadata for Drive file {request.file_id}.")
        updated = await execute_google_request(
            service.files()
            .update(
                fileId=request.file_id,
                body=body,
                supportsAllDrives=request.supports_all_drives,
                fields=request.fields,
            )
        )
        return {"status": "ok", "file": updated}

    @server.tool(name="update_file_content")
    async def update_file_content(
        file_id: str,
        local_path: str,
        mime_type: str | None = None,
        resumable: bool = True,
        keep_revision_forever: bool | None = None,
        supports_all_drives: bool = True,
        fields: str = "id,name,mimeType,size,parents,driveId,webViewLink,modifiedTime",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Replace file binary content from a local file."""
        request = UpdateFileContentRequest(
            file_id=file_id,
            local_path=local_path,
            mime_type=mime_type,
            resumable=resumable,
            keep_revision_forever=keep_revision_forever,
            supports_all_drives=supports_all_drives,
            fields=fields,
        )
        service = drive_service()
        media = media_file_upload(request.local_path, request.mime_type, request.resumable)
        if ctx is not None:
            await ctx.info(f"Uploading replacement content for file {request.file_id}.")
        update_request = (
            service.files()
            .update(
                fileId=request.file_id,
                media_body=media,
                keepRevisionForever=request.keep_revision_forever,
                supportsAllDrives=request.supports_all_drives,
                fields=request.fields,
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
        add_parent_ids: list[str] | None = None,
        remove_parent_ids: list[str] | None = None,
        supports_all_drives: bool = True,
        fields: str = "id,name,parents,driveId,webViewLink",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Move file by adding/removing parent folders."""
        request = MoveFileRequest(
            file_id=file_id,
            add_parent_ids=add_parent_ids or [],
            remove_parent_ids=remove_parent_ids or [],
            supports_all_drives=supports_all_drives,
            fields=fields,
        )
        service = drive_service()
        if ctx is not None:
            await ctx.info(f"Moving Drive file {request.file_id}.")
        updated = await execute_google_request(
            service.files()
            .update(
                fileId=request.file_id,
                addParents=",".join(request.add_parent_ids) if request.add_parent_ids else None,
                removeParents=",".join(request.remove_parent_ids) if request.remove_parent_ids else None,
                supportsAllDrives=request.supports_all_drives,
                fields=request.fields,
            )
        )
        return {"status": "ok", "file": updated}

    @server.tool(name="copy_file")
    async def copy_file(
        file_id: str,
        name: str | None = None,
        parent_ids: list[str] | None = None,
        description: str | None = None,
        supports_all_drives: bool = True,
        fields: str = "id,name,mimeType,parents,driveId,webViewLink",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Copy a Drive file to a new file."""
        request = CopyFileRequest(
            file_id=file_id,
            name=name,
            parent_ids=parent_ids or [],
            description=description,
            supports_all_drives=supports_all_drives,
            fields=fields,
        )
        service = drive_service()
        body: dict[str, Any] = {}
        if request.name is not None:
            body["name"] = request.name
        if request.parent_ids:
            body["parents"] = request.parent_ids
        if request.description is not None:
            body["description"] = request.description
        if ctx is not None:
            await ctx.info(f"Copying Drive file {request.file_id}.")
        copied = await execute_google_request(
            service.files()
            .copy(
                fileId=request.file_id,
                body=body,
                supportsAllDrives=request.supports_all_drives,
                fields=request.fields,
            )
        )
        return {"status": "ok", "file": copied}

    @server.tool(name="delete_file")
    async def delete_file(
        file_id: str,
        delete_mode: Literal["trash", "permanent"] = "trash",
        confirm_permanent: bool = True,
        supports_all_drives: bool = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Delete a Drive file safely (trash by default, permanent optional)."""
        request = DeleteFileRequest(
            file_id=file_id,
            delete_mode=delete_mode,
            confirm_permanent=confirm_permanent,
            supports_all_drives=supports_all_drives,
        )
        service = drive_service()
        if request.delete_mode == "trash":
            if ctx is not None:
                await ctx.info(f"Moving Drive file {request.file_id} to trash.")
            updated = await execute_google_request(
                service.files().update(
                    fileId=request.file_id,
                    body={"trashed": True},
                    supportsAllDrives=request.supports_all_drives,
                    fields="id,name,trashed,driveId,webViewLink",
                )
            )
            return {"status": "ok", "mode": "trash", "file": updated}
        if request.confirm_permanent:
            if ctx is None:
                raise RuntimeError("delete_file permanent mode requires MCP context for confirmation.")
            response = await ctx.elicit(
                f"Permanently delete Drive file {request.file_id}? This cannot be undone.",
                response_type=bool,  # type: ignore[arg-type]
            )
            if response.action != "accept" or not bool(response.data):
                return {"status": "cancelled", "mode": "permanent"}
        if ctx is not None:
            await ctx.warning(f"Permanently deleting Drive file {request.file_id}.")
        await execute_google_request(
            service.files().delete(
                fileId=request.file_id,
                supportsAllDrives=request.supports_all_drives,
            )
        )
        return {"status": "ok", "mode": "permanent", "file_id": request.file_id}

    @server.tool(name="download_file")
    async def download_file(
        file_id: str,
        output_path: str,
        overwrite: bool = False,
        acknowledge_abuse: bool = False,
        supports_all_drives: bool = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Download binary file content to local path."""
        request = DownloadFileRequest(
            file_id=file_id,
            output_path=output_path,
            overwrite=overwrite,
            acknowledge_abuse=acknowledge_abuse,
            supports_all_drives=supports_all_drives,
        )
        service = drive_service()
        out = Path(request.output_path)
        if await run_blocking(out.exists) and not request.overwrite:
            raise FileExistsError(f"Output path already exists: {out}")
        await run_blocking(out.parent.mkdir, parents=True, exist_ok=True)

        media_req = service.files().get_media(
            fileId=request.file_id,
            acknowledgeAbuse=request.acknowledge_abuse,
            supportsAllDrives=request.supports_all_drives,
        )
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, media_req)
        done = False
        while not done:
            status, done = await run_blocking(downloader.next_chunk)
            if status is not None:
                if ctx is not None:
                    await ctx.report_progress(
                        int(status.progress() * 100),
                        100,
                        "Downloading Drive file",
                    )
        data = buf.getvalue()
        await write_bytes_file(out, data)
        return {
            "status": "ok",
            "file_id": request.file_id,
            "saved_to": str(out),
            "bytes_written": len(data),
        }

    @server.tool(name="export_google_file")
    async def export_google_file(
        file_id: str,
        mime_type: str,
        output_path: str,
        overwrite: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Export Google-native file format (Docs/Sheets/Slides) to local path."""
        request = ExportGoogleFileRequest(
            file_id=file_id,
            mime_type=mime_type,
            output_path=output_path,
            overwrite=overwrite,
        )
        service = drive_service()
        if ctx is not None:
            await ctx.info(f"Exporting Google file {request.file_id} as {request.mime_type}.")
        media_req = service.files().export_media(fileId=request.file_id, mimeType=request.mime_type)
        stream = io.BytesIO()
        downloader = MediaIoBaseDownload(stream, media_req)
        done = False
        while not done:
            status, done = await run_blocking(downloader.next_chunk)
            if status is not None:
                if ctx is not None:
                    await ctx.report_progress(
                        int(status.progress() * 100),
                        100,
                        "Exporting Google-native file",
                    )
        data = stream.getvalue()
        path = await run_blocking(write_bytes_to_path, data, request.output_path, request.overwrite)
        return {
            "status": "ok",
            "file_id": request.file_id,
            "mime_type": request.mime_type,
            "saved_to": str(path),
            "bytes_written": len(data),
        }

    @server.tool(name="get_file_content_capabilities")
    async def get_file_content_capabilities(
        file_id: str,
        supports_all_drives: bool = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Return whether file can be downloaded/exported and available export links."""
        request = GetFileContentCapabilitiesRequest(
            file_id=file_id,
            supports_all_drives=supports_all_drives,
        )
        service = drive_service()
        if ctx is not None:
            await ctx.info(f"Inspecting content capabilities for file {request.file_id}.")
        file_obj = await execute_google_request(
            service.files()
            .get(
                fileId=request.file_id,
                supportsAllDrives=request.supports_all_drives,
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
