"""Drive tools for file sharing permissions."""

from __future__ import annotations

import logging

from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ...common.confirmation import confirm_destructive_action
from ..client import drive_service
from ..schemas import (
    CreatePermissionRequest,
    DeletePermissionRequest,
    GetPermissionRequest,
    ListPermissionsRequest,
    UpdatePermissionRequest,
)

LOGGER = logging.getLogger(__name__)

# Shared drives are always in scope and calls act as the user, never with
# domain-administrator access; the fields are the ones each response returns.
_PERMISSION_LIST_FIELDS = "nextPageToken,permissions(id,type,role,emailAddress,domain,displayName,deleted,allowFileDiscovery,pendingOwner,expirationTime)"
_PERMISSION_FIELDS = "id,type,role,emailAddress,domain,displayName,deleted,allowFileDiscovery,pendingOwner,expirationTime"
_WRITTEN_PERMISSION_FIELDS = "id,type,role,emailAddress,domain,displayName,allowFileDiscovery,pendingOwner,expirationTime"


def register(server: FastMCP) -> None:
    @server.tool(name="list_permissions")
    async def list_permissions(
        file_id: str,
        page_size: int = 50,
        page_token: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List permissions on a Drive file."""
        request = ListPermissionsRequest(
            file_id=file_id,
            page_size=page_size,
            page_token=page_token,
        )
        service = drive_service()
        LOGGER.debug(f"Listing permissions for file {request.file_id}.")
        result = await execute_google_request(
            service.permissions()
            .list(
                fileId=request.file_id,
                pageSize=request.page_size,
                pageToken=request.page_token,
                supportsAllDrives=True,
                useDomainAdminAccess=False,
                fields=_PERMISSION_LIST_FIELDS,
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
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Get a specific permission on a Drive file."""
        request = GetPermissionRequest(
            file_id=file_id,
            permission_id=permission_id,
        )
        service = drive_service()
        LOGGER.debug(f"Fetching permission {request.permission_id} for file {request.file_id}.")
        permission = await execute_google_request(
            service.permissions()
            .get(
                fileId=request.file_id,
                permissionId=request.permission_id,
                supportsAllDrives=True,
                useDomainAdminAccess=False,
                fields=_PERMISSION_FIELDS,
            )
        )
        return {"permission": permission}

    @server.tool(name="create_permission")
    async def create_permission(
        file_id: str,
        type: Annotated[
            Literal["user", "group", "domain", "anyone"],
            "Permission grantee type: user, group, domain, or anyone.",
        ],
        role: Annotated[
            Literal["owner", "organizer", "fileOrganizer", "writer", "commenter", "reader"],
            "Access level granted to the grantee, from owner down to reader.",
        ],
        email_address: Annotated[str | None, "Email address for user/group permission types."] = None,
        domain: Annotated[str | None, "Domain name for the domain permission type."] = None,
        allow_file_discovery: bool | None = None,
        expiration_time: str | None = None,
        send_notification_email: bool | None = None,
        email_message: Annotated[
            str | None, "Custom message included in the sharing notification email."
        ] = None,
        transfer_ownership: bool | None = None,
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
        )
        grantee = request.email_address or request.domain or request.type
        create_details = [f"role {request.role}"]
        if request.allow_file_discovery is not None:
            create_details.append(
                f"file discovery {'enabled' if request.allow_file_discovery else 'disabled'}"
            )
        if request.expiration_time is not None:
            create_details.append(f"expiration {request.expiration_time}")
        if request.transfer_ownership:
            create_details.append("transfer ownership")
        if not await confirm_destructive_action(
            ctx,
            "create_permission",
            (
                f"Grant access to {grantee} on Drive file {request.file_id} "
                f"with {', '.join(create_details)}?"
            ),
        ):
            return {
                "status": "cancelled",
                "file_id": request.file_id,
                "grantee": grantee,
                "role": request.role,
            }
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

        LOGGER.debug(
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
                supportsAllDrives=True,
                useDomainAdminAccess=False,
                fields=_WRITTEN_PERMISSION_FIELDS,
            )
        )
        return {"status": "ok", "permission": created}

    @server.tool(name="update_permission")
    async def update_permission(
        file_id: str,
        permission_id: str,
        role: Literal["owner", "organizer", "fileOrganizer", "writer", "commenter", "reader"] | None = None,
        allow_file_discovery: bool | None = None,
        expiration_time: str | None = None,
        remove_expiration: bool = False,
        transfer_ownership: bool | None = None,
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
        )
        update_details: list[str] = []
        if request.role is not None:
            update_details.append(f"set role to {request.role}")
        if request.allow_file_discovery is not None:
            update_details.append(
                f"set file discovery to {request.allow_file_discovery}"
            )
        if request.expiration_time is not None:
            update_details.append(f"set expiration to {request.expiration_time}")
        if request.remove_expiration:
            update_details.append("remove expiration")
        if request.transfer_ownership:
            update_details.append("transfer ownership")
        change_summary = ", ".join(update_details) or "apply the requested changes"
        if not await confirm_destructive_action(
            ctx,
            "update_permission",
            (
                f"Update permission {request.permission_id} "
                f"on Drive file {request.file_id}: {change_summary}?"
            ),
        ):
            return {
                "status": "cancelled",
                "file_id": request.file_id,
                "permission_id": request.permission_id,
            }
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

        LOGGER.debug(
            f"Updating permission {request.permission_id} for file {request.file_id}."
        )
        updated = await execute_google_request(
            service.permissions()
            .update(
                fileId=request.file_id,
                permissionId=request.permission_id,
                body=body,
                transferOwnership=request.transfer_ownership,
                supportsAllDrives=True,
                useDomainAdminAccess=False,
                fields=_WRITTEN_PERMISSION_FIELDS,
            )
        )
        return {"status": "ok", "permission": updated}

    @server.tool(name="delete_permission")
    async def delete_permission(
        file_id: str,
        permission_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Delete a file permission."""
        request = DeletePermissionRequest(
            file_id=file_id,
            permission_id=permission_id,
        )
        if not await confirm_destructive_action(
            ctx,
            "delete_permission",
            f"Remove permission {request.permission_id} from Drive file {request.file_id}?",
        ):
            return {
                "status": "cancelled",
                "file_id": request.file_id,
                "permission_id": request.permission_id,
            }
        service = drive_service()
        LOGGER.debug(
            f"Deleting permission {request.permission_id} from file {request.file_id}."
        )
        await execute_google_request(
            service.permissions().delete(
                fileId=request.file_id,
                permissionId=request.permission_id,
                supportsAllDrives=True,
                useDomainAdminAccess=False,
            )
        )
        return {
            "status": "ok",
            "file_id": request.file_id,
            "permission_id": request.permission_id,
        }
