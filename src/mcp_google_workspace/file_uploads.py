"""MCP Apps file uploads and remote-filesystem safety helpers."""

from __future__ import annotations

import base64
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import io
import logging
import mimetypes
import os
from pathlib import Path
import secrets
import socket
import sqlite3
from threading import RLock
import time
from typing import Annotated, Any, Protocol, cast
import zipfile

from cryptography.fernet import InvalidToken
from fastmcp import Context
from fastmcp.apps.file_upload import FileUpload
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.providers.addressing import hashed_resource_uri
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from .auth.identity import current_principal
from .common.crypto import FernetKeyring
from .common.errors import RecoverableToolError
from .common.fastmcp_compat import local_tools
from .common.component_annotations import apply_structural_input_limits
from .runtime import get_token_storage_settings

AUDIT_LOGGER = logging.getLogger("mcp_google_workspace.audit")


@dataclass(frozen=True, slots=True)
class UploadedFile:
    """A fully decoded file stored by the MCP Apps picker."""

    name: str
    mime_type: str
    data: bytes
    upload_id: str | None = None
    checksum_sha256: str | None = None
    expires_at: int | None = None

    @property
    def size(self) -> int:
        return len(self.data)


class UploadedFileSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="Opaque upload handle (same value as upload_id).")
    type: str = Field(description="Validated MIME type.")
    size: int = Field(ge=0, description="Decoded file size in bytes.")
    size_display: str = Field(description="Human-readable file size.")
    uploaded_at: str = Field(description="UTC upload timestamp.")
    upload_id: str | None = Field(default=None, description="Opaque upload ID (upl_...).")
    display_name: str | None = Field(default=None, description="Original uploaded filename.")
    checksum_sha256: str | None = Field(default=None, description="SHA-256 content checksum.")
    expires_at: int | None = Field(default=None, description="Upload handle expiry epoch.")
    remaining_quota_bytes: int | None = Field(
        default=None, ge=0, description="Remaining upload quota of the current user in bytes."
    )


class UploadedFilePage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: list[UploadedFileSummary] = Field(description="Uploaded file summaries in this page.")
    count: int = Field(ge=0, description="Number of files returned in this page.")
    next_cursor: str | None = Field(
        default=None, description="Cursor for the next page, or null at the end."
    )


class UploadStore(Protocol):
    """Principal-scoped upload storage keyed by opaque ``upl_`` upload IDs.

    ``scope`` is always the caller's principal storage key (never a transport
    session), so uploads survive across independent MCP 2026-07-28 requests.
    Implemented by :class:`LocalUploadStore` (trusted local stdio principal,
    in memory), :class:`EncryptedUploadStore` (single remote node, SQLite +
    encrypted blobs), and ``common.s3_uploads.S3UploadStore`` (replica-shared
    Redis metadata + encrypted S3 objects).
    """

    def store(
        self, scope: str, files: list[dict[str, Any]], max_file_size: int
    ) -> list[dict[str, Any]]: ...

    def list(
        self, scope: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]: ...

    def get(self, scope: str, upload_id: str) -> UploadedFile: ...

    def delete(self, scope: str, upload_id: str) -> bool: ...


