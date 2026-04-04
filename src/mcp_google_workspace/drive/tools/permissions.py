"""Drive tools for file sharing permissions."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
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
    async def list_permissions(
        file_id: str,
        page_size: int = 50,
        page_token: str | None = None,
        supports_all_drives: bool = True,
        use_domain_admin_access: bool = False,
        fields: str = "nextPageToken,permissions(id,type,role,emailAddress,domain,displayName,deleted,allowFileDiscovery,pendingOwner,expirationTime)",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List permissions on a Drive file."""
        request = ListPermissionsRequest(
            file_id=file_id,
            page_size=page_size,
            page_token=page_token,
            supports_all_drives=supports_all_drives,
            use_domain_admin_access=use_domain_admin_access,
            fields=fields,
        )
        service = drive_service()
        if ctx is not None:
            await ctx.info(f"Listing permissions for file {request.file_id}.")
        result = await execute_google_request(
            service.permissions()
            .list(
                fileId=request.file_id,
                pageSize=request.page_size,
                pageToken=request.page_token,
                supportsAllDrives=request.supports_all_drives,
                useDomainAdminAccess=request.use_domain_admin_access,
                fields=request.fields,
            )
        )
        items = result.get("permissions", [])
        if ctx is not None:
            await ctx.report_progress(len(items), request.page_size, "Permissions page loaded")
        return {
            "permissions": items,
            "next_page_token": result.get("nextPageToken"),
            "count": len(items),
        }

    @server.tool(name="get_permission")
    async def get_permission(
        file_id: str,
        permission_id: str,
        supports_all_drives: bool = True,
        use_domain_admin_access: bool = False,
        fields: str = "id,type,role,emailAddress,domain,displayName,deleted,allowFileDiscovery,pendingOwner,expirationTime",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Get a specific permission on a Drive file."""
        request = GetPermissionRequest(
            file_id=file_id,
            permission_id=permission_id,
            supports_all_drives=supports_all_drives,
            use_domain_admin_access=use_domain_admin_access,
            fields=fields,
        )
        service = drive_service()
        if ctx is not None:
            await ctx.info(f"Fetching permission {request.permission_id} for file {request.file_id}.")
        permission = await execute_google_request(
            service.permissions()
            .get(
                fileId=request.file_id,
                permissionId=request.permission_id,
                supportsAllDrives=request.supports_all_drives,
                useDomainAdminAccess=request.use_domain_admin_access,
                fields=request.fields,
            )
        )
        return {"permission": permission}

    @server.tool(name="create_permission")
    async def create_permission(
        file_id: str,
        type: str,
        role: str,
        email_address: str | None = None,
        domain: str | None = None,
        allow_file_discovery: bool | None = None,
        expiration_time: str | None = None,
        send_notification_email: bool | None = None,
        email_message: str | None = None,
        transfer_ownership: bool | None = None,
        use_domain_admin_access: bool = False,
        supports_all_drives: bool = True,
        fields: str = "id,type,role,emailAddress,domain,displayName,allowFileDiscovery,pendingOwner,expirationTime",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create a sharing permission for a Drive file."""
        request = CreatePermissionRequest(
            file_id=file_id,
            type=type,
            role=role,
            email_address=email_address,
            domain=domain,
            allow_file_discovery=allow_file_discovery,
            expiration_time=expiration_time,
            send_notification_email=send_notification_email,
            email_message=email_message,
            transfer_ownership=transfer_ownership,
            use_domain_admin_access=use_domain_admin_access,
            supports_all_drives=supports_all_drives,
            fields=fields,
        )
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

        if ctx is not None:
            await ctx.warning(
                f"Creating permission {request.type}:{request.role} for file {request.file_id}."
            )
        created = await execute_google_request(
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
        )
        return {"status": "ok", "permission": created}

    @server.tool(name="update_permission")
    async def update_permission(
        file_id: str,
        permission_id: str,
        role: str | None = None,
        allow_file_discovery: bool | None = None,
        expiration_time: str | None = None,
        remove_expiration: bool = False,
        transfer_ownership: bool | None = None,
        use_domain_admin_access: bool = False,
        supports_all_drives: bool = True,
        fields: str = "id,type,role,emailAddress,domain,displayName,allowFileDiscovery,pendingOwner,expirationTime",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Update an existing Drive file permission."""
        request = UpdatePermissionRequest(
            file_id=file_id,
            permission_id=permission_id,
            role=role,
            allow_file_discovery=allow_file_discovery,
            expiration_time=expiration_time,
            remove_expiration=remove_expiration,
            transfer_ownership=transfer_ownership,
            use_domain_admin_access=use_domain_admin_access,
            supports_all_drives=supports_all_drives,
            fields=fields,
        )
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

        if ctx is not None:
            await ctx.warning(
                f"Updating permission {request.permission_id} for file {request.file_id}."
            )
        updated = await execute_google_request(
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
        )
        return {"status": "ok", "permission": updated}

    @server.tool(name="delete_permission")
    async def delete_permission(
        file_id: str,
        permission_id: str,
        use_domain_admin_access: bool = False,
        supports_all_drives: bool = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Delete a file permission."""
        request = DeletePermissionRequest(
            file_id=file_id,
            permission_id=permission_id,
            use_domain_admin_access=use_domain_admin_access,
            supports_all_drives=supports_all_drives,
        )
        service = drive_service()
        if ctx is not None:
            await ctx.warning(
                f"Deleting permission {request.permission_id} from file {request.file_id}."
            )
        await execute_google_request(
            service.permissions().delete(
                fileId=request.file_id,
                permissionId=request.permission_id,
                supportsAllDrives=request.supports_all_drives,
                useDomainAdminAccess=request.use_domain_admin_access,
            )
        )
        return {
            "status": "ok",
            "file_id": request.file_id,
            "permission_id": request.permission_id,
        }
