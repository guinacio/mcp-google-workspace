from __future__ import annotations

from datetime import date

import anyio
import pytest

from mcp_google_workspace.apps import state as apps_state
from mcp_google_workspace.apps.schemas import DashboardState, DashboardStatePatch
from mcp_google_workspace.apps.state import (
    DashboardViewService,
    ViewHandleError,
    ViewStateConflict,
    next_range,
    patch_state,
    prev_range,
)
from mcp_google_workspace.auth.identity import Principal
from mcp_google_workspace.common.app_state import MemoryAppStateStore

ALICE = Principal(issuer="https://issuer.example", subject="alice")
BOB = Principal(issuer="https://issuer.example", subject="bob")


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class _YieldingStore:
    """Yield to the event loop before every store call to force interleaving."""

    def __init__(self, inner: MemoryAppStateStore) -> None:
        self.inner = inner
        self.backend_name = inner.backend_name

    async def create(self, key, value, *, ttl_seconds):
        await anyio.sleep(0)
        return await self.inner.create(key, value, ttl_seconds=ttl_seconds)

    async def get(self, key, *, refresh_ttl_seconds=None):
        await anyio.sleep(0)
        return await self.inner.get(key, refresh_ttl_seconds=refresh_ttl_seconds)

    async def compare_and_set(self, key, expected_revision, value, *, ttl_seconds):
        await anyio.sleep(0)
        return await self.inner.compare_and_set(
            key, expected_revision, value, ttl_seconds=ttl_seconds
        )

    async def delete(self, key):
        await anyio.sleep(0)
        return await self.inner.delete(key)


def _as(monkeypatch: pytest.MonkeyPatch, principal: Principal) -> None:
    monkeypatch.setattr(apps_state, "current_principal", lambda: principal)


def _service(clock: _Clock | None = None, ttl: int = 3600) -> DashboardViewService:
    clock = clock or _Clock()
    return DashboardViewService(
        MemoryAppStateStore(clock=clock), ttl_seconds=ttl, clock=clock
    )


def test_state_defaults_and_patch() -> None:
    state = DashboardState()
    assert state.view == "week"

    updated = patch_state(
        state,
        DashboardStatePatch(
            view="day", timezone="America/Sao_Paulo", selected_calendars=["primary", "team"]
        ),
    )
    assert updated.view == "day"
    assert updated.timezone == "America/Sao_Paulo"
    assert updated.selected_calendars == ["primary", "team"]
    assert state.view == "week", "transitions never mutate their input"


def test_state_navigation() -> None:
    state = DashboardState(view="week", anchor_date=date(2026, 3, 1))
    forward = next_range(state)
    assert forward.anchor_date == date(2026, 3, 8)
    assert prev_range(forward).anchor_date == date(2026, 3, 1)


def test_month_navigation_uses_calendar_months_and_clamps_day() -> None:
    state = DashboardState(view="month", anchor_date=date(2026, 1, 31))
    assert next_range(state).anchor_date == date(2026, 2, 28)
    assert prev_range(next_range(state)).anchor_date == date(2026, 1, 28)


def test_dashboard_state_is_closed_and_has_no_transport_session_field() -> None:
    assert "session_id" not in DashboardState.model_fields
    with pytest.raises(ValueError):
        DashboardState.model_validate({"session_id": "legacy"})


def test_handles_are_unguessable_and_every_view_is_isolated(monkeypatch) -> None:
    _as(monkeypatch, ALICE)
    service = _service()

    async def scenario():
        first = await service.create(DashboardState(anchor_date=date(2026, 3, 1)))
        second = await service.create(DashboardState(anchor_date=date(2026, 3, 1)))
        await service.update(
            first.handle, lambda s: s.model_copy(update={"view": "day"}), expected_revision=1
        )
        return first, second, await service.resolve(first.handle), await service.resolve(second.handle)

    first, second, first_now, second_now = anyio.run(scenario)
    assert first.handle != second.handle
    for handle in (first.handle, second.handle):
        assert apps_state.HANDLE_PATTERN.fullmatch(handle)
        # 43 URL-safe base64 characters = 256 random bits (>= 128 required).
        assert len(handle) - len(apps_state.HANDLE_PREFIX) == 43
    assert first_now.state.view == "day" and first_now.revision == 2
    assert second_now.state.view == "week" and second_now.revision == 1


def test_handle_is_never_stored_and_record_is_principal_bound(monkeypatch) -> None:
    _as(monkeypatch, ALICE)
    store = MemoryAppStateStore()
    service = DashboardViewService(store, ttl_seconds=3600)
    view = anyio.run(service.create, DashboardState())
    keys = list(store._entries)
    assert len(keys) == 1
    assert view.handle not in keys[0]
    assert keys[0].startswith(f"view:{ALICE.storage_key}:")
    assert view.handle not in store._entries[keys[0]][1]


@pytest.mark.parametrize(
    "handle",
    ["", "session-1", "wsv_short", "wsv_" + "!" * 43, "WSV_" + "a" * 43, "wsv_" + "a" * 44],
)
def test_malformed_handles_are_rejected(monkeypatch, handle: str) -> None:
    _as(monkeypatch, ALICE)
    with pytest.raises(ViewHandleError) as raised:
        anyio.run(_service().resolve, handle)
    assert raised.value.reason == "malformed"
    assert raised.value.code == "view_handle_invalid"


