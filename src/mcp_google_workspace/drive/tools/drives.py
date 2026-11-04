"""Drive tools for Shared Drives operations."""

from __future__ import annotations

import logging

from typing import Annotated, Any

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ..client import drive_service
from ..schemas import ListDrivesRequest, UpdateDriveRequest

LOGGER = logging.getLogger(__name__)

# Calls act as the user, never with domain-administrator access.
_DRIVE_LIST_FIELDS = "nextPageToken,drives(id,name,hidden,createdTime,orgUnitId,restrictions,capabilities)"
_DRIVE_FIELDS = "id,name,hidden,createdTime,orgUnitId,restrictions,capabilities"


def register(server: FastMCP) -> None:
    @server.tool(name="list_drives")
    async def list_drives(
        drive_id: Annotated[
            str | None,
            (
                "Return only this shared drive, as a one-item list; page_size, page_token and query "
                "are ignored when it is given. Omit to list the user's shared drives."
            ),
        ] = None,
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
        """List the shared drives the user is a member of, or look up one by drive_id. Use it to get a drive_id for drive_list_files; to search the files inside drives use drive_list_files. Returns drives (id, name, hidden, createdTime, orgUnitId, restrictions, capabilities, where capabilities says what the user may do in it), count and next_page_token."""
        request = ListDrivesRequest(
            drive_id=drive_id,
            page_size=page_size,
            page_token=page_token,
            query=query,
        )
        service = drive_service()
        if request.drive_id is not None:
            LOGGER.debug(f"Fetching Shared Drive {request.drive_id}.")
            drive = await execute_google_request(
                service.drives()
                .get(
                    driveId=request.drive_id,
                    useDomainAdminAccess=False,
                    fields=_DRIVE_FIELDS,
                )
            )
            return {"drives": [drive], "next_page_token": None, "count": 1}
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

    @server.tool(name="update_drive")
    async def update_drive(
        drive_id: str,
        hidden: Annotated[
            bool,
            (
                "True hides the shared drive from the user's default Drive view; false shows a hidden "
                "drive again."
            ),
        ],
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Hide a shared drive from the user's default Drive view, or show a hidden one again; membership and content are unchanged. The change affects only this user; find drive IDs and the current hidden state with drive_list_drives. Returns status 'ok' and drive, the shared drive resource Google returns."""
        request = UpdateDriveRequest(drive_id=drive_id, hidden=hidden)
        service = drive_service()
        drives = service.drives()
        if request.hidden:
            LOGGER.debug(f"Hiding Shared Drive {request.drive_id}.")
            drive = await execute_google_request(drives.hide(driveId=request.drive_id))
        else:
            LOGGER.debug(f"Unhiding Shared Drive {request.drive_id}.")
            drive = await execute_google_request(drives.unhide(driveId=request.drive_id))
        return {"status": "ok", "drive": drive}
