"""Cross-replica encrypted upload storage backed by S3 and Redis."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import secrets
import time
from typing import Any

import boto3
import redis
from redis.exceptions import WatchError

from .errors import RecoverableToolError
from ..runtime import get_token_storage_settings


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
        for raw_upload_id, raw in self.redis.hgetall(key).items():
            upload_id = str(raw_upload_id)
            if int(json.loads(raw)["expires_at"]) < now:
                expired.append(upload_id)
                self.s3.delete_object(Bucket=self.bucket, Key=self._object_key(scope, upload_id))
        if expired:
            self.redis.hdel(key, *expired)
            from .production import UPLOAD_CLEANUPS

            UPLOAD_CLEANUPS.labels("s3").inc(len(expired))

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
                "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "expires_at": now + self.ttl_seconds,
            }
            prepared.append((upload_id, metadata, self.keyring.encrypt(data)))

        key = self._metadata_key(scope)
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
                    for upload_id, _, encrypted in prepared:
                        self.s3.put_object(
                            Bucket=self.bucket,
                            Key=self._object_key(scope, upload_id),
                            Body=encrypted,
                            ContentType="application/octet-stream",
                        )
                    pipe.multi()
                    for upload_id, metadata, _ in prepared:
                        pipe.hset(key, upload_id, json.dumps(metadata, separators=(",", ":")))
                    pipe.expire(key, self.ttl_seconds + 300)
                    pipe.execute()
                    break
            except WatchError:
                continue
        return self.list(scope)

    def list(self, scope: str) -> list[dict[str, Any]]:
        self._delete_expired(scope)
        items = sorted(
            (json.loads(raw) for raw in self.redis.hvals(self._metadata_key(scope))),
            key=lambda item: item["uploaded_at"],
        )
        remaining = self.quota_bytes - sum(int(item["size"]) for item in items)
        from .production import UPLOAD_BYTES

        UPLOAD_BYTES.labels(scope[:16], "s3").set(sum(int(item["size"]) for item in items))
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
        deleted = bool(self.redis.hdel(self._metadata_key(scope), upload_id))
        if deleted:
            self.s3.delete_object(Bucket=self.bucket, Key=self._object_key(scope, upload_id))
        return deleted
