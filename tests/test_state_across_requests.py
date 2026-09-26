"""W3: application state and uploads outlive transport connections.

Every call here is an independent MCP 2026-07-28 HTTP request (no initialize,
no ``Mcp-Session-Id``), authenticated with a bearer token, and possibly served
by a different "replica": two independently constructed FastMCP servers that
share one app-state backend (an in-memory Redis) and one upload backend
(Redis-style metadata + S3-style objects).
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from contextlib import ExitStack
from io import BytesIO
from itertools import count
import threading
from typing import Any

import anyio
import burner_redis
import pytest
from cryptography.fernet import Fernet
from fastmcp import Client, FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.providers.addressing import hash_tool
from starlette.testclient import TestClient

import mcp_google_workspace.apps.tools as apps_tools
from mcp_google_workspace.apps.server import create_apps_server
from mcp_google_workspace.apps.state import DashboardViewService, mint_view_handle
from mcp_google_workspace.common.app_state import MemoryAppStateStore, RedisAppStateStore
from mcp_google_workspace.common.crypto import FernetKeyring
from mcp_google_workspace.common.s3_uploads import S3UploadStore
from mcp_google_workspace.file_uploads import LocalUploadStore, WorkspaceFileUpload

MODERN = "2026-07-28"
ISSUER = "https://issuer.example.test"
TOKENS: dict[str, dict[str, Any]] = {
    "alice-token": {"client_id": "host-a", "iss": ISSUER, "sub": "alice", "scopes": []},
    "alice-second-client": {"client_id": "host-b", "iss": ISSUER, "sub": "alice", "scopes": []},
    "bob-token": {"client_id": "host-a", "iss": ISSUER, "sub": "bob", "scopes": []},
}
STORE_FILES = f"{hash_tool('Workspace Files', 'store_files')}_store_files"


class _SyncPipeline:
    def __init__(self, backend: "_SharedSyncRedis") -> None:
        self.backend = backend
        self.operations: list[tuple[str, str, str]] = []

    def __enter__(self) -> "_SyncPipeline":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def watch(self, key: str) -> None:
        return None

    def hvals(self, key: str) -> list[str]:
        return self.backend.hvals(key)

    def multi(self) -> None:
        return None

    def hset(self, key: str, field: str, value: str) -> None:
        self.operations.append((str(key), str(field), value))

    def expire(self, key: str, seconds: int) -> None:
        return None

    def execute(self) -> list[bool]:
        for key, field, value in self.operations:
            self.backend.hset(key, field, value)
        return [True] * len(self.operations)


class _SharedSyncRedis:
    """The subset of synchronous Redis used by ``S3UploadStore`` (shared, thread-safe)."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.lock = threading.RLock()

    def pipeline(self, transaction: bool = True) -> _SyncPipeline:
        return _SyncPipeline(self)

    def hgetall(self, key: str) -> dict[str, str]:
        with self.lock:
            return dict(self.hashes.get(str(key), {}))

    def hvals(self, key: str) -> list[str]:
        with self.lock:
            return list(self.hashes.get(str(key), {}).values())

    def hget(self, key: str, field: str) -> str | None:
        with self.lock:
            return self.hashes.get(str(key), {}).get(str(field))

    def hset(self, key: str, field: str, value: str) -> int:
        with self.lock:
            self.hashes.setdefault(str(key), {})[str(field)] = value
            return 1

    def hdel(self, key: str, *fields: str) -> int:
        with self.lock:
            values = self.hashes.get(str(key), {})
            return sum(int(values.pop(str(field), None) is not None) for field in fields)


class _SharedObjects:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        self.objects[Key] = bytes(Body)

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        return {"Body": BytesIO(self.objects[Key])}

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop(Key, None)


class _Backends:
    """One shared app-state Redis and one shared upload backend for all replicas."""

    def __init__(self) -> None:
        self.state_redis = burner_redis.BurnerRedis()
        self.keyring = FernetKeyring.single(Fernet.generate_key().decode())
        self.upload_redis = _SharedSyncRedis()
        self.objects = _SharedObjects()

    def upload_store(self) -> S3UploadStore:
        store = object.__new__(S3UploadStore)
        store.redis = self.upload_redis  # type: ignore[assignment]
        store.s3 = self.objects
        store.bucket = "uploads"
        store.prefix = "workspace/uploads"
        store.ttl_seconds = 3600
        store.quota_bytes = 10_000
        store.keyring = self.keyring
        return store

    def replica(self) -> FastMCP:
        """A fresh, independently constructed server over the shared backends."""
        server = FastMCP("replica", auth=StaticTokenVerifier(TOKENS))
        views = DashboardViewService(
            RedisAppStateStore(self.state_redis, keyring=self.keyring), ttl_seconds=3600
        )
        server.mount(create_apps_server(views), namespace="apps")
        server.add_provider(WorkspaceFileUpload(remote_store=self.upload_store()), namespace="files")
        return server


