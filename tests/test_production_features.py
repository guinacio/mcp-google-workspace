from __future__ import annotations

import json

import anyio
from cryptography.fernet import Fernet
import mcp.types as mt
from fastmcp.server.middleware import MiddlewareContext

from mcp_google_workspace.common.approvals import impact_preview, requires_prepare
from mcp_google_workspace.common.crypto import FernetKeyring
from mcp_google_workspace.common.resources import parse_resource_uri, resource_handle
from mcp_google_workspace.common.errors import _error_envelope
from mcp_google_workspace.common.production import (
    CapabilityCatalogMiddleware,
    _validate_payload_shape,
    build_version_payload,
    readiness_report,
    RUNTIME_STATE,
)
from mcp_google_workspace.server import workspace_mcp


def test_encryption_keyring_reads_old_ciphertext_and_rotates() -> None:
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    old_ring = FernetKeyring({"old": old_key}, "old")
    rotating_ring = FernetKeyring({"old": old_key, "current": new_key}, "current")

    old_ciphertext = old_ring.encrypt(b"credential backup")
    restored = rotating_ring.decrypt(old_ciphertext)
    assert restored.plaintext == b"credential backup"
    assert restored.needs_rotation is True

    rotated_ciphertext = rotating_ring.encrypt(restored.plaintext)
    rotated = rotating_ring.decrypt(rotated_ciphertext)
    assert rotated.plaintext == b"credential backup"
    assert rotated.key_id == "current"
    assert rotated.needs_rotation is False


def test_secret_file_can_supply_versioned_keyring(tmp_path, monkeypatch) -> None:
    secret_file = tmp_path / "workspace-secrets.json"
    key = Fernet.generate_key().decode()
    secret_file.write_text(json.dumps({
        "active_token_encryption_key_id": "2026-07",
        "token_encryption_keys": {"2026-07": key},
    }))
    monkeypatch.setenv("MCP_SECRET_FILE", str(secret_file))
    monkeypatch.delenv("MCP_TOKEN_ENCRYPTION_KEY", raising=False)

    ring = FernetKeyring.from_environment()
    assert ring.active_key_id == "2026-07"
    assert ring.decrypt(ring.encrypt(b"round trip")).plaintext == b"round trip"


def test_resource_handles_round_trip_without_provider_specific_guessing() -> None:
    handle = resource_handle(
        "drive_file",
        "file/id with spaces",
        name="Proposal.pdf",
        mime_type="application/pdf",
    )
    assert handle["uri"].startswith("gdrive:///")
    assert parse_resource_uri(handle["uri"]) == ("drive_file", "file/id with spaces")


def test_consequential_action_policy_is_cost_and_impact_aware() -> None:
    arguments = {
        "subject": "Announcement",
        "to": [f"person-{index}@example.com" for index in range(10)],
    }
    assert requires_prepare("gmail_send_email", arguments)
    preview = impact_preview("gmail_send_email", arguments)
    assert preview["counts"]["to"] == 10
    assert "body" not in preview

    batch = {"message_ids": [f"message-{index}" for index in range(10)]}
    assert requires_prepare("gmail_batch_modify", batch)
    assert impact_preview("gmail_batch_modify", batch)["counts"] == {"messages": 10}


def test_version_payload_advertises_streamable_http_and_current_protocol() -> None:
    payload = build_version_payload()
    assert payload["protocol_transport"] == "streamable-http"
    assert payload["mcp_protocol_version"] == "2025-11-25"


def test_structural_admission_limits_are_enforced() -> None:
    _validate_payload_shape({"safe": ["value"]})
    try:
        _validate_payload_shape({"too_many": [None] * 10_001})
    except ValueError as exc:
        assert "10,000" in str(exc)
    else:  # pragma: no cover - policy invariant
        raise AssertionError("Oversized input was accepted")


def test_recoverable_errors_always_include_next_action() -> None:
    _, envelope = _error_envelope(ValueError("invalid argument"))
    assert envelope["code"] == "invalid_input"
    assert envelope["required_action"] == {
        "action": "correct_arguments",
        "field_errors": [],
    }


def test_readiness_validates_secret_and_storage(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MCP_USER_TOKEN_DIR", str(tmp_path / "tokens"))
    monkeypatch.setenv("MCP_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("MCP_SECRET_FILE", raising=False)
    monkeypatch.delenv("MCP_REDIS_URL", raising=False)
    monkeypatch.delenv("MCP_UPLOAD_S3_BUCKET", raising=False)
    monkeypatch.setenv("MCP_WORKERS", "1")
    RUNTIME_STATE.draining = False
    ready, payload = readiness_report()
    assert ready
    assert payload["checks"]["encryption"]["ok"]


def test_remote_catalog_is_capability_and_transport_aware(monkeypatch) -> None:
    async def exercise() -> tuple[set[str], object]:
        raw_tools = await workspace_mcp.list_tools(run_middleware=False)
        middleware = CapabilityCatalogMiddleware()

        async def call_next(_context):
            return raw_tools

        context = MiddlewareContext(
            message=mt.ListToolsRequest(method="tools/list"),
            method="tools/list",
        )
        visible = await middleware.on_list_tools(context, call_next)
        by_name = {tool.name: tool for tool in visible}
        return set(by_name), by_name["gmail_send_email"].parameters

    monkeypatch.setattr(
        "mcp_google_workspace.common.production.get_access_token", lambda: object()
    )
    monkeypatch.setattr(
        "mcp_google_workspace.auth.google_oauth.google_connection_status",
        lambda: {"granted_capabilities": ["gmail"]},
    )
    names, parameters = anyio.run(exercise)
    assert "gmail_send_email" in names
    assert "drive_list_files" not in names
    assert "gmail_download_attachment" not in names
    attachment_items = parameters["properties"]["attachments"]["anyOf"][0]["items"]
    assert "file_path" not in attachment_items["properties"]
