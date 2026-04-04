"""MCP resources for Google Drive datasets."""

from __future__ import annotations

import json

from fastmcp import FastMCP

from ..common.async_ops import execute_google_request
from .client import drive_service


def register_resources(server: FastMCP) -> None:
    @server.resource("drive://recent", name="drive_recent_files")
    async def drive_recent_files() -> str:
        service = drive_service()
        result = await execute_google_request(
            service.files()
            .list(
                pageSize=25,
                orderBy="modifiedTime desc",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields="files(id,name,mimeType,modifiedTime,driveId,webViewLink),nextPageToken",
            )
        )
        return json.dumps(result, indent=2)

    @server.resource("drive://shared-drives", name="drive_shared_drives")
    async def drive_shared_drives() -> str:
        service = drive_service()
        result = await execute_google_request(
            service.drives()
            .list(
                pageSize=50,
                fields="drives(id,name,hidden,createdTime,orgUnitId),nextPageToken",
            )
        )
        return json.dumps(result, indent=2)

    @server.resource("drive://file/{file_id}", name="drive_file_by_id")
    async def drive_file_by_id(file_id: str) -> str:
        service = drive_service()
        result = await execute_google_request(
            service.files()
            .get(
                fileId=file_id,
                supportsAllDrives=True,
                fields="id,name,mimeType,size,parents,driveId,modifiedTime,owners,webViewLink,webContentLink,capabilities,exportLinks",
            )
        )
        return json.dumps(result, indent=2)