class EncryptedUploadStore:
    """Shared encrypted SQLite storage for authenticated remote uploads."""

    def __init__(
        self,
        path: Path,
        encryption_key: str | FernetKeyring,
        *,
        ttl_seconds: int = 60 * 60,
        quota_bytes: int = 250 * 1024 * 1024,
    ) -> None:
        self.path = path
        self.blob_directory = path.with_suffix(".blobs")
        self.keyring = (
            encryption_key
            if isinstance(encryption_key, FernetKeyring)
            else FernetKeyring.single(encryption_key)
        )
        self.ttl_seconds = ttl_seconds
        self.quota_bytes = quota_bytes
        self._last_reconcile: float | None = None

    @classmethod
    def from_environment(cls) -> "EncryptedUploadStore":
        settings = get_token_storage_settings()
        configured = os.getenv("MCP_UPLOAD_DB", "").strip()
        path = (
            Path(configured).expanduser().resolve()
            if configured
            else settings.user_token_dir / "uploads.sqlite3"
        )
        ttl, quota = _upload_limits_from_environment()
        return cls(path, settings.keyring, ttl_seconds=ttl, quota_bytes=quota)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS uploads_v2 (
                scope TEXT NOT NULL,
                upload_id TEXT NOT NULL,
                original_name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size INTEGER NOT NULL,
                checksum_sha256 TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                PRIMARY KEY (scope, upload_id)
            )
            """
        )
        return connection

    def _blob_path(self, scope: str, upload_id: str) -> Path:
        return self.blob_directory / scope / f"{upload_id}.blob"

    def _cleanup(self, connection: sqlite3.Connection, now: int) -> int:
        expired = connection.execute(
            "SELECT scope,upload_id,size FROM uploads_v2 WHERE expires_at < ?", (now,)
        ).fetchall()
        connection.execute("DELETE FROM uploads_v2 WHERE expires_at < ?", (now,))
        for scope, upload_id, _ in expired:
            self._blob_path(str(scope), str(upload_id)).unlink(missing_ok=True)
        if expired:
            from .common.production import UPLOAD_CLEANUPS, UPLOAD_REMOVED_BYTES

            UPLOAD_CLEANUPS.labels("encrypted-filesystem").inc(len(expired))
            UPLOAD_REMOVED_BYTES.labels("encrypted-filesystem").inc(
                sum(int(size) for _, _, size in expired)
            )
        # Reconcile crash leftovers at most once every five minutes. Blob names
        # are opaque server-generated handles, so no user path is traversed.
        if self._last_reconcile is None or time.monotonic() - self._last_reconcile >= 300:
            referenced = {
                self._blob_path(str(scope), str(upload_id)).resolve()
                for scope, upload_id in connection.execute(
                    "SELECT scope,upload_id FROM uploads_v2"
                ).fetchall()
            }
            # A concurrent store() writes its blob before committing the row,
            # so only files older than this grace window are true orphans.
            orphan_cutoff = time.time() - 600
            if self.blob_directory.exists():
                for path in self.blob_directory.glob("*/*"):
                    if not path.is_file():
                        continue
                    if path.suffix != ".tmp" and path.resolve() in referenced:
                        continue
                    try:
                        if path.stat().st_mtime > orphan_cutoff:
                            continue
                    except OSError:
                        continue
                    path.unlink(missing_ok=True)
            self._last_reconcile = time.monotonic()
        return len(expired)

    def store(self, scope: str, files: list[dict[str, Any]], max_file_size: int) -> list[dict[str, Any]]:
        now = int(time.time())
        decoded = _decode_upload_items(files, max_file_size)
        created_blobs: list[Path] = []
        with self._connect() as connection:
            self._cleanup(connection, now)
            existing = connection.execute(
                "SELECT upload_id,size FROM uploads_v2 WHERE scope=?", (scope,)
            ).fetchall()
            projected = sum(int(size) for _, size in existing) + sum(len(data) for _, _, data, _ in decoded)
            if projected > self.quota_bytes:
                raise RecoverableToolError(
                    "upload_quota_exhausted",
                    f"Upload quota exceeded ({self.quota_bytes} bytes per principal).",
                    required_action={"tool": "files_list_files", "arguments": {}},
                )
            uploaded_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            for name, mime_type, data, checksum in decoded:
                upload_id = "upl_" + secrets.token_urlsafe(24)
                blob_path = self._blob_path(scope, upload_id)
                blob_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = blob_path.with_suffix(".tmp")
                try:
                    temporary.write_bytes(self.keyring.encrypt(data))
                    temporary.replace(blob_path)
                    created_blobs.append(blob_path)
                    connection.execute(
                        """
                        INSERT INTO uploads_v2(
                          scope,upload_id,original_name,mime_type,size,checksum_sha256,uploaded_at,expires_at
                        ) VALUES(?,?,?,?,?,?,?,?)
                        """,
                        (
                            scope,
                            upload_id,
                            name,
                            mime_type,
                            len(data),
                            checksum,
                            uploaded_at,
                            now + self.ttl_seconds,
                        ),
                    )
                except BaseException:
                    temporary.unlink(missing_ok=True)
                    for created_blob in created_blobs:
                        created_blob.unlink(missing_ok=True)
                    raise
                AUDIT_LOGGER.info(
                    "upload_stored principal_hash=%s upload_id=%s size=%s mime_type=%s",
                    scope[:16], upload_id, len(data), mime_type,
                )
            from .common.production import UPLOAD_STORED_BYTES

            UPLOAD_STORED_BYTES.labels("encrypted-filesystem").inc(
                sum(len(data) for _, _, data, _ in decoded)
            )
        return self.list(scope)

    def list(
        self, scope: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Upload pagination requires limit 1..100 and a non-negative offset.")
        now = int(time.time())
        with self._connect() as connection:
            self._cleanup(connection, now)
            used_bytes = int(
                connection.execute(
                    "SELECT COALESCE(SUM(size),0) FROM uploads_v2 WHERE scope=?",
                    (scope,),
                ).fetchone()[0]
            )
            rows = connection.execute(
                "SELECT upload_id,original_name,mime_type,size,checksum_sha256,uploaded_at,expires_at "
                "FROM uploads_v2 WHERE scope=? ORDER BY uploaded_at DESC LIMIT ? OFFSET ?",
                (scope, limit, offset),
            ).fetchall()
        remaining_quota_bytes = max(0, self.quota_bytes - used_bytes)
        return [
            {
                "name": upload_id,
                "upload_id": upload_id,
                "display_name": original_name,
                "type": mime_type,
                "size": size,
                "size_display": _size_display(size),
                "checksum_sha256": checksum,
                "uploaded_at": uploaded_at,
                "expires_at": expires_at,
                "remaining_quota_bytes": remaining_quota_bytes,
            }
            for upload_id, original_name, mime_type, size, checksum, uploaded_at, expires_at in rows
        ]

    def get(self, scope: str, name: str) -> UploadedFile:
        now = int(time.time())
        with self._connect() as connection:
            self._cleanup(connection, now)
            row = connection.execute(
                "SELECT original_name,mime_type,checksum_sha256,expires_at FROM uploads_v2 "
                "WHERE scope=? AND upload_id=?",
                (scope, name),
            ).fetchone()
        if row is None:
            raise RecoverableToolError(
                "upload_expired_or_missing",
                "The uploaded file handle was not found or has expired.",
                required_action={"tool": "files_file_manager", "arguments": {}},
            )
        stored_name, mime_type, checksum, expires_at = row
        blob_path = self._blob_path(scope, name)
        try:
            result = self.keyring.decrypt(blob_path.read_bytes())
            data = result.plaintext
        except (InvalidToken, OSError) as exc:
            raise ValueError("Uploaded file storage could not be decrypted.") from exc
        if result.needs_rotation:
            temporary = blob_path.with_suffix(".tmp")
            temporary.write_bytes(self.keyring.encrypt(data))
            temporary.replace(blob_path)
        AUDIT_LOGGER.info("upload_consumed principal_hash=%s upload_id=%s", scope[:16], name)
        return UploadedFile(
            name=stored_name,
            mime_type=mime_type,
            data=data,
            upload_id=name,
            checksum_sha256=checksum,
            expires_at=expires_at,
        )

    def delete(self, scope: str, name: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT size FROM uploads_v2 WHERE scope=? AND upload_id=?",
                (scope, name),
            ).fetchone()
            cursor = connection.execute(
                "DELETE FROM uploads_v2 WHERE scope=? AND upload_id=?", (scope, name)
            )
            deleted = cursor.rowcount > 0
            if deleted:
                # Keep metadata rollback-capable if object deletion fails.
                self._blob_path(scope, name).unlink(missing_ok=True)
        if deleted:
            from .common.production import UPLOAD_REMOVED_BYTES

            UPLOAD_REMOVED_BYTES.labels("encrypted-filesystem").inc(
                int(row[0]) if row else 0
            )
            AUDIT_LOGGER.info("upload_deleted principal_hash=%s upload_id=%s", scope[:16], name)
        return deleted


def _upload_limits_from_environment() -> tuple[int, int]:
    """Upload TTL and per-principal quota shared by every upload backend."""
    ttl = int(os.getenv("MCP_UPLOAD_TTL_SECONDS", "3600"))
    quota = int(os.getenv("MCP_UPLOAD_QUOTA_BYTES", str(250 * 1024 * 1024)))
    if not 60 <= ttl <= 7 * 24 * 60 * 60:
        raise ValueError("MCP_UPLOAD_TTL_SECONDS must be between 60 and 604800.")
    if not 1 <= quota <= 10 * 1024 * 1024 * 1024:
        raise ValueError("MCP_UPLOAD_QUOTA_BYTES must be between 1 byte and 10 GiB.")
    return ttl, quota


def _decode_upload_items(
    files: list[dict[str, Any]], max_file_size: int
) -> list[tuple[str, str, bytes, str]]:
    """Validate picker items: name, base64, size, MIME sniffing, malware scan."""
    decoded: list[tuple[str, str, bytes, str]] = []
    for item in files:
        name = str(item.get("name") or "").strip()
        if not name or len(name) > 255:
            raise ValueError("Uploaded filenames must contain 1 to 255 characters.")
        try:
            data = base64.b64decode(item["data"], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Uploaded file {name!r} has invalid encoded data.") from exc
        if len(data) > max_file_size:
            raise ValueError(f"Uploaded file {name!r} exceeds the per-file limit.")
        declared_type = str(item.get("type") or "application/octet-stream")
        detected_type = _validate_upload_content(name, declared_type, data)
        _scan_malware(data)
        decoded.append((name, detected_type, data, sha256(data).hexdigest()))
    return decoded


def _missing_upload() -> RecoverableToolError:
    return RecoverableToolError(
        "upload_expired_or_missing",
        "The uploaded file handle was not found or has expired.",
        required_action={"tool": "files_file_manager", "arguments": {}},
    )


@dataclass(frozen=True, slots=True)
class _LocalUpload:
    upload_id: str
    name: str
    mime_type: str
    data: bytes
    checksum_sha256: str
    uploaded_at: str
    expires_at: int


class LocalUploadStore:
    """In-memory uploads for the trusted local (stdio) principal.

    Local stdio has no transport session under MCP 2026-07-28, so uploads are
    scoped to the one explicit trusted-local principal and addressed by opaque
    ``upl_`` IDs, exactly like the remote stores. The same validation, TTL and
    per-principal quota apply; bytes stay in process memory and disappear with
    the process (no encryption at rest is needed because nothing is written).
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = 60 * 60,
        quota_bytes: int = 250 * 1024 * 1024,
        clock: Any = time.time,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.quota_bytes = quota_bytes
        self._clock = clock
        self._uploads: dict[str, dict[str, _LocalUpload]] = {}
        self._lock = RLock()

    @classmethod
    def from_environment(cls) -> "LocalUploadStore":
        ttl, quota = _upload_limits_from_environment()
        return cls(ttl_seconds=ttl, quota_bytes=quota)

    def _live_locked(self, scope: str) -> dict[str, _LocalUpload]:
        now = int(self._clock())
        uploads = self._uploads.get(scope, {})
        for upload_id in [key for key, item in uploads.items() if item.expires_at < now]:
            del uploads[upload_id]
        if not uploads:
            self._uploads.pop(scope, None)
        return uploads

    def store(self, scope: str, files: list[dict[str, Any]], max_file_size: int) -> list[dict[str, Any]]:
        decoded = _decode_upload_items(files, max_file_size)
        with self._lock:
            uploads = self._live_locked(scope)
            projected = sum(len(item.data) for item in uploads.values()) + sum(
                len(data) for _, _, data, _ in decoded
            )
            if projected > self.quota_bytes:
                raise RecoverableToolError(
                    "upload_quota_exhausted",
                    f"Upload quota exceeded ({self.quota_bytes} bytes per principal).",
                    required_action={"tool": "files_list_files", "arguments": {}},
                )
            now = int(self._clock())
            uploaded_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            target = self._uploads.setdefault(scope, uploads)
            for name, mime_type, data, checksum in decoded:
                upload_id = "upl_" + secrets.token_urlsafe(24)
                target[upload_id] = _LocalUpload(
                    upload_id=upload_id,
                    name=name,
                    mime_type=mime_type,
                    data=data,
                    checksum_sha256=checksum,
                    uploaded_at=uploaded_at,
                    expires_at=now + self.ttl_seconds,
                )
                AUDIT_LOGGER.info(
                    "upload_stored principal_hash=%s upload_id=%s size=%s mime_type=%s",
                    scope[:16], upload_id, len(data), mime_type,
                )
        return self.list(scope)

    def list(
        self, scope: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Upload pagination requires limit 1..100 and a non-negative offset.")
        with self._lock:
            uploads = list(self._live_locked(scope).values())
        remaining_quota_bytes = max(0, self.quota_bytes - sum(len(item.data) for item in uploads))
        uploads.sort(key=lambda item: (item.uploaded_at, item.upload_id), reverse=True)
        return [
            {
                "name": item.upload_id,
                "upload_id": item.upload_id,
                "display_name": item.name,
                "type": item.mime_type,
                "size": len(item.data),
                "size_display": _size_display(len(item.data)),
                "checksum_sha256": item.checksum_sha256,
                "uploaded_at": item.uploaded_at,
                "expires_at": item.expires_at,
                "remaining_quota_bytes": remaining_quota_bytes,
            }
            for item in uploads[offset : offset + limit]
        ]

    def get(self, scope: str, name: str) -> UploadedFile:
        with self._lock:
            item = self._live_locked(scope).get(name)
        if item is None:
            raise _missing_upload()
        if sha256(item.data).hexdigest() != item.checksum_sha256:
            raise ValueError("Uploaded file checksum verification failed.")
        AUDIT_LOGGER.info("upload_consumed principal_hash=%s upload_id=%s", scope[:16], name)
        return UploadedFile(
            name=item.name,
            mime_type=item.mime_type,
            data=item.data,
            upload_id=item.upload_id,
            checksum_sha256=item.checksum_sha256,
            expires_at=item.expires_at,
        )

    def delete(self, scope: str, name: str) -> bool:
        with self._lock:
            deleted = self._live_locked(scope).pop(name, None) is not None
        if deleted:
            AUDIT_LOGGER.info("upload_deleted principal_hash=%s upload_id=%s", scope[:16], name)
        return deleted


def _detected_mime(name: str, data: bytes) -> str:
    signatures = (
        (b"%PDF-", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"PK\x03\x04", "application/zip"),
        (b"RIFF", "application/octet-stream"),
    )
    for signature, mime_type in signatures:
        if data.startswith(signature):
            return mime_type
    guessed, _ = mimetypes.guess_type(name)
    if data and b"\x00" not in data[:4096]:
        return guessed if guessed and guessed.startswith("text/") else "text/plain"
    return guessed or "application/octet-stream"


def _validate_upload_content(name: str, declared: str, data: bytes) -> str:
    detected = _detected_mime(name, data)
    declared_family = declared.split("/", 1)[0]
    detected_family = detected.split("/", 1)[0]
    if declared != "application/octet-stream" and declared_family != detected_family:
        raise ValueError(
            f"Uploaded file {name!r} content type {detected!r} does not match declared type {declared!r}."
        )
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                total = sum(item.file_size for item in archive.infolist())
                compressed = max(1, sum(item.compress_size for item in archive.infolist()))
                if len(archive.infolist()) > 10_000 or total > 1_000_000_000 or total / compressed > 100:
                    raise ValueError(f"Uploaded archive {name!r} exceeds safe expansion limits.")
        except zipfile.BadZipFile as exc:
            raise ValueError(f"Uploaded archive {name!r} is invalid.") from exc
    return declared if declared != "application/octet-stream" else detected


def _scan_malware(data: bytes) -> None:
    host = os.getenv("MCP_CLAMAV_HOST", "").strip()
    required = os.getenv("MCP_REQUIRE_MALWARE_SCAN", "").strip().lower() in {"1", "true", "yes", "on"}
    if not host:
        if required:
            raise RuntimeError("Remote uploads require MCP_CLAMAV_HOST when malware scanning is enforced.")
        return
    port = int(os.getenv("MCP_CLAMAV_PORT", "3310"))
    try:
        with socket.create_connection((host, port), timeout=10) as connection:
            connection.sendall(b"zINSTREAM\0")
            for offset in range(0, len(data), 64 * 1024):
                chunk = data[offset : offset + 64 * 1024]
                connection.sendall(len(chunk).to_bytes(4, "big") + chunk)
            connection.sendall((0).to_bytes(4, "big"))
            response = connection.recv(4096)
    except OSError as exc:
        raise RuntimeError("Malware scanner is unavailable; upload was rejected.") from exc
    if b"FOUND" in response or b"OK" not in response:
        raise ValueError("Malware scanner rejected the uploaded file.")


def _size_display(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


class WorkspaceFileUpload(FileUpload):
    """FileUpload scoped to the caller's principal, never to a transport session.

    Every operation resolves ``(store, scope)`` per request: an authenticated
    remote caller uses the shared remote store (SQLite/blob or Redis/S3) scoped
    by its ``(issuer, subject)`` storage key; an unauthenticated local stdio
    caller uses the in-memory :class:`LocalUploadStore` scoped by the one
    explicit trusted-local principal. Uploads are addressed by opaque upload IDs
    in both cases, so they survive independent MCP 2026-07-28 requests.
    """

    def __init__(
        self,
        *,
        remote_store: UploadStore | None = None,
        local_store: UploadStore | None = None,
    ) -> None:
        super().__init__(
            name="Workspace Files",
            max_file_size=25 * 1024 * 1024,
            title="Choose Workspace files",
            description=(
                "Upload files for Gmail attachments, Google Drive uploads, or Gemini media tools. "
                "The file bytes go directly to the MCP server instead of through the model context."
            ),
            drop_label="Drop files here or choose files",
        )
        self._upload_lock = RLock()
        self._encrypted_store: UploadStore | None = remote_store
        self._local_store: UploadStore | None = local_store

        @self.tool(model=True)
        def delete_file(name: str, ctx: Context) -> dict[str, Any]:
            """Delete one uploaded file of the current user by its upload ID."""
            store, scope = self._scoped_store()
            deleted = store.delete(scope, name)
            return {"status": "deleted" if deleted else "not_found", "name": name}

        @self.tool(model=True)
        def list_files_page(
            ctx: Context,
            limit: Annotated[int, "Maximum number of uploaded files to return (1-100)."] = 50,
            cursor: Annotated[
                str | None,
                "Opaque continuation cursor returned by the previous page.",
            ] = None,
        ) -> UploadedFilePage:
            """Page through uploaded files without returning an unbounded catalog."""
            if not 1 <= limit <= 100:
                raise ValueError("limit must be between 1 and 100")
            try:
                offset = int(cursor or "0")
            except ValueError as exc:
                raise ValueError("cursor must be a non-negative integer string") from exc
            if offset < 0:
                raise ValueError("cursor must be a non-negative integer string")
            store, scope = self._scoped_store()
            files = store.list(scope, limit=limit, offset=offset)
            next_cursor = str(offset + len(files)) if len(files) == limit else None
            return UploadedFilePage(
                files=[UploadedFileSummary.model_validate(item) for item in files],
                count=len(files),
                next_cursor=next_cursor,
            )

        for raw_component in local_tools(self):
            component = cast(Any, raw_component)
            component.title = f"Files {component.name.replace('_', ' ').title()}"
            component.tags.update({"files", "upload", "mcp-app"})
            read_only = component.name not in {"store_files", "delete_file"}
            component.annotations = ToolAnnotations(
                read_only_hint=read_only,
                destructive_hint=False,
                idempotent_hint=component.name != "store_files",
                open_world_hint=False,
            )
            if component.name == "file_manager":
                component.meta = {
                    **(component.meta or {}),
                    "ui/resourceUri": hashed_resource_uri(self.name, component.name),
                }
            if component.name == "store_files":
                # FastMCP 4 lists this app-only callback in tools/list (hosts
                # filter by _meta.ui.visibility), so its input is now part of
                # the published contract: document and bound the exact
                # DropZone payload instead of an open list[dict].
                component.parameters = _store_files_input_schema(self._max_file_size)
            properties = component.parameters.get("properties", {})
            component.parameters.setdefault("required", [])
            if "name" in properties:
                properties["name"].setdefault(
                    "description",
                    "Opaque upload ID (upl_...) returned by the picker or files_list_files.",
                )
            apply_structural_input_limits(component.parameters)
            if component.name in {"list_files", "store_files"}:
                component.output_schema = {
                    "type": "object",
                    "x-fastmcp-wrap-result": True,
                    "properties": {
                        "result": {
                            "type": "array",
                            "description": "Files currently stored for the calling user.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "type": {"type": "string"},
                                    "size": {"type": "integer"},
                                    "size_display": {"type": "string"},
                                    "uploaded_at": {"type": "string"},
                                    "upload_id": {"type": "string"},
                                    "display_name": {"type": "string"},
                                    "checksum_sha256": {"type": "string"},
                                    "expires_at": {"type": "integer"},
                                    "remaining_quota_bytes": {
                                        "type": "integer",
                                        "minimum": 0,
                                        "description": (
                                            "Bytes still available in the calling user's "
                                            "upload quota."
                                        ),
                                    },
                                },
                                "required": ["name", "type", "size", "size_display", "uploaded_at"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["result"],
                    "additionalProperties": False,
                }
            elif component.name == "read_file":
                # read_file returns a plain dict; the declared {"result": ...}
                # envelope only matches the wire payload when FastMCP wraps it.
                component.output_schema = {
                    "type": "object",
                    "x-fastmcp-wrap-result": True,
                    "properties": {
                        "result": {
                            "type": "object",
                            "description": "Uploaded file metadata and readable content or binary preview.",
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string"},
                                "size": {"type": "integer"},
                                "uploaded_at": {"type": "string"},
                                "content": {"type": "string"},
                                "content_base64": {"type": "string"},
                                "encoding": {"type": "string"},
                                "truncated": {"type": "boolean"},
                            },
                            "required": ["name", "type", "size"],
                            "additionalProperties": False,
                        }
                    },
                    "required": ["result"],
                    "additionalProperties": False,
                }
            elif component.name == "file_manager":
                component.output_schema = copy.deepcopy(_PICKER_OUTPUT_SCHEMA)
            elif component.name == "delete_file":
                component.output_schema = {
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "description": "Whether the upload was deleted or not found.",
                        },
                        "name": {
                            "type": "string",
                            "description": "Upload ID requested for deletion.",
                        },
                    },
                    "required": ["status", "name"],
                    "additionalProperties": False,
                }

    def _get_scope_key(self, ctx: Context) -> str:
        # The inherited in-memory helpers are never used; if they were, they
        # must still partition by principal rather than by transport session.
        return current_principal().storage_key

    def _remote_store(self) -> UploadStore:
        with self._upload_lock:
            if self._encrypted_store is None:
                if os.getenv("MCP_REDIS_URL", "").strip() and os.getenv(
                    "MCP_UPLOAD_S3_BUCKET", ""
                ).strip():
                    from .common.s3_uploads import S3UploadStore

                    self._encrypted_store = S3UploadStore()
                else:
                    self._encrypted_store = EncryptedUploadStore.from_environment()
            return self._encrypted_store

    def _trusted_local_store(self) -> UploadStore:
        with self._upload_lock:
            if self._local_store is None:
                self._local_store = LocalUploadStore.from_environment()
            return self._local_store

    def _scoped_store(self) -> tuple[UploadStore, str]:
        """Resolve the backend and principal scope for the current request."""
        scope = current_principal().storage_key
        if get_access_token() is not None:
            return self._remote_store(), scope
        return self._trusted_local_store(), scope

    def on_store(self, files: list[dict[str, Any]], ctx: Context) -> list[dict[str, Any]]:
        store, scope = self._scoped_store()
        return store.store(scope, files, self._max_file_size)

    def on_list(self, ctx: Context) -> list[dict[str, Any]]:
        store, scope = self._scoped_store()
        return store.list(scope)

    def on_read(self, name: str, ctx: Context) -> dict[str, Any]:
        store, scope = self._scoped_store()
        uploaded = store.get(scope, name)
        if uploaded.mime_type.startswith("text/"):
            return {
                "name": uploaded.name,
                "type": uploaded.mime_type,
                "size": uploaded.size,
                "content": uploaded.data.decode("utf-8", errors="replace"),
                "encoding": "utf-8",
                "truncated": False,
            }
        return {
            "name": uploaded.name,
            "type": uploaded.mime_type,
            "size": uploaded.size,
            "content_base64": base64.b64encode(uploaded.data[:150]).decode("ascii"),
            "encoding": "base64-preview",
            "truncated": uploaded.size > 150,
        }

    def get_file(
        self,
        name: str,
        ctx: Context | None = None,
        *,
        allowed_mime_prefixes: tuple[str, ...] | None = None,
    ) -> UploadedFile:
        """Return complete uploaded bytes for an integration tool.

        ``ctx`` is accepted for call-site compatibility only: the upload is
        resolved from the caller's principal and the opaque upload ID.
        """
        store, scope = self._scoped_store()
        uploaded = store.get(scope, name)
        if uploaded.size > self._max_file_size:
            raise ValueError(
                f"Uploaded file {name!r} exceeds the {self._max_file_size}-byte limit."
            )
        _require_allowed_mime(uploaded, allowed_mime_prefixes)
        return uploaded


# Prefab wire envelope produced by ``FileUpload.file_manager`` under
# fastmcp 4.0.10 / prefab-ui 0.20.2: ``PrefabApp(view=..., state=...)`` emits
# ``$prefab``/``view``/``state``; FastMCP then records late-bound backend tool
# names under ``_meta.fastmcp.toolNames`` (see
# ``fastmcp.server.providers.prefab_payload``). Only that closed ``_meta`` shape
# is accepted; any other top-level or ``_meta`` key still fails validation.
_PICKER_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "$prefab": {"type": "object", "description": "Prefab protocol metadata."},
        "view": {"type": "object", "description": "Declarative picker UI tree."},
        "state": {"type": "object", "description": "Initial picker UI state."},
        "_meta": {
            "type": "object",
            "description": "FastMCP renderer metadata for the Prefab payload.",
            "properties": {
                "fastmcp": {
                    "type": "object",
                    "description": "FastMCP-owned Prefab addressing metadata.",
                    "properties": {
                        "toolNames": {
                            "type": "object",
                            "description": (
                                "Map from each backend tool name used in the UI tree to its "
                                "stable <hash>_<name> address."
                            ),
                            "additionalProperties": {"type": "string"},
                        }
                    },
                    "additionalProperties": False,
                }
            },
            "additionalProperties": False,
        },
    },
    "required": ["$prefab", "view", "state"],
    "additionalProperties": False,
}