def test_guessed_handles_and_other_principals_resolve_to_nothing(monkeypatch) -> None:
    _as(monkeypatch, ALICE)
    service = _service()
    view = anyio.run(service.create, DashboardState())

    with pytest.raises(ViewHandleError) as guessed:
        anyio.run(service.resolve, apps_state.mint_view_handle())
    assert guessed.value.reason == "unknown_or_expired"

    _as(monkeypatch, BOB)
    with pytest.raises(ViewHandleError) as foreign:
        anyio.run(service.resolve, view.handle)
    # Indistinguishable from an unknown handle: no existence oracle.
    assert foreign.value.reason == "unknown_or_expired"
    assert str(foreign.value) == str(guessed.value)
    with pytest.raises(ViewHandleError):
        anyio.run(
            lambda: service.update(view.handle, lambda s: s.model_copy(update={"view": "month"}))
        )

    _as(monkeypatch, ALICE)
    assert anyio.run(service.resolve, view.handle).state.view == "week"


def test_views_expire_after_a_sliding_idle_ttl(monkeypatch) -> None:
    _as(monkeypatch, ALICE)
    clock = _Clock()
    service = _service(clock, ttl=100)
    view = anyio.run(service.create, DashboardState())
    assert view.expires_at == int(clock.now) + 100

    clock.now += 90
    assert anyio.run(service.resolve, view.handle).revision == 1  # use slides the expiry
    clock.now += 90
    assert anyio.run(service.resolve, view.handle).revision == 1
    clock.now += 101
    with pytest.raises(ViewHandleError) as expired:
        anyio.run(service.resolve, view.handle)
    assert expired.value.reason == "unknown_or_expired"


def test_stale_conditional_update_gets_a_deterministic_conflict(monkeypatch) -> None:
    _as(monkeypatch, ALICE)
    service = _service()

    async def scenario():
        view = await service.create(DashboardState(anchor_date=date(2026, 3, 1)))
        await service.update(view.handle, next_range, expected_revision=1)
        try:
            await service.update(
                view.handle, lambda s: s.model_copy(update={"view": "month"}), expected_revision=1
            )
        except ViewStateConflict as conflict:
            return conflict, await service.resolve(view.handle)
        raise AssertionError("stale write was accepted")

    conflict, latest = anyio.run(scenario)
    assert conflict.code == "view_state_conflict"
    assert conflict.expected_revision == 1
    assert conflict.current.revision == 2
    assert conflict.current.state.anchor_date == date(2026, 3, 8)
    assert latest.state.view == "week", "the stale write changed nothing"


def test_concurrent_conditional_updates_have_exactly_one_winner(monkeypatch) -> None:
    _as(monkeypatch, ALICE)
    service = DashboardViewService(_YieldingStore(MemoryAppStateStore()), ttl_seconds=3600)
    outcomes: list[str] = []

    async def writer(handle: str, view: str) -> None:
        try:
            await service.update(
                handle, lambda s: s.model_copy(update={"view": view}), expected_revision=1
            )
            outcomes.append(f"ok:{view}")
        except ViewStateConflict:
            outcomes.append("conflict")

    async def scenario():
        created = await service.create(DashboardState())
        async with anyio.create_task_group() as group:
            for view in ["day", "month", "agenda"] * 4:
                group.start_soon(writer, created.handle, view)
        return await service.resolve(created.handle)

    final = anyio.run(scenario)
    winners = [outcome for outcome in outcomes if outcome.startswith("ok:")]
    assert len(winners) == 1
    assert outcomes.count("conflict") == 11
    assert final.revision == 2
    assert f"ok:{final.state.view}" == winners[0]


def test_unconditional_updates_never_lose_a_concurrent_change(monkeypatch) -> None:
    _as(monkeypatch, ALICE)
    service = DashboardViewService(_YieldingStore(MemoryAppStateStore()), ttl_seconds=3600)

    async def scenario():
        created = await service.create(DashboardState(anchor_date=date(2026, 3, 1)))
        async with anyio.create_task_group() as group:
            for _ in range(10):
                group.start_soon(service.update, created.handle, next_range)
        return await service.resolve(created.handle)

    final = anyio.run(scenario)
    assert final.revision == 11
    assert final.state.anchor_date == date(2026, 5, 10)  # ten weekly steps


def test_view_ttl_is_configurable_and_validated(monkeypatch) -> None:
    monkeypatch.delenv("MCP_APP_VIEW_TTL_SECONDS", raising=False)
    assert apps_state.view_ttl_seconds_from_environment() == 24 * 60 * 60
    monkeypatch.setenv("MCP_APP_VIEW_TTL_SECONDS", "600")
    assert apps_state.view_ttl_seconds_from_environment() == 600
    assert DashboardViewService(MemoryAppStateStore()).ttl_seconds == 600
    for invalid in ("59", "2592001", "soon"):
        monkeypatch.setenv("MCP_APP_VIEW_TTL_SECONDS", invalid)
        with pytest.raises(ValueError, match="MCP_APP_VIEW_TTL_SECONDS"):
            apps_state.view_ttl_seconds_from_environment()
