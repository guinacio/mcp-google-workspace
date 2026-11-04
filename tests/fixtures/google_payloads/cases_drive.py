"""Drive response normalizers over recorded-shape payloads."""

from __future__ import annotations

from mcp_google_workspace.drive.presentation import file_envelope, files_page

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
]
