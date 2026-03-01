"""Google Drive API client helpers."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

from ..auth import build_drive_service


def drive_service() -> Any:
    return build_drive_service()


def download_media_to_bytes(request: Any) -> bytes:
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def write_bytes_to_path(data: bytes, output_path: str, overwrite: bool = False) -> Path:
    path = Path(output_path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"Output path already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def media_file_upload(local_path: str, mime_type: str | None, resumable: bool = True) -> MediaFileUpload:
    path = Path(local_path)
    if not path.exists():
        raise FileNotFoundError(f"Local file not found: {path}")
    return MediaFileUpload(filename=str(path), mimetype=mime_type, resumable=resumable)
