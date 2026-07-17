from __future__ import annotations

import base64
import json
import os
import sqlite3
import time

import pytest
from cryptography.fernet import Fernet
from redis.exceptions import WatchError

from mcp_google_workspace.common.crypto import FernetKeyring
from mcp_google_workspace.common.s3_uploads import S3UploadStore
from mcp_google_workspace.file_uploads import EncryptedUploadStore


class _Pipeline:
    def __init__(self, backend: "_FakeRedis") -> None:
        self.backend = backend
        self.operations: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def watch(self, key):
        if self.backend.watch_failures:
            self.backend.watch_failures -= 1
            raise WatchError()

    def hvals(self, key):
        return list(self.backend.hashes.get(str(key), {}).values())

    def multi(self):
        return None

    def hset(self, key, field, value):
        self.operations.append(("hset", (str(key), str(field), value)))

    def expire(self, key, seconds):
        self.operations.append(("expire", (str(key), int(seconds))))

    def execute(self):
        self.backend.execute_count += 1
        if self.backend.fail_execute_on == self.backend.execute_count:
            raise RuntimeError("injected redis transaction failure")
        for operation, args in self.operations:
            if operation == "hset":
                key, field, value = args
                self.backend.hashes.setdefault(key, {})[field] = value
        return [True] * len(self.operations)


class _FakeRedis:
    def __init__(self, *, watch_failures: int = 0, fail_execute_on: int | None = None):
        self.hashes: dict[str, dict[str, str]] = {}
        self.watch_failures = watch_failures
        self.fail_execute_on = fail_execute_on
        self.execute_count = 0

    def pipeline(self, transaction=True):
        return _Pipeline(self)

    def hgetall(self, key):
        return dict(self.hashes.get(str(key), {}))

    def hvals(self, key):
        return list(self.hashes.get(str(key), {}).values())

    def hget(self, key, field):
        return self.hashes.get(str(key), {}).get(str(field))

    def hset(self, key, field, value):
        self.hashes.setdefault(str(key), {})[str(field)] = value
        return 1

    def hdel(self, key, *fields):
        values = self.hashes.get(str(key), {})
        deleted = 0
        for field in fields:
            deleted += int(values.pop(str(field), None) is not None)
        return deleted


class _FakeS3:
    def __init__(self, *, fail_put_on: int | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_count = 0
        self.fail_put_on = fail_put_on

    def put_object(self, *, Bucket, Key, Body, ContentType):
        self.put_count += 1
        if self.fail_put_on == self.put_count:
            raise RuntimeError("injected object-store failure")
        self.objects[str(Key)] = bytes(Body)

    def delete_object(self, *, Bucket, Key):
        self.objects.pop(str(Key), None)


def _store(redis_backend: _FakeRedis, s3: _FakeS3) -> S3UploadStore:
    store = object.__new__(S3UploadStore)
    store.redis = redis_backend
    store.s3 = s3
    store.bucket = "uploads"
    store.prefix = "workspace/uploads"
    store.ttl_seconds = 3600
    store.quota_bytes = 1_000
    store.keyring = FernetKeyring.single(Fernet.generate_key().decode())
    return store


def _files(count: int = 1) -> list[dict[str, object]]:
    return [
        {
            "name": f"file-{index}.txt",
            "type": "text/plain",
            "data": base64.b64encode(f"value-{index}".encode()).decode(),
        }
        for index in range(count)
    ]


def test_s3_upload_reserves_before_side_effects_and_retries_watch_conflicts() -> None:
    redis_backend = _FakeRedis(watch_failures=1)
    s3 = _FakeS3()
    store = _store(redis_backend, s3)

    result = store.store("alice", _files(), 100)

    assert len(result) == 1
    metadata = json.loads(next(iter(redis_backend.hashes["mcp:uploads:alice"].values())))
    assert metadata["status"] == "ready"
    assert len(s3.objects) == 1


def test_s3_upload_compensates_partial_object_failure() -> None:
    redis_backend = _FakeRedis()
    s3 = _FakeS3(fail_put_on=2)
    store = _store(redis_backend, s3)

    with pytest.raises(RuntimeError, match="object-store"):
        store.store("alice", _files(2), 100)

    assert s3.objects == {}
    assert redis_backend.hashes["mcp:uploads:alice"] == {}


def test_s3_upload_compensates_metadata_finalization_failure() -> None:
    redis_backend = _FakeRedis(fail_execute_on=2)
    s3 = _FakeS3()
    store = _store(redis_backend, s3)

    with pytest.raises(RuntimeError, match="redis transaction"):
        store.store("alice", _files(), 100)

    assert s3.objects == {}
    assert redis_backend.hashes["mcp:uploads:alice"] == {}


def test_s3_delete_keeps_tombstone_when_object_deletion_fails() -> None:
    redis_backend = _FakeRedis()
    s3 = _FakeS3()
    store = _store(redis_backend, s3)
    uploaded = store.store("alice", _files(), 100)[0]

    def fail_delete(**kwargs):
        raise RuntimeError("injected delete failure")

    s3.delete_object = fail_delete  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="delete failure"):
        store.delete("alice", uploaded["upload_id"])

    raw = redis_backend.hget("mcp:uploads:alice", uploaded["upload_id"])
    assert raw is not None
    assert json.loads(raw)["status"] == "deleting"


