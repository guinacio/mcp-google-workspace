"""Drive tools for Shared Drives operations."""

from __future__ import annotations

import logging

from typing import Annotated, Any

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ..client import drive_service
from ..schemas import GetDriveRequest, HideDriveRequest, ListDrivesRequest, UnhideDriveRequest

LOGGER = logging.getLogger(__name__)

# Calls act as the user, never with domain-administrator access.
_DRIVE_LIST_FIELDS = "nextPageToken,drives(id,name,hidden,createdTime,orgUnitId,restrictions,capabilities)"
_DRIVE_FIELDS = "id,name,hidden,createdTime,orgUnitId,restrictions,capabilities"


def register(server: FastMCP) -> None:
    @server.tool(name="list_drives")
    async def list_drives(
        page_size: Annotated[int, "Shared drives per page, 1 to 100."] = 25,
        page_token: str | None = None,
        query: Annotated[
            str | None,
            (
                "Filter in Drive's shared-drive search syntax, e.g. \"name contains 'Finance'\" or "
                "\"hidden = true\"; omit to list every shared drive the user is a member of."
            ),
        ] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List the shared drives the user is a member of. Use it to get a drive_id for drive_list_files (corpora 'drive') or drive_get_drive; to search the files inside drives use drive_list_files. Returns drives (id, name, hidden, createdTime, restrictions, capabilities), count and next_page_token."""
        request = ListDrivesRequest(
            page_size=page_size,
            page_token=page_token,
            query=query,
        )
        service = drive_service()
        LOGGER.debug("Listing Shared Drives.")
        result = await execute_google_request(
            service.drives()
            .list(
                pageSize=request.page_size,
                pageToken=request.page_token,
                q=request.query,
                useDomainAdminAccess=False,
                fields=_DRIVE_LIST_FIELDS,
            )
        )
        items = result.get("drives", [])
        if ctx is not None:
            await ctx.report_progress(len(items), request.page_size, "Shared Drives page loaded")
        return {
            "drives": items,
            "next_page_token": result.get("nextPageToken"),
            "count": len(items),
        }

    @server.tool(name="get_drive")
    async def get_drive(
        drive_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Get the details of one shared drive. To find drive IDs use drive_list_drives. Returns drive (id, name, hidden, createdTime, orgUnitId, restrictions, capabilities), where capabilities says what the user may do in it."""
        request = GetDriveRequest(
            drive_id=drive_id,
        )
        service = drive_service()
        LOGGER.debug(f"Fetching Shared Drive {request.drive_id}.")
        drive = await execute_google_request(
            service.drives()
            .get(
                driveId=request.drive_id,
                useDomainAdminAccess=False,
                fields=_DRIVE_FIELDS,
            )
        )
        return {"drive": drive}

    @server.tool(name="hide_drive")
    async def hide_drive(
        drive_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Hide a shared drive from the user's default Drive view; membership and content are unchanged. The change affects only this user. Returns status 'ok' and drive, the shared drive resource Google returns. Undo with drive_unhide_drive."""
        request = HideDriveRequest(
            drive_id=drive_id,
        )
        service = drive_service()
        LOGGER.debug(f"Hiding Shared Drive {request.drive_id}.")
        drive = await execute_google_request(
            service.drives()
            .hide(
                driveId=request.drive_id,
            )
        )
        return {"status": "ok", "drive": drive}

    @server.tool(name="unhide_drive")
    async def unhide_drive(
        drive_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Show a previously hidden shared drive in the user's default Drive view again. It reverses drive_hide_drive and affects only this user. Returns status 'ok' and drive, the shared drive resource Google returns."""
        request = UnhideDriveRequest(
            drive_id=drive_id,
        )
        service = drive_service()
        LOGGER.debug(f"Unhiding Shared Drive {request.drive_id}.")
        drive = await execute_google_request(
            service.drives()
            .unhide(
                driveId=request.drive_id,
            )
        )
        return {"status": "ok", "drive": drive}
