"""Drive tools for file sharing permissions."""

from __future__ import annotations

import logging

from typing import Annotated, Any

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ..client import drive_service
from ..schemas import (
    CreatePermissionRequest,
    DeletePermissionRequest,
    ListPermissionsRequest,
    PermissionRole,
    PermissionType,
    UpdatePermissionRequest,
)

LOGGER = logging.getLogger(__name__)

# Shared drives are always in scope and calls act as the user, never with
# domain-administrator access; the fields are the ones each response returns.
_PERMISSION_LIST_FIELDS = "nextPageToken,permissions(id,type,role,emailAddress,domain,displayName,deleted,allowFileDiscovery,pendingOwner,expirationTime)"
_PERMISSION_FIELDS = "id,type,role,emailAddress,domain,displayName,deleted,allowFileDiscovery,pendingOwner,expirationTime"
_WRITTEN_PERMISSION_FIELDS = "id,type,role,emailAddress,domain,displayName,allowFileDiscovery,pendingOwner,expirationTime"

# Parameters that describe a new grant; an existing grant's grantee and
# notification cannot change, so update mode rejects them.
_CREATE_ONLY_PARAMETERS = ("type", "email_address", "domain", "email_message", "send_notification_email")


async def _create_permission(request: CreatePermissionRequest) -> dict[str, Any]:
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
    return {"status": "ok", "mode": "created", "permission": created}


async def _update_permission(request: UpdatePermissionRequest) -> dict[str, Any]:
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
    return {"status": "ok", "mode": "updated", "permission": updated}


def register(server: FastMCP) -> None:
    @server.tool(name="list_permissions")
    async def list_permissions(
        file_id: str,
        permission_id: Annotated[
            str | None,
            (
                "Return only this permission, as a one-item list; page_size and page_token are "
                "ignored when it is given. Omit to list every grant on the file."
            ),
        ] = None,
        page_size: Annotated[int, "Permissions per page, 1 to 200."] = 50,
        page_token: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List who has access to a Drive file or folder and at what level, or look up one grant by permission_id. Use it before drive_share_file (update mode) or drive_unshare_file to find the permission_id. Returns permissions (id, type, role, emailAddress, domain, displayName, allowFileDiscovery, pendingOwner, expirationTime, deleted), count and next_page_token."""
        request = ListPermissionsRequest(
            file_id=file_id,
            permission_id=permission_id,
            page_size=page_size,
            page_token=page_token,
        )
        service = drive_service()
        if request.permission_id is not None:
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
            return {"permissions": [permission], "next_page_token": None, "count": 1}  # nosec B105 -- no next page, not a credential
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

    @server.tool(name="share_file")
    async def share_file(
        file_id: str,
        permission_id: Annotated[
            str | None,
            (
                "Existing grant to change, from drive_list_permissions; selects update mode, where "
                "type, email_address, domain, email_message and send_notification_email are rejected "
                "because a grant's grantee cannot change. Omit to create a new grant."
            ),
        ] = None,
        type: Annotated[
            PermissionType | None,
            (
                "Who receives access, required to create a grant: 'user' or 'group' (set "
                "email_address), 'domain' (everyone in a Google Workspace domain; set domain), or "
                "'anyone' (anyone with the link, no sign-in)."
            ),
        ] = None,
        role: Annotated[
            PermissionRole | None,
            (
                "Access level: reader, commenter, writer, fileOrganizer or organizer (shared drives "
                "only), or owner (ownership transfer). Required to create a grant; in update mode omit "
                "it to keep the current role."
            ),
        ] = None,
        email_address: Annotated[
            str | None,
            "Email address of the person or Google group to share with; required for type user or group.",
        ] = None,
        domain: Annotated[
            str | None, "Domain to share with, e.g. 'example.com'; required for type 'domain'."
        ] = None,
        allow_file_discovery: bool | None = None,
        expiration_time: str | None = None,
        remove_expiration: Annotated[
            bool,
            (
                "Update mode only: true clears the grant's expiration so access no longer ends; "
                "overrides expiration_time."
            ),
        ] = False,
        send_notification_email: Annotated[
            bool | None,
            (
                "Create mode, type user or group: false shares silently. Omitted, Google emails the "
                "grantee; it cannot be false for an ownership transfer."
            ),
        ] = None,
        email_message: Annotated[
            str | None,
            "Create mode: plain-text message added to the sharing notification email sent to a user or group.",
        ] = None,
        transfer_ownership: bool | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Share a Drive file or folder with a person, group, domain or anyone with the link, or with permission_id change an existing grant's role, expiry or discoverability. To revoke access use drive_unshare_file; find permission_id with drive_list_permissions. Returns status 'ok', mode 'created' or 'updated' and permission (id, type, role, emailAddress, domain, displayName, expirationTime, pendingOwner), or status 'cancelled' if the user declines. Every share or change asks the user to confirm first, shown who gets which access; Google emails new user and group grantees by default."""
        if permission_id is not None:
            rejected = [
                name
                for name, value in (
                    ("type", type),
                    ("email_address", email_address),
                    ("domain", domain),
                    ("email_message", email_message),
                    ("send_notification_email", send_notification_email),
                )
                if value is not None
            ]
            if rejected:
                raise ValueError(
                    f"{', '.join(rejected)} cannot be given with permission_id: updating a grant changes only "
                    "role, expiration_time, remove_expiration, allow_file_discovery and transfer_ownership. "
                    "To share with someone new, omit permission_id."
                )
            return await _update_permission(
                UpdatePermissionRequest(
                    file_id=file_id,
                    permission_id=permission_id,
                    role=role,
                    allow_file_discovery=allow_file_discovery,
                    expiration_time=expiration_time,
                    remove_expiration=remove_expiration,
                    transfer_ownership=transfer_ownership,
                ),
            )
        if remove_expiration:
            raise ValueError(
                "remove_expiration applies only with permission_id, to clear an existing grant's expiration."
            )
        if type is None or role is None:
            missing = [name for name, value in (("type", type), ("role", role)) if value is None]
            raise ValueError(
                f"drive_share_file needs {' and '.join(missing)} to create a grant; "
                "pass permission_id to update an existing one instead."
            )
        return await _create_permission(
            CreatePermissionRequest(
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
            ),
        )

    @server.tool(name="unshare_file")
    async def unshare_file(
        file_id: str,
        permission_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Revoke one person's, group's, domain's or link's access to a Drive file or folder. Find the permission_id with drive_list_permissions; to lower access instead of removing it use drive_share_file with permission_id. Returns status 'ok' with file_id and permission_id, or status 'cancelled' if the user declines. It asks the user to confirm first; undo by granting access again with drive_share_file."""
        request = DeletePermissionRequest(
            file_id=file_id,
            permission_id=permission_id,
        )
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
