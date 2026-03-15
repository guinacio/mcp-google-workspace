"""Local file helpers for Gemini media tools."""

from __future__ import annotations

import mimetypes
import tempfile
from pathlib import Path
from secrets import token_hex


_MIME_EXTENSION_MAP: dict[str, str] = {
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/wav": ".wav",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/svg+xml": ".svg",
    "text/plain": ".txt",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
}


def guess_mime_type(path: Path, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or fallback


def guess_extension(mime_type: str | None) -> str:
    if not mime_type:
        return ""
    normalized = mime_type.split(";", 1)[0].strip().lower()
    return _MIME_EXTENSION_MAP.get(normalized) or mimetypes.guess_extension(normalized) or ""


def sanitize_filename(filename: str) -> str:
    sanitized = "".join("_" if char in '\\/:*?"<>|' else char for char in filename).strip()
    return sanitized or "asset"


def ensure_filename_extension(filename: str, mime_type: str | None) -> str:
    sanitized = sanitize_filename(filename)
    if Path(sanitized).suffix:
        return sanitized
    extension = guess_extension(mime_type)
    return f"{sanitized}{extension}" if extension else sanitized


def resolve_output_dir(configured_output_dir: str, override: str | None = None) -> Path:
    base = Path(override or configured_output_dir)
    base.mkdir(parents=True, exist_ok=True)
    return base


def build_output_path(
    output_dir: Path,
    *,
    output_filename: str | None,
    default_stem: str,
    mime_type: str | None,
) -> Path:
    candidate = ensure_filename_extension(output_filename or default_stem, mime_type)
    path = output_dir / candidate
    if not path.exists():
        return path
    stem = path.stem or default_stem
    suffix = path.suffix
    return output_dir / f"{stem}-{token_hex(4)}{suffix}"


def stage_temp_file(data: bytes, *, filename: str, mime_type: str | None = None) -> Path:
    suffix = Path(filename).suffix or guess_extension(mime_type)
    with tempfile.NamedTemporaryFile(prefix="gemini-drive-", suffix=suffix, delete=False) as handle:
        handle.write(data)
        return Path(handle.name)
