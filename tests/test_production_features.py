from __future__ import annotations

from importlib.metadata import version
import json
import logging

import anyio
from cryptography.fernet import Fernet
from fastmcp.exceptions import McpError
import mcp.types as mt
import pytest
from types import SimpleNamespace
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools import ToolResult

import mcp_google_workspace
from mcp_google_workspace.common.approvals import (
    COMMIT_ACTIVE,
    ClaimedApproval,
    impact_preview,
    requires_prepare,
)
from mcp_google_workspace.common.crypto import FernetKeyring
from mcp_google_workspace.common.resources import parse_resource_uri, resource_handle
from mcp_google_workspace.common.errors import (
    RPC_RATE_LIMITED,
    ConfirmationRequiredError,
    RecoverableToolError,
    StructuredToolErrorMiddleware,
    _error_envelope,
)
from mcp_google_workspace.common.production import (
    AdmissionError,
    CapabilityCatalogMiddleware,
    ProductionControlMiddleware,
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


def test_approved_commit_reenters_middleware_with_only_prepare_gate_bypassed(
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}
    settled: list[tuple[str, str]] = []

    async def exercise() -> dict[str, object]:
        tool = await workspace_mcp.get_tool("commit_workspace_action")
        assert tool is not None

        async def dispatch(name, arguments, **kwargs):
            observed.update(
                name=name,
                arguments=arguments,
                kwargs=kwargs,
                commit_active=COMMIT_ACTIVE.get(),
            )
            return ToolResult(structured_content={"ok": True})

        monkeypatch.setattr(
            "mcp_google_workspace.server.APPROVAL_STORE.claim",
            lambda token: ClaimedApproval(
                token, "gmail_batch_modify", {"message_ids": ["m"] * 10}
            ),
        )
        monkeypatch.setattr(
            "mcp_google_workspace.server.APPROVAL_STORE.complete",
            lambda token: settled.append(("complete", token)),
        )
        monkeypatch.setattr(
            "mcp_google_workspace.server.APPROVAL_STORE.release",
            lambda token: settled.append(("release", token)),
        )
        monkeypatch.setattr(
            "mcp_google_workspace.server.workspace_mcp",
            SimpleNamespace(call_tool=dispatch),
        )
        return await tool.fn("cmt_test")

    result = anyio.run(exercise)
    assert observed == {
        "name": "gmail_batch_modify",
        "arguments": {"message_ids": ["m"] * 10},
        "kwargs": {},
        "commit_active": True,
    }
    assert result["status"] == "committed"
    assert settled == [("complete", "cmt_test")]
    assert COMMIT_ACTIVE.get() is False


def test_commit_context_does_not_bypass_revocation_admission(monkeypatch) -> None:
    async def exercise() -> None:
        middleware = ProductionControlMiddleware()
        monkeypatch.setattr(middleware, "_principal", lambda: "revoked-principal")
        monkeypatch.setattr(
            "mcp_google_workspace.common.production._principal_revoked",
            lambda _principal: True,
        )
        context = MiddlewareContext(
            message=mt.CallToolRequestParams(
                name="gmail_batch_modify",
                arguments={"message_ids": ["m"] * 10},
            ),
            method="tools/call",
        )

        async def call_next(_context):  # pragma: no cover - must never run
            raise AssertionError("revoked commit reached the provider")

        token = COMMIT_ACTIVE.set(True)
        try:
            with pytest.raises(RecoverableToolError, match="invalidated"):
                await middleware.on_call_tool(context, call_next)
        finally:
            COMMIT_ACTIVE.reset(token)

    anyio.run(exercise)


def test_version_payload_advertises_streamable_http_and_current_protocol() -> None:
    payload = build_version_payload()
    assert payload["protocol_transport"] == "streamable-http"
    # W2: was "2025-11-25" under FastMCP 3 / SDK 1. The preferred revision is now
    # the stateless 2026-07-28 era; tested legacy support is reported separately
    # and never conflated with the package version.
    assert payload["mcp_protocol_version"] == "2026-07-28"
    versions = payload["mcp_protocol_versions"]
    assert versions["preferred"] == "2026-07-28"
    assert versions["modern"] == ["2026-07-28"]
    assert versions["tested"] == ["2025-11-25", "2026-07-28"]
    assert set(versions["tested"]) <= set(versions["modern"]) | set(versions["legacy"])
    assert payload["version"] not in versions["tested"]


def test_tested_protocol_versions_are_actually_negotiated() -> None:
    from fastmcp import Client

    from mcp_google_workspace.server import workspace_mcp

    async def negotiate(mode: str) -> tuple[str | None, int]:
        async with Client(workspace_mcp, mode=mode) as client:  # type: ignore[arg-type]
            tools = await client.list_tools()
            return client.protocol_version, len(tools)

    modern_version, modern_tools = anyio.run(negotiate, "auto")
    legacy_version, legacy_tools = anyio.run(negotiate, "legacy")
    assert modern_version == "2026-07-28"
    assert legacy_version == "2025-11-25"
    assert modern_tools == legacy_tools


def test_exported_package_version_matches_installed_metadata() -> None:
    assert mcp_google_workspace.__version__ == version("mcp-google-workspace")


def test_structural_admission_limits_are_enforced() -> None:
    _validate_payload_shape({"safe": ["value"]})
    try:
        _validate_payload_shape({"too_many": [None] * 10_001})
    except ValueError as exc:
        assert "10,000" in str(exc)
    else:  # pragma: no cover - policy invariant
        raise AssertionError("Oversized input was accepted")


def test_principal_admission_state_is_bounded_and_evicts_idle_entries(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_PRINCIPAL_STATE_LIMIT", "100")
    middleware = ProductionControlMiddleware()
    for index in range(110):
        state = middleware._admission_state(f"principal-{index}")
        state.last_seen = 0
    middleware._admission_state("active-principal")
    assert len(middleware._principal_states) <= 100
    assert "active-principal" in middleware._principal_states


def test_recoverable_errors_always_include_next_action() -> None:
    _, envelope = _error_envelope(ValueError("invalid argument"))
    assert envelope["code"] == "invalid_input"
    assert envelope["required_action"] == {
        "action": "correct_arguments",
        "field_errors": [],
    }


def test_internal_errors_are_generic_to_clients_and_logged(caplog) -> None:
    _, envelope = _error_envelope(RuntimeError("private provider detail"))
    assert envelope["code"] == "internal_error"
    assert envelope["message"] == (
        "The Workspace tool failed unexpectedly. Check server logs for details."
    )
    assert "RuntimeError" not in envelope["message"]
    assert "private provider detail" not in envelope["message"]

    async def exercise() -> None:
        middleware = StructuredToolErrorMiddleware()
        context = MiddlewareContext(
            message=mt.CallToolRequestParams(
                name="gmail_read_emails",
                arguments={},
            ),
            method="tools/call",
        )

        async def call_next(_context):
            raise RuntimeError("private provider detail")

        with pytest.raises(McpError):
            await middleware.on_call_tool(context, call_next)

    with caplog.at_level(
        logging.ERROR,
        logger="mcp_google_workspace.errors",
    ):
        anyio.run(exercise)

    assert "RuntimeError" in caplog.text
    assert "private provider detail" in caplog.text


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


def test_multi_worker_readiness_requires_distributed_oauth_state(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MCP_USER_TOKEN_DIR", str(tmp_path / "tokens"))
    monkeypatch.setenv("MCP_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("MCP_WORKERS", "2")
    monkeypatch.setenv("MCP_REDIS_URL", "redis://example")
    monkeypatch.setenv("MCP_UPLOAD_S3_BUCKET", "uploads")
    monkeypatch.setenv("MCP_REQUEST_STATE_KEYS", "k" * 64)
    monkeypatch.setenv("FASTMCP_TASKS_ENCRYPTION_KEY", "t" * 40)
    # W5: modern traffic needs no affinity, so MCP_SESSION_AFFINITY is no
    # longer required for readiness (it is reported for legacy sessions only).
    monkeypatch.delenv("MCP_SESSION_AFFINITY", raising=False)
    monkeypatch.delenv("MCP_RUNTIME_MODE", raising=False)  # set by bundle tests earlier in the run

    class Backend:
        backend_name = "redis"

        def ping(self):
            return True

    class RedisClient:
        def ping(self):
            return True

    class S3Client:
        def head_bucket(self, **kwargs):
            return {}

    monkeypatch.setattr(
        "mcp_google_workspace.auth.google_auth.get_token_store", lambda: Backend()
    )
    monkeypatch.setattr(
        "mcp_google_workspace.common.production.redis.Redis.from_url",
        lambda _url: RedisClient(),
    )
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: S3Client())

    ready, payload = readiness_report()

    assert ready, json.dumps(payload["checks"])
    assert payload["checks"]["token_storage"]["backend"] == "redis"
    assert payload["checks"]["fleet_storage"]["ok"]
    assert payload["checks"]["continuation_keys"]["ok"]
    assert payload["checks"]["app_state"] == {"backend": "redis", "ok": True}
    assert payload["checks"]["task_queue"]["ok"] and payload["checks"]["task_queue"]["backend"] == "redis"
    assert payload["checks"]["legacy_session_affinity"]["required"] is False
    assert payload["warnings"] == ["legacy_session_affinity"]

    # Without a shared continuation key ring a replica fleet is not ready:
    # a confirmation asked on one replica could not be answered on another.
    monkeypatch.delenv("MCP_REQUEST_STATE_KEYS")
    ready, payload = readiness_report()
    assert not ready
    assert payload["checks"]["continuation_keys"]["ok"] is False


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

    from mcp_google_workspace.auth.grants import GrantSnapshot

    monkeypatch.setattr(
        "mcp_google_workspace.common.production.get_access_token", lambda: object()
    )
    # W5: the catalog reads the caller's current grant (auth.grants) per request.
    monkeypatch.setattr(
        "mcp_google_workspace.auth.grants.read_grant",
        lambda principal=None: GrantSnapshot("p", "rev-1", frozenset({"gmail"})),
    )
    monkeypatch.setattr(
        "mcp_google_workspace.auth.grants.current_principal",
        lambda: SimpleNamespace(storage_key="p"),
    )
    names, parameters = anyio.run(exercise)
    assert "gmail_send_email" in names
    assert "drive_list_files" not in names
    assert "gmail_download_attachment" not in names
    attachment_items = parameters["properties"]["attachments"]["anyOf"][0]["items"]
    assert "file_path" not in attachment_items["properties"]


def _run_error_middleware(error: Exception):
    async def exercise():
        middleware = StructuredToolErrorMiddleware()
        context = MiddlewareContext(
            message=mt.CallToolRequestParams(name="gmail_read_emails", arguments={}),
            method="tools/call",
        )

        async def call_next(_context):
            raise error

        return await middleware.on_call_tool(context, call_next)

    return anyio.run(exercise)


def test_rate_limit_uses_implementation_defined_code_and_structured_data() -> None:
    # W2: the FastMCP 3 code -32029 sat in the range MCP 2026-07-28 reserves for
    # spec-defined codes; -32005 is in the implementation-defined band and does
    # not collide with the SDK's -32020/-32021/-32022.
    assert -32019 <= RPC_RATE_LIMITED <= -32000
    assert RPC_RATE_LIMITED not in {-32000, -32001, -32020, -32021, -32022}
    # W5: an *admission* rate limit is a protocol rejection (AdmissionError);
    # a Google 429 is a tool execution error instead.
    error = AdmissionError("rate_limited", "Per-principal request rate exceeded.", retry_after=3)
    with pytest.raises(McpError) as raised:
        _run_error_middleware(error)
    assert raised.value.code == RPC_RATE_LIMITED
    envelope = raised.value.data
    assert envelope["code"] == "rate_limited"
    assert envelope["retryable"] is True
    assert envelope["required_action"] == {"action": "retry", "after_seconds": 3}
    # Human-readable message, not a JSON document; still names code and next step.
    assert not raised.value.message.startswith("{")
    assert "[code: rate_limited]" in raised.value.message


def test_framework_wrapped_errors_keep_their_recovery_envelope() -> None:
    from fastmcp.exceptions import ToolError

    cause = RecoverableToolError(
        "picker_required",
        "Use the Workspace Files picker.",
        required_action={"tool": "files_file_manager", "arguments": {}},
    )
    try:
        raise ToolError("Error calling tool 'upload_file': Use the picker") from cause
    except ToolError as wrapped:
        error = wrapped
    # W5: a recoverable tool failure is an isError result, not a JSON-RPC error.
    result = _run_error_middleware(error)
    assert isinstance(result, ToolResult) and result.is_error is True
    assert result.structured_content["code"] == "picker_required"
    assert result.structured_content["required_action"] == {"tool": "files_file_manager", "arguments": {}}


def test_confirmation_required_is_an_error_tool_result_not_a_protocol_error() -> None:
    result = _run_error_middleware(ConfirmationRequiredError("delete_task", "Delete task t1?"))
    assert isinstance(result, ToolResult)
    assert result.is_error is True
    assert result.structured_content["code"] == "confirmation_required"
    assert result.structured_content["required_action"]["prompt"] == "Delete task t1?"
