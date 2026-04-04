"""Drive tools for Shared Drives operations."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ..client import drive_service
from ..schemas import GetDriveRequest, HideDriveRequest, ListDrivesRequest, UnhideDriveRequest


def register(server: FastMCP) -> None:
    @server.tool(name="list_drives")
    async def list_drives(
        page_size: int = 25,
        page_token: str | None = None,
        query: str | None = None,
        use_domain_admin_access: bool = False,
        fields: str = "nextPageToken,drives(id,name,hidden,createdTime,orgUnitId,restrictions,capabilities)",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List Shared Drives visible to the authenticated account."""
        request = ListDrivesRequest(
            page_size=page_size,
            page_token=page_token,
            query=query,
            use_domain_admin_access=use_domain_admin_access,
            fields=fields,
        )
        service = drive_service()
        if ctx is not None:
            await ctx.info("Listing Shared Drives.")
        result = await execute_google_request(
            service.drives()
            .list(
                pageSize=request.page_size,
                pageToken=request.page_token,
                q=request.query,
                useDomainAdminAccess=request.use_domain_admin_access,
                fields=request.fields,
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
        use_domain_admin_access: bool = False,
        fields: str = "id,name,hidden,createdTime,orgUnitId,restrictions,capabilities",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Get metadata for one Shared Drive."""
        request = GetDriveRequest(
            drive_id=drive_id,
            use_domain_admin_access=use_domain_admin_access,
            fields=fields,
        )
        service = drive_service()
        if ctx is not None:
            await ctx.info(f"Fetching Shared Drive {request.drive_id}.")
        drive = await execute_google_request(
            service.drives()
            .get(
                driveId=request.drive_id,
                useDomainAdminAccess=request.use_domain_admin_access,
                fields=request.fields,
            )
        )
        return {"drive": drive}

    @server.tool(name="hide_drive")
    async def hide_drive(
        drive_id: str,
        use_domain_admin_access: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Hide a Shared Drive from the Drive UI."""
        request = HideDriveRequest(
            drive_id=drive_id,
            use_domain_admin_access=use_domain_admin_access,
        )
        service = drive_service()
        if ctx is not None:
            await ctx.warning(f"Hiding Shared Drive {request.drive_id}.")
        drive = await execute_google_request(
            service.drives()
            .hide(
                driveId=request.drive_id,
                useDomainAdminAccess=request.use_domain_admin_access,
            )
        )
        return {"status": "ok", "drive": drive}

    @server.tool(name="unhide_drive")
    async def unhide_drive(
        drive_id: str,
        use_domain_admin_access: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Unhide a Shared Drive in the Drive UI."""
        request = UnhideDriveRequest(
            drive_id=drive_id,
            use_domain_admin_access=use_domain_admin_access,
        )
        service = drive_service()
        if ctx is not None:
            await ctx.warning(f"Unhiding Shared Drive {request.drive_id}.")
        drive = await execute_google_request(
            service.drives()
            .unhide(
                driveId=request.drive_id,
                useDomainAdminAccess=request.use_domain_admin_access,
            )
        )
        return {"status": "ok", "drive": drive}