class _Wire:
    """Raw modern JSON-RPC over HTTP: every call is its own request."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.ids = count(1)

    def call(self, token: str, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "MCP-Protocol-Version": MODERN,
                "Mcp-Method": "tools/call",
                "Mcp-Name": name,
            },
            json={
                "jsonrpc": "2.0",
                "id": next(self.ids),
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": arguments or {},
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": MODERN,
                        "io.modelcontextprotocol/clientInfo": {"name": "w3-test", "version": "0"},
                        "io.modelcontextprotocol/clientCapabilities": {},
                    },
                },
            },
        )
        assert response.status_code == 200, response.text
        assert "mcp-session-id" not in response.headers
        body = response.json()
        assert "error" not in body, body
        return body["result"]


@pytest.fixture(autouse=True)
def _fake_google(monkeypatch: pytest.MonkeyPatch) -> None:
    async def timezone() -> str:
        return "America/Sao_Paulo"

    async def dashboard(state, ctx) -> dict[str, Any]:
        return {"state": state.model_dump(mode="json")}

    async def weekly(state, *, ctx, date_override=None, include_weekend_override=None):
        return {"state": state.model_dump(mode="json")}

    monkeypatch.setattr(apps_tools, "resolve_user_timezone", timezone)
    monkeypatch.setattr(apps_tools, "build_dashboard_payload_with_progress", dashboard)
    monkeypatch.setattr(apps_tools, "build_weekly_calendar_payload_with_progress", weekly)


@pytest.fixture()
def replicas() -> Iterator[tuple[_Wire, _Wire]]:
    backends = _Backends()
    with ExitStack() as stack:
        wires = []
        for _ in range(2):
            app = backends.replica().http_app(
                transport="http",
                stateless_http=False,
                json_response=True,
                allowed_hosts=["testserver"],
                allowed_origins=["http://testserver"],
            )
            wires.append(_Wire(stack.enter_context(TestClient(app))))
        yield wires[0], wires[1]


def _upload(name: str, content: bytes) -> dict[str, Any]:
    return {
        "files": [
            {
                "name": name,
                "size": len(content),
                "type": "text/plain",
                "data": base64.b64encode(content).decode("ascii"),
            }
        ]
    }


def test_view_state_survives_independent_requests_across_replicas(replicas) -> None:
    a, b = replicas
    opened = a.call("alice-token", "apps_get_dashboard")
    assert opened["isError"] is False
    view = opened["_meta"]["mcp-google-workspace/view"]
    assert opened["structuredContent"]["view"] == view
    handle = view["handle"]

    patched = b.call(
        "alice-token",
        "apps_patch_state",
        {"view_handle": handle, "expected_revision": 1, "view": "day", "anchor_date": "2026-03-05"},
    )
    assert patched["isError"] is False
    assert patched["structuredContent"]["view"]["revision"] == 2

    moved = a.call("alice-token", "apps_next_range", {"view_handle": handle, "expected_revision": 2})
    assert moved["structuredContent"]["state"]["anchor_date"] == "2026-03-06"

    state = b.call("alice-token", "apps_get_state", {"view_handle": handle})
    assert state["structuredContent"]["state"]["view"] == "day"
    assert state["structuredContent"]["state"]["anchor_date"] == "2026-03-06"
    assert state["structuredContent"]["view"]["revision"] == 3

    reopened = b.call("alice-token", "apps_get_weekly_calendar_view", {"view_handle": handle})
    assert reopened["structuredContent"]["state"]["anchor_date"] == "2026-03-06"
    assert reopened["_meta"]["mcp-google-workspace/view"]["handle"] == handle


def test_stale_writer_on_another_replica_gets_a_deterministic_conflict(replicas) -> None:
    a, b = replicas
    handle = a.call("alice-token", "apps_get_dashboard")["structuredContent"]["view"]["handle"]

    winner = a.call("alice-token", "apps_patch_state", {"view_handle": handle, "expected_revision": 1, "view": "month"})
    loser = b.call("alice-token", "apps_patch_state", {"view_handle": handle, "expected_revision": 1, "view": "day"})

    assert winner["isError"] is False
    assert loser["isError"] is True
    error = loser["structuredContent"]
    assert error["code"] == "view_state_conflict"
    assert error["details"] == {"expected_revision": 1, "current_revision": 2}
    assert error["state"]["view"] == "month"
    assert error["view"]["revision"] == 2
    assert loser["_meta"]["mcp-google-workspace/view"]["revision"] == 2
    assert "[code: view_state_conflict]" in loser["content"][0]["text"]
    final = b.call("alice-token", "apps_get_state", {"view_handle": handle})
    assert final["structuredContent"]["state"]["view"] == "month"


def test_handles_are_bound_to_the_principal_not_the_client(replicas) -> None:
    a, b = replicas
    handle = a.call("alice-token", "apps_get_dashboard")["structuredContent"]["view"]["handle"]

    foreign = b.call("bob-token", "apps_get_state", {"view_handle": handle})
    guessed = b.call("bob-token", "apps_get_state", {"view_handle": mint_view_handle()})
    tampered = b.call("bob-token", "apps_patch_state", {"view_handle": handle, "view": "day"})
    for result in (foreign, guessed, tampered):
        assert result["isError"] is True
        assert result["structuredContent"]["code"] == "view_handle_invalid"
        assert result["structuredContent"]["details"] == {"reason": "unknown_or_expired"}
    # A foreign handle is indistinguishable from a guessed one.
    assert foreign["structuredContent"] == guessed["structuredContent"]

    malformed = b.call("alice-token", "apps_get_state", {"view_handle": "ui-1727000000-abc123"})
    assert malformed["structuredContent"]["details"] == {"reason": "malformed"}

    # Same (issuer, subject) through another OAuth client: the handle is the
    # per-view capability, so client-id scoping is unnecessary.
    same_user = a.call("alice-second-client", "apps_get_state", {"view_handle": handle})
    assert same_user["isError"] is False
    untouched = a.call("alice-token", "apps_get_state", {"view_handle": handle})
    assert untouched["structuredContent"]["view"]["revision"] == 1


def test_two_views_of_one_principal_are_isolated(replicas) -> None:
    a, b = replicas
    first = a.call("alice-token", "apps_get_dashboard")["structuredContent"]["view"]["handle"]
    second = b.call("alice-token", "apps_get_weekly_calendar_view")["structuredContent"]["view"]["handle"]
    assert first != second

    b.call("alice-token", "apps_patch_state", {"view_handle": first, "include_weekend": False, "view": "day"})

    one = a.call("alice-token", "apps_get_state", {"view_handle": first})["structuredContent"]
    two = a.call("alice-token", "apps_get_state", {"view_handle": second})["structuredContent"]
    assert (one["state"]["view"], one["state"]["include_weekend"]) == ("day", False)
    assert (two["state"]["view"], two["state"]["include_weekend"]) == ("week", True)
    assert two["view"]["revision"] == 1


def test_uploads_survive_independent_requests_across_replicas_and_stay_principal_scoped(
    replicas,
) -> None:
    a, b = replicas
    stored = a.call("alice-token", STORE_FILES, _upload("notes.txt", b"alice data"))
    assert stored["isError"] is False
    entry = stored["structuredContent"]["result"][0]
    upload_id = entry["upload_id"]
    assert upload_id.startswith("upl_") and entry["display_name"] == "notes.txt"

    listed = b.call("alice-token", "files_list_files")["structuredContent"]["result"]
    assert [item["upload_id"] for item in listed] == [upload_id]
    read = b.call("alice-token", "files_read_file", {"name": upload_id})["structuredContent"]["result"]
    assert read["content"] == "alice data"

    assert b.call("bob-token", "files_list_files")["structuredContent"]["result"] == []
    bob_read = b.call("bob-token", "files_read_file", {"name": upload_id})
    assert bob_read["isError"] is True
    bob_delete = a.call("bob-token", "files_delete_file", {"name": upload_id})
    assert bob_delete["structuredContent"] == {"status": "not_found", "name": upload_id}

    deleted = a.call("alice-token", "files_delete_file", {"name": upload_id})
    assert deleted["structuredContent"] == {"status": "deleted", "name": upload_id}
    assert b.call("alice-token", "files_list_files")["structuredContent"]["result"] == []


def _local_replica() -> FastMCP:
    server = FastMCP("local")
    server.mount(
        create_apps_server(DashboardViewService(MemoryAppStateStore(), ttl_seconds=3600)),
        namespace="apps",
    )
    server.add_provider(WorkspaceFileUpload(local_store=LocalUploadStore()), namespace="files")
    return server


@pytest.mark.parametrize("mode", ["legacy", MODERN])
def test_trusted_local_dashboard_and_picker_work_on_both_protocol_eras(mode: str) -> None:
    server = _local_replica()

    async def call(name: str, arguments: dict[str, Any] | None = None):
        # A fresh client per call: nothing may depend on connection scope.
        async with Client(server, mode=mode) as client:  # type: ignore[arg-type]
            expected = "2025-11-25" if mode == "legacy" else MODERN
            assert client.protocol_version == expected
            return await client.call_tool(name, arguments or {}, raise_on_error=False)

    async def scenario():
        handle = (await call("apps_get_dashboard")).structured_content["view"]["handle"]
        await call("apps_patch_state", {"view_handle": handle, "expected_revision": 1, "view": "day"})
        state = await call("apps_get_state", {"view_handle": handle})
        stored = await call(STORE_FILES, _upload("local.txt", b"local bytes"))
        upload_id = stored.structured_content["result"][0]["upload_id"]
        listed = await call("files_list_files")
        read = await call("files_read_file", {"name": upload_id})
        deleted = await call("files_delete_file", {"name": upload_id})
        after = await call("files_list_files")
        return state, upload_id, listed, read, deleted, after

    state, upload_id, listed, read, deleted, after = anyio.run(scenario)
    assert state.structured_content["state"]["view"] == "day"
    assert state.structured_content["view"]["revision"] == 2
    assert [item["upload_id"] for item in listed.structured_content["result"]] == [upload_id]
    assert read.structured_content["result"]["content"] == "local bytes"
    assert deleted.structured_content == {"status": "deleted", "name": upload_id}
    assert after.structured_content["result"] == []
