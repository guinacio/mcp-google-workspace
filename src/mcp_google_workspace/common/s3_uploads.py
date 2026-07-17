"""Cross-replica encrypted upload storage backed by S3 and Redis."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import os
import secrets
import time
from typing import Any

import boto3
import redis
from redis.exceptions import WatchError

from .errors import RecoverableToolError
from ..runtime import get_token_storage_settings

LOGGER = logging.getLogger("mcp_google_workspace.uploads.s3")


class S3UploadStore:
    """Encrypted S3-compatible blobs with Redis metadata and atomic quotas."""

    def __init__(self) -> None:
        self.redis = redis.Redis.from_url(os.environ["MCP_REDIS_URL"], decode_responses=True)
        self.s3 = boto3.client("s3", endpoint_url=os.getenv("MCP_UPLOAD_S3_ENDPOINT") or None)
        self.bucket = os.environ["MCP_UPLOAD_S3_BUCKET"]
        self.prefix = os.getenv("MCP_UPLOAD_S3_PREFIX", "mcp-google-workspace/uploads").strip("/")
        self.ttl_seconds = int(os.getenv("MCP_UPLOAD_TTL_SECONDS", "3600"))
        self.quota_bytes = int(os.getenv("MCP_UPLOAD_QUOTA_BYTES", str(250 * 1024 * 1024)))
        self.keyring = get_token_storage_settings().keyring

    def _metadata_key(self, scope: str) -> str:
        return f"mcp:uploads:{scope}"

    def _object_key(self, scope: str, upload_id: str) -> str:
        return f"{self.prefix}/{scope}/{upload_id}.blob"

    def _delete_expired(self, scope: str) -> None:
        now = int(time.time())
        key = self._metadata_key(scope)
        expired: list[str] = []
        expired_bytes = 0
        for raw_upload_id, raw in self.redis.hgetall(key).items():
            upload_id = str(raw_upload_id)
            item = json.loads(raw)
            if int(item["expires_at"]) < now or item.get("status") == "deleting":
                expired.append(upload_id)
                expired_bytes += int(item.get("size", 0))
                self.s3.delete_object(Bucket=self.bucket, Key=self._object_key(scope, upload_id))
        if expired:
            self.redis.hdel(key, *expired)
            from .production import UPLOAD_CLEANUPS, UPLOAD_REMOVED_BYTES

            UPLOAD_CLEANUPS.labels("s3").inc(len(expired))
            UPLOAD_REMOVED_BYTES.labels("s3").inc(expired_bytes)

    def store(self, scope: str, files: list[dict[str, Any]], max_file_size: int) -> list[dict[str, Any]]:
        # Import after file_uploads is initialized to avoid a module cycle.
        from ..file_uploads import _scan_malware, _size_display, _validate_upload_content

        now = int(time.time())
        prepared: list[tuple[str, dict[str, Any], bytes]] = []
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
            declared = str(item.get("type") or "application/octet-stream")
            mime_type = _validate_upload_content(name, declared, data)
            _scan_malware(data)
            upload_id = "upl_" + secrets.token_urlsafe(24)
            metadata = {
                "upload_id": upload_id,
                "name": upload_id,
                "display_name": name,
                "type": mime_type,
                "size": len(data),
                "size_display": _size_display(len(data)),
                "checksum_sha256": sha256(data).hexdigest(),
                "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                "expires_at": now + self.ttl_seconds,
            }
            prepared.append((upload_id, metadata, self.keyring.encrypt(data)))

        key = self._metadata_key(scope)
        upload_ids = [upload_id for upload_id, _, _ in prepared]
        # Reserve quota and handles in Redis before creating any object. Staged
        # records are invisible to clients and expire quickly, allowing the
        # reconciler above to clean up after a process crash.
        while True:
            try:
                with self.redis.pipeline() as pipe:
                    pipe.watch(key)
                    existing = [json.loads(raw) for raw in pipe.hvals(key)]
                    projected = sum(
                        int(item["size"]) for item in existing if int(item["expires_at"]) >= now
                    ) + sum(int(metadata["size"]) for _, metadata, _ in prepared)
                    if projected > self.quota_bytes:
                        raise RecoverableToolError(
                            "upload_quota_exhausted",
                            f"Upload quota exceeded ({self.quota_bytes} bytes per principal).",
                            required_action={"tool": "files_list_files", "arguments": {}},
                        )
                    pipe.multi()
                    for upload_id, metadata, _ in prepared:
                        expires_at = metadata.get("expires_at")
                        if not isinstance(expires_at, int):
                            raise ValueError("Prepared upload metadata is missing expires_at.")
                        staged = {
                            **metadata,
                            "status": "staging",
                            "expires_at": min(expires_at, now + 300),
                        }
                        pipe.hset(key, upload_id, json.dumps(staged, separators=(",", ":")))
                    pipe.expire(key, self.ttl_seconds + 300)
                    pipe.execute()
                    break
            except WatchError:
                continue
        uploaded: list[str] = []
        try:
            for upload_id, _, encrypted in prepared:
                self.s3.put_object(
                    Bucket=self.bucket,
                    Key=self._object_key(scope, upload_id),
                    Body=encrypted,
                    ContentType="application/octet-stream",
                )
                uploaded.append(upload_id)
            with self.redis.pipeline(transaction=True) as pipe:
                for upload_id, metadata, _ in prepared:
                    ready = {**metadata, "status": "ready"}
                    pipe.hset(key, upload_id, json.dumps(ready, separators=(",", ":")))
                pipe.expire(key, self.ttl_seconds + 300)
                pipe.execute()
            from .production import UPLOAD_STORED_BYTES

            UPLOAD_STORED_BYTES.labels("s3").inc(
                sum(int(metadata["size"]) for _, metadata, _ in prepared)
            )
        except BaseException:
            # Compensation is deliberately best-effort in both stores. Any
            # surviving staged record is still bounded and reconciled on list.
            for upload_id in uploaded:
                try:
                    self.s3.delete_object(
                        Bucket=self.bucket,
                        Key=self._object_key(scope, upload_id),
                    )
                except Exception as cleanup_error:
                    LOGGER.warning(
                        "Failed to compensate uploaded object %s: %s",
                        upload_id,
                        cleanup_error.__class__.__name__,
                    )
            try:
                if upload_ids:
                    self.redis.hdel(key, *upload_ids)
            except Exception as cleanup_error:
                LOGGER.warning(
                    "Failed to remove staged upload metadata: %s",
                    cleanup_error.__class__.__name__,
                )
            raise
        return self.list(scope)

    def list(
        self, scope: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Upload pagination requires limit 1..100 and a non-negative offset.")
        self._delete_expired(scope)
        all_items = sorted(
            (
                item
                for raw in self.redis.hvals(self._metadata_key(scope))
                if (item := json.loads(raw)).get("status", "ready") == "ready"
            ),
            key=lambda item: item["uploaded_at"],
            reverse=True,
        )
        remaining = max(
            0, self.quota_bytes - sum(int(item["size"]) for item in all_items)
        )
        items = all_items[offset : offset + limit]
        return [{**item, "remaining_quota_bytes": remaining} for item in items]

    def get(self, scope: str, upload_id: str) -> Any:
        from ..file_uploads import UploadedFile

        raw = self.redis.hget(self._metadata_key(scope), upload_id)
        if raw is None:
            raise RecoverableToolError(
                "upload_expired_or_missing",
                "The uploaded file handle was not found or has expired.",
                required_action={"tool": "files_file_manager", "arguments": {}},
            )
        item = json.loads(raw)
        if item.get("status", "ready") != "ready":
            raise RecoverableToolError(
                "upload_not_ready",
                "The uploaded file is still being finalized or deleted.",
                required_action={"action": "retry", "after_seconds": 1},
                retryable=True,
                retry_after=1,
            )
        if int(item["expires_at"]) < int(time.time()):
            self.delete(scope, upload_id)
            raise RecoverableToolError(
                "upload_expired_or_missing",
                "The uploaded file handle has expired.",
                required_action={"tool": "files_file_manager", "arguments": {}},
            )
        response = self.s3.get_object(Bucket=self.bucket, Key=self._object_key(scope, upload_id))
        result = self.keyring.decrypt(response["Body"].read())
        data = result.plaintext
        if sha256(data).hexdigest() != item["checksum_sha256"]:
            raise ValueError("Uploaded object checksum verification failed.")
        if result.needs_rotation:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=self._object_key(scope, upload_id),
                Body=self.keyring.encrypt(data),
                ContentType="application/octet-stream",
            )
        return UploadedFile(
            name=item["display_name"],
            mime_type=item["type"],
            data=data,
            upload_id=upload_id,
            checksum_sha256=item["checksum_sha256"],
            expires_at=item["expires_at"],
        )

    def delete(self, scope: str, upload_id: str) -> bool:
        key = self._metadata_key(scope)
        raw = self.redis.hget(key, upload_id)
        if raw is None:
            return False
        item = json.loads(raw)
        item["status"] = "deleting"
        item["expires_at"] = min(int(item["expires_at"]), int(time.time()) + 300)
        self.redis.hset(key, upload_id, json.dumps(item, separators=(",", ":")))
        self.s3.delete_object(Bucket=self.bucket, Key=self._object_key(scope, upload_id))
        self.redis.hdel(key, upload_id)
        from .production import UPLOAD_REMOVED_BYTES

        UPLOAD_REMOVED_BYTES.labels("s3").inc(int(item.get("size", 0)))
        return True
