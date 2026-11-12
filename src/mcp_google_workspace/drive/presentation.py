"""Compact Drive metadata envelopes."""

from __future__ import annotations

from typing import Any

from ..common.resources import clean_web_url
from ..common.timezone import in_account_timezone


def file_kind(mime_type: str | None) -> str:
    mapping = {
        "application/vnd.google-apps.folder": "folder",
        "application/vnd.google-apps.document": "document",
        "application/vnd.google-apps.spreadsheet": "spreadsheet",
        "application/vnd.google-apps.presentation": "presentation",
        "application/vnd.google-apps.form": "form",
    }
    if mime_type in mapping:
        return mapping[mime_type]
    if (mime_type or "").startswith("image/"):
        return "image"
    if (mime_type or "").startswith("video/"):
        return "video"
    if (mime_type or "").startswith("audio/"):
        return "audio"
    return "file"


def _person(person: dict[str, Any] | None) -> dict[str, Any] | None:
    if not person:
        return None
    return {"name": person.get("displayName"), "email": person.get("emailAddress")}


def _people(people: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if people is None:
        return None
    return [{"name": person.get("displayName"), "email": person.get("emailAddress")} for person in people]


def _size_bytes(file: dict[str, Any]) -> int | None:
    """Drive sends int64 values as strings; ``size`` keeps that string, ``size_bytes`` is the number."""
    size = file.get("size")
    try:
        return int(size) if size is not None else None
    except (TypeError, ValueError):
        return None


def file_envelope(file: dict[str, Any], *, account_timezone: str) -> dict[str, Any]:
    """Present file metadata suitable for selecting the next action.

    A field Google did not return (not in the requested ``fields`` mask, or
    not applicable, such as ``owners`` on a shared-drive file) is ``null``:
    the envelope must never report ``false`` or an empty list as a fact the
    request did not fetch.
    """
    capabilities = file.get("capabilities", {})
    return {
        "id": file.get("id"),
        "name": file.get("name"),
        "kind": file_kind(file.get("mimeType")),
        "mime_type": file.get("mimeType"),
        "size": file.get("size"),
        "size_bytes": _size_bytes(file),
        "created_at": in_account_timezone(file.get("createdTime"), account_timezone),
        "modified_at": in_account_timezone(file.get("modifiedTime"), account_timezone),
        "description": file.get("description"),
        "owners": _people(file.get("owners")),
        "last_modified_by": _person(file.get("lastModifyingUser")),
        "parent_ids": file.get("parents"),
        "drive_id": file.get("driveId"),
        "web_url": clean_web_url(file.get("webViewLink")),
        "shared": file.get("shared"),
        "starred": file.get("starred"),
        "trashed": file.get("trashed"),
        "owned_by_me": file.get("ownedByMe"),
        "can_edit": capabilities.get("canEdit"),
        "can_download": capabilities.get("canDownload"),
    }


def files_page(payload: dict[str, Any], *, account_timezone: str) -> dict[str, Any]:
    """files.list as one page of file envelopes."""
    files = [file_envelope(file, account_timezone=account_timezone) for file in payload.get("files", [])]
    return {
        "files": files,
        "count": len(files),
        "next_page_token": payload.get("nextPageToken"),
        "account_timezone": account_timezone,
    }


def file_summary(file: dict[str, Any]) -> dict[str, Any]:
    """The file a Drive change returns: identity, place and link, in the read envelope's names.

    A change requests a narrow field mask, so only these keys are reported;
    read the full metadata with drive_get_file.
    """
    return {
        "id": file.get("id"),
        "name": file.get("name"),
        "kind": file_kind(file.get("mimeType")) if file.get("mimeType") else None,
        "mime_type": file.get("mimeType"),
        "size": file.get("size"),
        "size_bytes": _size_bytes(file),
        "parent_ids": file.get("parents"),
        "drive_id": file.get("driveId"),
        "web_url": clean_web_url(file.get("webViewLink")),
        "trashed": file.get("trashed"),
    }


def drive_envelope(drive: dict[str, Any], *, account_timezone: str) -> dict[str, Any]:
    """A shared drive: identity, visibility, what the user may do in it and its main restrictions.

    Google's ``capabilities`` and ``restrictions`` objects are reduced to the
    flags that decide the next action; a flag Google did not return is null.
    """
    capabilities = drive.get("capabilities") or {}
    restrictions = drive.get("restrictions") or {}
    return {
        "id": drive.get("id"),
        "name": drive.get("name"),
        "hidden": drive.get("hidden"),
        "created_at": in_account_timezone(drive.get("createdTime"), account_timezone),
        "org_unit_id": drive.get("orgUnitId"),
        "can_add_children": capabilities.get("canAddChildren"),
        "can_edit": capabilities.get("canEdit"),
        "can_share": capabilities.get("canShare"),
        "can_manage_members": capabilities.get("canManageMembers"),
        "can_download": capabilities.get("canDownload"),
        "can_delete_drive": capabilities.get("canDeleteDrive"),
        "admin_managed_restrictions": restrictions.get("adminManagedRestrictions"),
        "domain_users_only": restrictions.get("domainUsersOnly"),
        "drive_members_only": restrictions.get("driveMembersOnly"),
        "copy_requires_writer_permission": restrictions.get("copyRequiresWriterPermission"),
    }


def drive_summary(drive: dict[str, Any]) -> dict[str, Any]:
    """The shared drive a hide or unhide returns: identity and the new visibility."""
    return {"id": drive.get("id"), "name": drive.get("name"), "hidden": drive.get("hidden")}


def permission_envelope(permission: dict[str, Any], *, account_timezone: str | None) -> dict[str, Any]:
    """One sharing grant, in the names drive_share_file takes.

    ``expires_at`` is in the account time zone. Callers resolve the zone only
    when a grant has an expiration (``needs_account_timezone``); without one the
    value is null anyway.
    """
    expiration = permission.get("expirationTime")
    return {
        "id": permission.get("id"),
        "type": permission.get("type"),
        "role": permission.get("role"),
        "email_address": permission.get("emailAddress"),
        "domain": permission.get("domain"),
        "display_name": permission.get("displayName"),
        "allow_file_discovery": permission.get("allowFileDiscovery"),
        "pending_owner": permission.get("pendingOwner"),
        "expires_at": in_account_timezone(expiration, account_timezone) if account_timezone else expiration,
        "deleted": permission.get("deleted"),
    }


def needs_account_timezone(permissions: list[dict[str, Any]]) -> bool:
    """Whether any grant carries a time to convert, so the account zone must be looked up."""
    return any(permission.get("expirationTime") for permission in permissions)
