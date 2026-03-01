"""Drive tools for Shared Drives operations."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from ..client import drive_service
from ..schemas import GetDriveRequest, HideDriveRequest, ListDrivesRequest, UnhideDriveRequest


def register(server: FastMCP) -> None:
    @server.tool(name="list_drives")
    async def list_drives(request: ListDrivesRequest, ctx: Context) -> dict[str, Any]:
        """List Shared Drives visible to the authenticated account."""
        service = drive_service()
        await ctx.info("Listing Shared Drives.")
        result = (
            service.drives()
            .list(
                pageSize=request.page_size,
                pageToken=request.page_token,
                q=request.query,
                useDomainAdminAccess=request.use_domain_admin_access,
                fields=request.fields,
            )
            .execute()
        )
        items = result.get("drives", [])
        await ctx.report_progress(len(items), request.page_size, "Shared Drives page loaded")
        return {
            "drives": items,
            "next_page_token": result.get("nextPageToken"),
            "count": len(items),
        }

    @server.tool(name="get_drive")
    async def get_drive(request: GetDriveRequest, ctx: Context) -> dict[str, Any]:
        """Get metadata for one Shared Drive."""
        service = drive_service()
        await ctx.info(f"Fetching Shared Drive {request.drive_id}.")
        drive = (
            service.drives()
            .get(
                driveId=request.drive_id,
                useDomainAdminAccess=request.use_domain_admin_access,
                fields=request.fields,
            )
            .execute()
        )
        return {"drive": drive}

    @server.tool(name="hide_drive")
    async def hide_drive(request: HideDriveRequest, ctx: Context) -> dict[str, Any]:
        """Hide a Shared Drive from the Drive UI."""
        service = drive_service()
        await ctx.warning(f"Hiding Shared Drive {request.drive_id}.")
        drive = (
            service.drives()
            .hide(
                driveId=request.drive_id,
                useDomainAdminAccess=request.use_domain_admin_access,
            )
            .execute()
        )
        return {"status": "ok", "drive": drive}

    @server.tool(name="unhide_drive")
    async def unhide_drive(request: UnhideDriveRequest, ctx: Context) -> dict[str, Any]:
        """Unhide a Shared Drive in the Drive UI."""
        service = drive_service()
        await ctx.warning(f"Unhiding Shared Drive {request.drive_id}.")
        drive = (
            service.drives()
            .unhide(
                driveId=request.drive_id,
                useDomainAdminAccess=request.use_domain_admin_access,
            )
            .execute()
        )
        return {"status": "ok", "drive": drive}
