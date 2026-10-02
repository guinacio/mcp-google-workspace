"""Contract tests for the revisioned app-state store (memory and Redis backends)."""

from __future__ import annotations

import time

import anyio
import burner_redis
import pytest
from cryptography.fernet import Fernet

from mcp_google_workspace.common import app_state
from mcp_google_workspace.common.app_state import (
    MemoryAppStateStore,
    RedisAppStateStore,
    app_state_backend_url,
)
from mcp_google_workspace.common.crypto import FernetKeyring


def _memory() -> MemoryAppStateStore:
    return MemoryAppStateStore()


def _redis() -> RedisAppStateStore:
    return RedisAppStateStore(
        burner_redis.BurnerRedis(),
        keyring=FernetKeyring.single(Fernet.generate_key().decode()),
    )


BACKENDS = pytest.mark.parametrize("factory", [_memory, _redis], ids=["memory", "redis"])


@BACKENDS
def test_create_get_and_revisioned_compare_and_set(factory) -> None:
    store = factory()

    async def scenario():
        created = await store.create("k", {"a": 1}, ttl_seconds=60)
        duplicate = await store.create("k", {"a": 2}, ttl_seconds=60)
        first = await store.compare_and_set("k", 1, {"a": 3}, ttl_seconds=60)
        stale = await store.compare_and_set("k", 1, {"a": 4}, ttl_seconds=60)
        missing = await store.compare_and_set("absent", 1, {"a": 5}, ttl_seconds=60)
        current = await store.get("k")
        deleted = await store.delete("k")
        return created, duplicate, first, stale, missing, current, deleted, await store.get("k")

    created, duplicate, first, stale, missing, current, deleted, gone = anyio.run(scenario)
    assert created is not None and created.revision == 1 and created.value == {"a": 1}
    assert duplicate is None
    assert first.status == "ok" and first.record.revision == 2
    assert stale.status == "conflict"
    assert stale.record.revision == 2 and stale.record.value == {"a": 3}
    assert missing.status == "missing"
    assert current.revision == 2 and current.value == {"a": 3}
    assert deleted is True and gone is None


@BACKENDS
def test_concurrent_compare_and_set_has_exactly_one_winner(factory) -> None:
    store = factory()
    results: list[str] = []

    async def writer(index: int) -> None:
        outcome = await store.compare_and_set("k", 1, {"writer": index}, ttl_seconds=60)
        results.append(outcome.status)

    async def scenario():
        await store.create("k", {"writer": None}, ttl_seconds=60)
        async with anyio.create_task_group() as group:
            for index in range(20):
                group.start_soon(writer, index)
        return await store.get("k")

    final = anyio.run(scenario)
    assert results.count("ok") == 1
    assert results.count("conflict") == 19
    assert final.revision == 2


def test_memory_records_expire_and_reads_can_slide_the_ttl() -> None:
    now = [100.0]
    store = MemoryAppStateStore(clock=lambda: now[0])

    async def read(refresh: float | None = None):
        return await store.get("k", refresh_ttl_seconds=refresh)

    anyio.run(lambda: store.create("k", {}, ttl_seconds=10))
    now[0] = 108.0
    assert anyio.run(read, 10.0) is not None
    now[0] = 116.0
    assert anyio.run(read) is not None, "the refreshed expiry is 118"
    now[0] = 118.5
    assert anyio.run(read) is None
    assert anyio.run(lambda: store.compare_and_set("k", 1, {}, ttl_seconds=10)).status == "missing"


def test_memory_store_is_bounded() -> None:
    store = MemoryAppStateStore(max_entries=3)

    async def scenario():
        for index in range(5):
            await store.create(f"k{index}", {}, ttl_seconds=60 + index)
        return [await store.get(f"k{index}") for index in range(5)]

    survivors = [record is not None for record in anyio.run(scenario)]
    assert survivors == [False, False, True, True, True]


def test_redis_records_expire_through_the_server_ttl() -> None:
    store = _redis()
    anyio.run(lambda: store.create("k", {}, ttl_seconds=0.05))
    time.sleep(0.2)
    assert anyio.run(lambda: store.get("k")) is None


def test_redis_bodies_are_encrypted_and_undecryptable_records_are_discarded() -> None:
    client = burner_redis.BurnerRedis()
    key = Fernet.generate_key().decode()
    store = RedisAppStateStore(client, keyring=FernetKeyring.single(key))
    anyio.run(lambda: store.create("k", {"inbox_query": "from:secret@example.com"}, ttl_seconds=60))

    raw = anyio.run(lambda: client.hgetall("mcp:appstate:v1:k"))
    assert raw[b"rev"] == b"1"
    assert b"secret@example.com" not in raw[b"data"]

    rotated_away = RedisAppStateStore(
        client, keyring=FernetKeyring.single(Fernet.generate_key().decode())
    )
    assert anyio.run(lambda: rotated_away.get("k")) is None
    assert anyio.run(lambda: store.get("k")).value == {"inbox_query": "from:secret@example.com"}


def test_two_store_instances_share_one_redis_backend() -> None:
    client = burner_redis.BurnerRedis()
    keyring = FernetKeyring.single(Fernet.generate_key().decode())
    replica_a = RedisAppStateStore(client, keyring=keyring)
    replica_b = RedisAppStateStore(client, keyring=keyring)

    anyio.run(lambda: replica_a.create("k", {"v": 1}, ttl_seconds=60))
    assert anyio.run(lambda: replica_b.compare_and_set("k", 1, {"v": 2}, ttl_seconds=60)).status == "ok"
    stale = anyio.run(lambda: replica_a.compare_and_set("k", 1, {"v": 3}, ttl_seconds=60))
    assert stale.status == "conflict" and stale.record.value == {"v": 2}


def test_backend_selection_follows_the_shared_redis_convention(monkeypatch) -> None:
    assert app_state_backend_url({}) is None
    assert app_state_backend_url({"MCP_REDIS_URL": "redis://r:6379/0"}) == "redis://r:6379/0"
    # The local stdio bundle never joins a remote fleet's Redis implicitly.
    assert (
        app_state_backend_url({"MCP_REDIS_URL": "redis://r:6379/0", "MCP_RUNTIME_MODE": "bundle"})
        is None
    )

    monkeypatch.delenv("MCP_REDIS_URL", raising=False)
    assert isinstance(app_state.build_app_state_store(), MemoryAppStateStore)

    monkeypatch.setenv("MCP_REDIS_URL", "redis://127.0.0.1:6399/0")
    monkeypatch.delenv("MCP_RUNTIME_MODE", raising=False)
    monkeypatch.setenv("MCP_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    built = app_state.build_app_state_store()
    assert isinstance(built, RedisAppStateStore)
    assert built._keyring is not None

    monkeypatch.delenv("MCP_TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("MCP_TOKEN_ENCRYPTION_KEYS", raising=False)
    monkeypatch.delenv("MCP_SECRET_FILE", raising=False)
    with pytest.raises(ValueError, match="key ring"):
        app_state.build_app_state_store()
