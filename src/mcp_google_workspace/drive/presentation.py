"""Compact Drive metadata envelopes."""

from __future__ import annotations

from typing import Any

from ..common.timezone import in_account_timezone


def _file_kind(mime_type: str | None) -> str:
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


def _people(people: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        {"name": person.get("displayName"), "email": person.get("emailAddress")}
        for person in people or []
    ]


def file_envelope(file: dict[str, Any], *, account_timezone: str) -> dict[str, Any]:
    """Present file metadata suitable for selecting the next action."""
    capabilities = file.get("capabilities", {})
    return {
        "id": file.get("id"),
        "name": file.get("name"),
        "kind": _file_kind(file.get("mimeType")),
        "mime_type": file.get("mimeType"),
        "size": file.get("size"),
        "created_at": in_account_timezone(file.get("createdTime"), account_timezone),
        "modified_at": in_account_timezone(file.get("modifiedTime"), account_timezone),
        "timezone": account_timezone,
        "source_created_at": file.get("createdTime"),
        "source_modified_at": file.get("modifiedTime"),
        "description": file.get("description"),
        "owners": _people(file.get("owners")),
        "last_modified_by": _people([file["lastModifyingUser"]] if file.get("lastModifyingUser") else [])[0] if file.get("lastModifyingUser") else None,
        "parent_ids": file.get("parents", []),
        "drive_id": file.get("driveId"),
        "web_url": file.get("webViewLink"),
        "shared": bool(file.get("shared", False)),
        "starred": bool(file.get("starred", False)),
        "trashed": bool(file.get("trashed", False)),
        "owned_by_me": file.get("ownedByMe"),
        "can_edit": capabilities.get("canEdit"),
        "can_download": capabilities.get("canDownload"),
    }
