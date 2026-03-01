"""Drive tools for file sharing permissions."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from ..client import drive_service
from ..schemas import (
    CreatePermissionRequest,
    DeletePermissionRequest,
    GetPermissionRequest,
    ListPermissionsRequest,
    UpdatePermissionRequest,
)


def register(server: FastMCP) -> None:
    @server.tool(name="list_permissions")
    async def list_permissions(request: ListPermissionsRequest, ctx: Context) -> dict[str, Any]:
        """List permissions on a Drive file."""
        service = drive_service()
        await ctx.info(f"Listing permissions for file {request.file_id}.")
        result = (
            service.permissions()
            .list(
                fileId=request.file_id,
                pageSize=request.page_size,
                pageToken=request.page_token,
                supportsAllDrives=request.supports_all_drives,
                useDomainAdminAccess=request.use_domain_admin_access,
                fields=request.fields,
            )
            .execute()
        )
        items = result.get("permissions", [])
        await ctx.report_progress(len(items), request.page_size, "Permissions page loaded")
        return {
            "permissions": items,
            "next_page_token": result.get("nextPageToken"),
            "count": len(items),
        }

    @server.tool(name="get_permission")
    async def get_permission(request: GetPermissionRequest, ctx: Context) -> dict[str, Any]:
        """Get a specific permission on a Drive file."""
        service = drive_service()
        await ctx.info(f"Fetching permission {request.permission_id} for file {request.file_id}.")
        permission = (
            service.permissions()
            .get(
                fileId=request.file_id,
                permissionId=request.permission_id,
                supportsAllDrives=request.supports_all_drives,
                useDomainAdminAccess=request.use_domain_admin_access,
                fields=request.fields,
            )
            .execute()
        )
        return {"permission": permission}

    @server.tool(name="create_permission")
    async def create_permission(request: CreatePermissionRequest, ctx: Context) -> dict[str, Any]:
        """Create a sharing permission for a Drive file."""
        service = drive_service()
        body: dict[str, Any] = {
            "type": request.type,
            "role": request.role,
        }
        if request.email_address is not None:
            body["emailAddress"] = request.email_address
        if request.domain is not None:
            body["domain"] = request.domain
        if request.allow_file_discovery is not None:
            body["allowFileDiscovery"] = request.allow_file_discovery
        if request.expiration_time is not None:
            body["expirationTime"] = request.expiration_time

        await ctx.warning(
            f"Creating permission {request.type}:{request.role} for file {request.file_id}."
        )
        created = (
            service.permissions()
            .create(
                fileId=request.file_id,
                body=body,
                sendNotificationEmail=request.send_notification_email,
                emailMessage=request.email_message,
                transferOwnership=request.transfer_ownership,
                supportsAllDrives=request.supports_all_drives,
                useDomainAdminAccess=request.use_domain_admin_access,
                fields=request.fields,
            )
            .execute()
        )
        return {"status": "ok", "permission": created}

    @server.tool(name="update_permission")
    async def update_permission(request: UpdatePermissionRequest, ctx: Context) -> dict[str, Any]:
        """Update an existing Drive file permission."""
        service = drive_service()
        body: dict[str, Any] = {}
        if request.role is not None:
            body["role"] = request.role
        if request.allow_file_discovery is not None:
            body["allowFileDiscovery"] = request.allow_file_discovery
        if request.expiration_time is not None:
            body["expirationTime"] = request.expiration_time
        if request.remove_expiration:
            body["expirationTime"] = None

        await ctx.warning(
            f"Updating permission {request.permission_id} for file {request.file_id}."
        )
        updated = (
            service.permissions()
            .update(
                fileId=request.file_id,
                permissionId=request.permission_id,
                body=body,
                transferOwnership=request.transfer_ownership,
                supportsAllDrives=request.supports_all_drives,
                useDomainAdminAccess=request.use_domain_admin_access,
                fields=request.fields,
            )
            .execute()
        )
        return {"status": "ok", "permission": updated}

    @server.tool(name="delete_permission")
    async def delete_permission(request: DeletePermissionRequest, ctx: Context) -> dict[str, Any]:
        """Delete a file permission."""
        service = drive_service()
        await ctx.warning(
            f"Deleting permission {request.permission_id} from file {request.file_id}."
        )
        service.permissions().delete(
            fileId=request.file_id,
            permissionId=request.permission_id,
            supportsAllDrives=request.supports_all_drives,
            useDomainAdminAccess=request.use_domain_admin_access,
        ).execute()
        return {
            "status": "ok",
            "file_id": request.file_id,
            "permission_id": request.permission_id,
        }