def _store_files_input_schema(max_file_size: int) -> dict[str, Any]:
    """Closed, documented input schema for the picker's upload callback."""
    max_base64_length = 4 * ((max_file_size + 2) // 3)
    return {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "description": (
                    "Files selected in the Workspace Files picker, as produced by its "
                    "drop zone. The total request is also bounded by the server's "
                    "HTTP request-size limit."
                ),
                "items": {
                    "type": "object",
                    "description": "One selected file with base64-encoded content.",
                    "properties": {
                        "name": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 1024,
                            "description": "Original filename reported by the browser.",
                        },
                        "size": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": max_file_size,
                            "description": (
                                "Browser-reported size in bytes; the server recomputes it "
                                "from the decoded data."
                            ),
                        },
                        "type": {
                            "type": "string",
                            "maxLength": 255,
                            "description": (
                                "Browser-reported MIME type; the server re-validates content."
                            ),
                        },
                        "data": {
                            "type": "string",
                            "maxLength": max_base64_length,
                            "description": (
                                f"Base64-encoded file content, at most {max_file_size} "
                                "decoded bytes."
                            ),
                        },
                    },
                    "required": ["name", "size", "type", "data"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["files"],
        "additionalProperties": False,
    }


def _require_allowed_mime(
    uploaded: UploadedFile, allowed_prefixes: tuple[str, ...] | None
) -> None:
    if allowed_prefixes and not uploaded.mime_type.startswith(allowed_prefixes):
        raise ValueError(
            f"Upload type {uploaded.mime_type!r} is not accepted by this tool; "
            f"expected one of {', '.join(allowed_prefixes)}."
        )


workspace_file_upload = WorkspaceFileUpload()


def require_local_filesystem(operation: str) -> None:
    """Allow raw server paths only for trusted local/stdio operation."""
    if get_access_token() is not None:
        raise RecoverableToolError(
            "picker_required",
            f"{operation} cannot use a server-local path over remote MCP. "
            "Use the Workspace Files picker and pass its upload handle instead.",
            required_action={"tool": "files_file_manager", "arguments": {}},
        )