def test_local_upload_compensates_every_blob_when_metadata_batch_fails(tmp_path) -> None:
    store = EncryptedUploadStore(
        tmp_path / "uploads.sqlite3",
        Fernet.generate_key().decode(),
        quota_bytes=1_000,
    )
    with store._connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_bad_upload BEFORE INSERT ON uploads_v2
            WHEN NEW.original_name = 'bad.txt'
            BEGIN SELECT RAISE(ABORT, 'injected metadata failure'); END
            """
        )

    files = _files(1) + [
        {
            "name": "bad.txt",
            "type": "text/plain",
            "data": base64.b64encode(b"bad").decode(),
        }
    ]
    with pytest.raises(sqlite3.IntegrityError, match="metadata failure"):
        store.store("alice", files, 100)

    assert not list(store.blob_directory.glob("*/*"))
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM uploads_v2").fetchone()[0] == 0


def test_local_reconcile_removes_only_aged_orphan_blobs(tmp_path) -> None:
    store = EncryptedUploadStore(
        tmp_path / "uploads.sqlite3",
        Fernet.generate_key().decode(),
        quota_bytes=1_000,
    )
    referenced_id = store.store("alice", _files(1), 100)[0]["upload_id"]
    referenced_blob = store._blob_path("alice", referenced_id)
    scope_dir = store.blob_directory / "alice"
    fresh_orphan = scope_dir / "upl_fresh_orphan.blob"
    fresh_orphan.write_bytes(b"in-flight store in another thread")
    fresh_tmp = scope_dir / "upl_in_progress.tmp"
    fresh_tmp.write_bytes(b"partial write in another thread")
    old_orphan = scope_dir / "upl_crash_leftover.blob"
    old_orphan.write_bytes(b"crash leftover")
    old_tmp = scope_dir / "upl_crash_partial.tmp"
    old_tmp.write_bytes(b"crash partial")
    aged = time.time() - 3_600
    os.utime(old_orphan, (aged, aged))
    os.utime(old_tmp, (aged, aged))
    os.utime(referenced_blob, (aged, aged))

    store._last_reconcile = 0.0
    store.list("alice")

    assert not old_orphan.exists(), "aged orphan blob must be reconciled"
    assert not old_tmp.exists(), "aged temporary file must be reconciled"
    assert fresh_orphan.exists(), "recent unreferenced blob may be an in-flight store"
    assert fresh_tmp.exists(), "recent temporary file may be an in-flight write"
    assert referenced_blob.exists(), "referenced blobs survive regardless of age"
