from __future__ import annotations

from collections.abc import Callable
from typing import Any

import anyio
import pytest

from mcp_google_workspace.drive.tools import permissions as drive_permissions
from mcp_google_workspace.gmail.tools import settings as gmail_settings


class _ToolCapture:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., Any]] = {}

    def tool(self, *, name: str):
        def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
            self.tools[name] = function
            return function

        return decorator


def test_get_forwarding_address_does_not_request_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _ToolCapture()
    gmail_settings.register(capture)  # type: ignore[arg-type]

    class ForwardingAddresses:
        def get(self, **kwargs: Any) -> dict[str, Any]:
            return kwargs

    class Settings:
        def forwardingAddresses(self) -> ForwardingAddresses:
            return ForwardingAddresses()

    class Users:
        def settings(self) -> Settings:
            return Settings()

    class Service:
        def users(self) -> Users:
            return Users()

    async def execute(request: dict[str, Any]) -> dict[str, str]:
        assert request["forwardingEmail"] == "forward@example.com"
        return {"forwardingEmail": "forward@example.com", "verificationStatus": "accepted"}

    async def unexpected_confirmation(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("A read-only getter must not request confirmation")

    monkeypatch.setattr(gmail_settings, "gmail_service", Service)
    monkeypatch.setattr(gmail_settings, "execute_google_request", execute)
    monkeypatch.setattr(gmail_settings, "confirm_destructive_action", unexpected_confirmation)

    result = anyio.run(capture.tools["get_forwarding_address"], "forward@example.com")

    assert result["forwarding_address"]["verificationStatus"] == "accepted"


def test_delete_forwarding_address_cancellation_skips_google_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _ToolCapture()
    gmail_settings.register(capture)  # type: ignore[arg-type]
    confirmation: dict[str, str] = {}

    async def cancel(ctx: Any, action: str, message: str) -> bool:
        confirmation.update(action=action, message=message)
        return False

    monkeypatch.setattr(gmail_settings, "confirm_destructive_action", cancel)
    monkeypatch.setattr(
        gmail_settings,
        "gmail_service",
        lambda: (_ for _ in ()).throw(AssertionError("Google API must not be called")),
    )

    async def call_tool() -> dict[str, Any]:
        return await capture.tools["delete_forwarding_address"](
            "forward@example.com", ctx=object()
        )

    result = anyio.run(call_tool)

    assert result == {"status": "cancelled", "forwarding_email": "forward@example.com"}
    assert confirmation["action"] == "delete_forwarding_address"
    assert "forward@example.com" in confirmation["message"]


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (
            "create_permission",
            {
                "file_id": "file-1",
                "type": "user",
                "role": "owner",
                "email_address": "writer@example.com",
                "transfer_ownership": True,
            },
        ),
        (
            "update_permission",
            {
                "file_id": "file-1",
                "permission_id": "permission-1",
                "role": "writer",
                "allow_file_discovery": True,
            },
        ),
    ],
)
def test_permission_mutation_cancellation_skips_google_api(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    capture = _ToolCapture()
    drive_permissions.register(capture)  # type: ignore[arg-type]
    confirmations: list[tuple[str, str]] = []

    async def cancel(ctx: Any, action: str, message: str) -> bool:
        confirmations.append((action, message))
        return False

    monkeypatch.setattr(drive_permissions, "confirm_destructive_action", cancel)
    monkeypatch.setattr(
        drive_permissions,
        "drive_service",
        lambda: (_ for _ in ()).throw(AssertionError("Google API must not be called")),
    )

    async def call_tool() -> dict[str, Any]:
        return await capture.tools[tool_name](**arguments, ctx=object())

    result = anyio.run(call_tool)

    assert result["status"] == "cancelled"
    assert result["file_id"] == "file-1"
    assert confirmations[0][0] == tool_name
    assert "file-1" in confirmations[0][1]
    if tool_name == "create_permission":
        assert "role owner" in confirmations[0][1]
        assert "transfer ownership" in confirmations[0][1]
    else:
        assert "set role to writer" in confirmations[0][1]
        assert "set file discovery to True" in confirmations[0][1]
