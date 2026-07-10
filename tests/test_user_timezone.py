from __future__ import annotations

import asyncio

import pytest

from mcp_google_workspace.common import timezone as timezone_module


def test_resolve_user_timezone_uses_calendar_settings(monkeypatch) -> None:
    request = object()

    class Settings:
        def get(self, *, setting: str):
            assert setting == "timezone"
            return request

    class CalendarService:
        def settings(self):
            return Settings()

    async def fake_execute(actual_request):
        assert actual_request is request
        return {"value": "America/Sao_Paulo"}

    monkeypatch.setattr(timezone_module, "build_calendar_service", CalendarService)
    monkeypatch.setattr(timezone_module, "execute_google_request", fake_execute)

    assert asyncio.run(timezone_module.resolve_user_timezone()) == "America/Sao_Paulo"


def test_resolve_user_timezone_uses_default_for_invalid_calendar_values(monkeypatch) -> None:
    class Settings:
        def get(self, *, setting: str):
            assert setting == "timezone"
            return object()

    class CalendarService:
        def settings(self):
            return Settings()

    async def fake_execute(_request):
        return {"value": "not/a-real-timezone"}

    monkeypatch.setattr(timezone_module, "build_calendar_service", CalendarService)
    monkeypatch.setattr(timezone_module, "execute_google_request", fake_execute)

    with pytest.raises(timezone_module.AccountTimezoneUnavailableError):
        asyncio.run(timezone_module.resolve_user_timezone())
