"""Drive tools for files/folders/content operations."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from fastmcp import Context, FastMCP
from googleapiclient.http import MediaIoBaseDownload

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


def register(server: FastMCP) -> None:
    @server.tool(name="list_files")
    async def list_files(request: ListFilesRequest, ctx: Context) -> dict[str, Any]:
        """List/search files with Shared Drives-aware query options."""
        service = drive_service()
        await ctx.info("Listing Google Drive files.")
        result = (
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
            .execute()
        )
        files = result.get("files", [])
        await ctx.report_progress(len(files), request.page_size, "Drive files page loaded")
        return {
            "files": files,
            "next_page_token": result.get("nextPageToken"),
            "count": len(files),
        }

    @server.tool(name="get_file")
    async def get_file(request: GetFileRequest, ctx: Context) -> dict[str, Any]:
        """Get metadata for a single Drive file."""
        service = drive_service()
        await ctx.info(f"Fetching Drive file {request.file_id}.")
        file_obj = (
            service.files()
            .get(
                fileId=request.file_id,
                supportsAllDrives=request.supports_all_drives,
                fields=request.fields,
            )
            .execute()
        )
        return {"file": file_obj}

    @server.tool(name="create_folder")
    async def create_folder(request: CreateFolderRequest, ctx: Context) -> dict[str, Any]:
        """Create a folder in Drive."""
        service = drive_service()
        body: dict[str, Any] = {
            "name": request.name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if request.parent_ids:
            body["parents"] = request.parent_ids
        await ctx.info(f"Creating Drive folder '{request.name}'.")
        created = (
            service.files()
            .create(
                body=body,
                supportsAllDrives=request.supports_all_drives,
                fields=request.fields,
            )
            .execute()
        )
        return {"status": "ok", "file": created}

    @server.tool(name="create_file_metadata")
    async def create_file_metadata(request: CreateFileMetadataRequest, ctx: Context) -> dict[str, Any]:
        """Create a Drive file shell (metadata only, no media upload)."""
        service = drive_service()
        body = _build_metadata_body(request)
        await ctx.info(f"Creating Drive metadata-only file '{request.name}'.")
        created = (
            service.files()
            .create(
                body=body,
                supportsAllDrives=request.supports_all_drives,
                fields=request.fields,
            )
            .execute()
        )
        return {"status": "ok", "file": created}

    @server.tool(name="upload_file")
    async def upload_file(request: UploadFileRequest, ctx: Context) -> dict[str, Any]:
        """Upload a local file to Drive."""
        service = drive_service()
        src = Path(request.local_path)
        if not src.exists():
            raise FileNotFoundError(f"Local file not found: {src}")
        body: dict[str, Any] = {"name": request.name or src.name}
        if request.parent_ids:
            body["parents"] = request.parent_ids
        media = media_file_upload(request.local_path, request.mime_type, request.resumable)
        await ctx.info(f"Uploading local file '{src.name}' to Drive.")
        created = (
            service.files()
            .create(
                body=body,
                media_body=media,
                supportsAllDrives=request.supports_all_drives,
                fields=request.fields,
            )
            .execute()
        )
        return {"status": "ok", "file": created}

    @server.tool(name="update_file_metadata")
    async def update_file_metadata(request: UpdateFileMetadataRequest, ctx: Context) -> dict[str, Any]:
        """Patch file metadata (name/description/properties)."""
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
        await ctx.info(f"Updating metadata for Drive file {request.file_id}.")
        updated = (
            service.files()
            .update(
                fileId=request.file_id,
                body=body,
                supportsAllDrives=request.supports_all_drives,
                fields=request.fields,
            )
            .execute()
        )
        return {"status": "ok", "file": updated}

    @server.tool(name="update_file_content")
    async def update_file_content(request: UpdateFileContentRequest, ctx: Context) -> dict[str, Any]:
        """Replace file binary content from a local file."""
        service = drive_service()
        media = media_file_upload(request.local_path, request.mime_type, request.resumable)
        await ctx.info(f"Uploading replacement content for file {request.file_id}.")
        updated = (
            service.files()
            .update(
                fileId=request.file_id,
                media_body=media,
                keepRevisionForever=request.keep_revision_forever,
                supportsAllDrives=request.supports_all_drives,
                fields=request.fields,
            )
            .execute()
        )
        return {"status": "ok", "file": updated}

    @server.tool(name="move_file")
    async def move_file(request: MoveFileRequest, ctx: Context) -> dict[str, Any]:
        """Move file by adding/removing parent folders."""
        service = drive_service()
        await ctx.info(f"Moving Drive file {request.file_id}.")
        updated = (
            service.files()
            .update(
                fileId=request.file_id,
                addParents=",".join(request.add_parent_ids) if request.add_parent_ids else None,
                removeParents=",".join(request.remove_parent_ids) if request.remove_parent_ids else None,
                supportsAllDrives=request.supports_all_drives,
                fields=request.fields,
            )
            .execute()
        )
        return {"status": "ok", "file": updated}

    @server.tool(name="copy_file")
    async def copy_file(request: CopyFileRequest, ctx: Context) -> dict[str, Any]:
        """Copy a Drive file to a new file."""
        service = drive_service()
        body: dict[str, Any] = {}
        if request.name is not None:
            body["name"] = request.name
        if request.parent_ids:
            body["parents"] = request.parent_ids
        if request.description is not None:
            body["description"] = request.description
        await ctx.info(f"Copying Drive file {request.file_id}.")
        copied = (
            service.files()
            .copy(
                fileId=request.file_id,
                body=body,
                supportsAllDrives=request.supports_all_drives,
                fields=request.fields,
            )
            .execute()
        )
        return {"status": "ok", "file": copied}

    @server.tool(name="delete_file")
    async def delete_file(request: DeleteFileRequest, ctx: Context) -> dict[str, Any]:
        """Permanently delete a Drive file."""
        service = drive_service()
        await ctx.warning(f"Deleting Drive file {request.file_id}.")
        service.files().delete(
            fileId=request.file_id,
            supportsAllDrives=request.supports_all_drives,
        ).execute()
        return {"status": "ok", "file_id": request.file_id}

    @server.tool(name="download_file")
    async def download_file(request: DownloadFileRequest, ctx: Context) -> dict[str, Any]:
        """Download binary file content to local path."""
        service = drive_service()
        out = Path(request.output_path)
        if out.exists() and not request.overwrite:
            raise FileExistsError(f"Output path already exists: {out}")
        out.parent.mkdir(parents=True, exist_ok=True)

        media_req = service.files().get_media(
            fileId=request.file_id,
            acknowledgeAbuse=request.acknowledge_abuse,
            supportsAllDrives=request.supports_all_drives,
        )
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, media_req)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status is not None:
                await ctx.report_progress(
                    int(status.progress() * 100),
                    100,
                    "Downloading Drive file",
                )
        data = buf.getvalue()
        out.write_bytes(data)
        return {
            "status": "ok",
            "file_id": request.file_id,
            "saved_to": str(out),
            "bytes_written": len(data),
        }

    @server.tool(name="export_google_file")
    async def export_google_file(request: ExportGoogleFileRequest, ctx: Context) -> dict[str, Any]:
        """Export Google-native file format (Docs/Sheets/Slides) to local path."""
        service = drive_service()
        await ctx.info(f"Exporting Google file {request.file_id} as {request.mime_type}.")
        media_req = service.files().export_media(fileId=request.file_id, mimeType=request.mime_type)
        stream = io.BytesIO()
        downloader = MediaIoBaseDownload(stream, media_req)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status is not None:
                await ctx.report_progress(
                    int(status.progress() * 100),
                    100,
                    "Exporting Google-native file",
                )
        path = write_bytes_to_path(stream.getvalue(), request.output_path, request.overwrite)
        return {
            "status": "ok",
            "file_id": request.file_id,
            "mime_type": request.mime_type,
            "saved_to": str(path),
            "bytes_written": path.stat().st_size,
        }

    @server.tool(name="get_file_content_capabilities")
    async def get_file_content_capabilities(
        request: GetFileContentCapabilitiesRequest,
        ctx: Context,
    ) -> dict[str, Any]:
        """Return whether file can be downloaded/exported and available export links."""
        service = drive_service()
        await ctx.info(f"Inspecting content capabilities for file {request.file_id}.")
        file_obj = (
            service.files()
            .get(
                fileId=request.file_id,
                supportsAllDrives=request.supports_all_drives,
                fields="id,name,mimeType,size,capabilities(canDownload,canEdit),webContentLink,exportLinks",
            )
            .execute()
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
