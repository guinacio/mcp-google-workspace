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
        page_size: Annotated[int, "Permissions per page, 1 to 200."] = 50,
        page_token: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List who has access to a Drive file or folder and at what level. Use it before drive_update_permission or drive_delete_permission to find the permission_id; for one known permission use drive_get_permission. Returns permissions (id, type, role, emailAddress, domain, displayName, allowFileDiscovery, pendingOwner, expirationTime, deleted), count and next_page_token."""
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
        """Get one sharing permission on a Drive file by its permission ID. To see everyone with access use drive_list_permissions. Returns permission (id, type, role, emailAddress, domain, displayName, allowFileDiscovery, pendingOwner, expirationTime, deleted)."""
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
            (
                "Who receives access: 'user' or 'group' (set email_address), 'domain' (everyone in a "
                "Google Workspace domain; set domain), or 'anyone' (anyone with the link, no sign-in; "
                "requires prepare_workspace_action first)."
            ),
        ],
        role: Literal["owner", "organizer", "fileOrganizer", "writer", "commenter", "reader"],
        email_address: Annotated[
            str | None,
            "Email address of the person or Google group to share with; required for type user or group.",
        ] = None,
        domain: Annotated[
            str | None, "Domain to share with, e.g. 'example.com'; required for type 'domain'."
        ] = None,
        allow_file_discovery: bool | None = None,
        expiration_time: str | None = None,
        send_notification_email: Annotated[
            bool | None,
            (
                "For type user or group: false shares silently. Omitted, Google emails the grantee; it "
                "cannot be false for an ownership transfer."
            ),
        ] = None,
        email_message: Annotated[
            str | None,
            "Plain-text message added to the sharing notification email sent to a user or group.",
        ] = None,
        transfer_ownership: bool | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Share a Drive file or folder with a person, group, domain or anyone with the link. To change an existing grant's role or expiry use drive_update_permission, and to revoke access drive_delete_permission. Returns status 'ok' and permission (id, type, role, emailAddress, domain, displayName, expirationTime, pendingOwner), or status 'cancelled'. It asks the user to confirm, Google emails user and group grantees by default, and type 'anyone' first requires prepare_workspace_action, then commit_workspace_action."""
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
        remove_expiration: Annotated[
            bool, "True clears the grant's expiration so access no longer ends; overrides expiration_time."
        ] = False,
        transfer_ownership: bool | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Change an existing sharing grant on a Drive file: its role, expiry or discoverability. The grantee cannot be changed; to share with someone new use drive_create_permission and to revoke access drive_delete_permission, with permission_id from drive_list_permissions. Returns status 'ok' and permission (id, type, role, emailAddress, domain, displayName, expirationTime, pendingOwner), or status 'cancelled'. It asks the user to confirm first."""
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
        """Revoke one person's, group's, domain's or link's access to a Drive file or folder. Find the permission_id with drive_list_permissions; to lower access instead of removing it use drive_update_permission. Returns status 'ok' with file_id and permission_id, or status 'cancelled'. It asks the user to confirm first; undo by granting access again with drive_create_permission."""
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
