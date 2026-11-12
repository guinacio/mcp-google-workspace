"""Drive response normalizers over recorded-shape payloads."""

from __future__ import annotations

from mcp_google_workspace.drive.presentation import (
    drive_envelope,
    drive_summary,
    file_envelope,
    files_page,
    permission_envelope,
)

from . import TIMEZONE, Case

CASES = [
    Case(
        name="drive_list_files",
        fixture="drive_list_files.json",
        normalize=lambda payload: files_page(payload, account_timezone=TIMEZONE),
        items_key="files",
    ),
    Case(
        name="drive_get_file",
        fixture="drive_list_files.json",
        normalize=lambda payload: {
            "file": file_envelope(payload["files"][1], account_timezone=TIMEZONE),
            "account_timezone": TIMEZONE,
        },
    ),
    Case(
        name="drive_list_drives",
        fixture="drive_list_drives.json",
        normalize=lambda payload: {
            "drives": [drive_envelope(item, account_timezone=TIMEZONE) for item in payload["drives"]],
            "next_page_token": payload.get("nextPageToken"),
            "count": len(payload["drives"]),
        },
        items_key="drives",
    ),
    Case(
        name="drive_update_drive",
        fixture="drive_list_drives.json",
        # hide/unhide request only id, name and hidden; extra fields must not leak.
        normalize=lambda payload: {"status": "ok", "drive": drive_summary(payload["drives"][1])},
    ),
    Case(
        name="drive_list_permissions",
        fixture="drive_list_permissions.json",
        normalize=lambda payload: {
            "permissions": [
                permission_envelope(item, account_timezone=TIMEZONE) for item in payload["permissions"]
            ],
            "next_page_token": None,
            "count": len(payload["permissions"]),
        },
        items_key="permissions",
    ),
    Case(
        name="drive_share_file",
        fixture="drive_list_permissions.json",
        normalize=lambda payload: {
            "status": "ok",
            "mode": "created",
            "permission": permission_envelope(payload["permissions"][1], account_timezone=TIMEZONE),
        },
    ),
]
