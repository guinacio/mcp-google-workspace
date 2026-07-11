"""Google Drive API client helpers."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload, MediaFileUpload

from ..auth import build_drive_service
from ..auth.google_auth import materialize_google_request
from ..common.downloads import max_download_bytes


def drive_service() -> Any:
    return build_drive_service()


def download_media_to_path(request: Any, path: Path) -> int:
    """Stream Google media into a bounded temporary/local path."""
    limit = max_download_bytes()
    try:
        with path.open("wb") as handle:
            downloader = MediaIoBaseDownload(
                handle, materialize_google_request(request)
            )
            done = False
            while not done:
                _, done = downloader.next_chunk()
                if handle.tell() > limit:
                    raise ValueError(
                        f"Download exceeded MCP_MAX_DOWNLOAD_BYTES ({limit} bytes)."
                    )
        return path.stat().st_size
    except BaseException:
        path.unlink(missing_ok=True)
        raise


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


def media_bytes_upload(data: bytes, mime_type: str | None, resumable: bool = True) -> MediaIoBaseUpload:
    """Create a Drive upload body without staging user bytes on disk."""
    return MediaIoBaseUpload(
        io.BytesIO(data),
        mimetype=mime_type or "application/octet-stream",
        resumable=resumable,
    )
