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
