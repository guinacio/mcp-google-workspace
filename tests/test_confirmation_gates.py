from __future__ import annotations

from collections.abc import Callable
from typing import Any

import anyio
import pytest

from consent_testing import answer_consent, gated_tools
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


def test_get_forwarding_address_action_does_not_request_confirmation(
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

    monkeypatch.setattr(gmail_settings, "gmail_service", Service)
    monkeypatch.setattr(gmail_settings, "execute_google_request", execute)
    answers = answer_consent(monkeypatch, True)
    tools = gated_tools(capture.tools, "gmail")

    async def call_get() -> dict[str, Any]:
        return await tools["manage_forwarding"]("get", "forward@example.com")

    result = anyio.run(call_get)

    assert result["forwarding_address"]["verificationStatus"] == "accepted"
    assert answers.asked == []  # a read-only getter never asks


def test_delete_forwarding_address_action_cancellation_skips_google_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _ToolCapture()
    gmail_settings.register(capture)  # type: ignore[arg-type]
    answers = answer_consent(monkeypatch, False)
    tools = gated_tools(capture.tools, "gmail")
    monkeypatch.setattr(
        gmail_settings,
        "gmail_service",
        lambda: (_ for _ in ()).throw(AssertionError("Google API must not be called")),
    )

    async def call_tool() -> dict[str, Any]:
        return await tools["manage_forwarding"]("delete", "forward@example.com", ctx=None)

    result = anyio.run(call_tool)

    assert result["status"] == "cancelled"
    assert result["action"] == "delete_forwarding_address"
    ((action, message),) = answers.asked
    assert action == "delete_forwarding_address"
    assert "forward@example.com" in message


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
    answers = answer_consent(monkeypatch, False)
    confirmations = answers.asked
    tools = gated_tools(capture.tools, "drive")
    monkeypatch.setattr(
        drive_permissions,
        "drive_service",
        lambda: (_ for _ in ()).throw(AssertionError("Google API must not be called")),
    )

    async def call_tool() -> dict[str, Any]:
        # Both grant paths are drive_share_file now; permission_id selects update.
        return await tools["share_file"](**arguments, ctx=None)

    result = anyio.run(call_tool)

    assert result["status"] == "cancelled"
    assert result["action"] == tool_name
    assert confirmations[0][0] == tool_name
    assert "file-1" in confirmations[0][1]
    if tool_name == "create_permission":
        assert "with user writer@example.com as owner" in confirmations[0][1]
        assert "transfers ownership" in confirmations[0][1]
    else:
        assert "permission-1" in confirmations[0][1]
        assert "role writer" in confirmations[0][1]
        assert "discoverable True" in confirmations[0][1]
