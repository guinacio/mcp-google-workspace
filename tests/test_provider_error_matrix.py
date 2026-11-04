"""Error matrix: a raw Google HTTP failure in any namespace is a useful tool error.

Issue #51: Gmail, Drive and Calendar tools let ``HttpError`` escape
unclassified, so a 403 (missing scope) and a 404 both reached the client as
JSON-RPC ``-32603`` / ``internal_error`` with no status and no next step. Every
cell below is called end to end through the in-memory client against the
production composition, with the Google client stubbed to fail.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import McpError, ToolError
from googleapiclient.errors import HttpError
from httplib2 import Response

import mcp_google_workspace.auth.google_auth as google_auth
from mcp_google_workspace.common.errors import (
    RPC_SERVER_UNAVAILABLE,
    StructuredToolErrorMiddleware,
    classify_error,
    protocol_error,
    provider_capability,
    provider_error_reason,
    provider_tool_error,
    unwrap_tool_error,
)
from mcp_google_workspace.server import workspace_mcp

# One read-only tool per namespace plus the URL the real client would report.
NAMESPACES: dict[str, tuple[str, dict[str, Any], str]] = {
    "gmail": ("gmail_list_labels", {}, "https://gmail.googleapis.com/gmail/v1/users/me/labels?alt=json"),
    "drive": ("drive_get_file", {"file_id": "file-1"}, "https://www.googleapis.com/drive/v3/files/file-1?fields=id%2Cname&alt=json"),
    "calendar": ("calendar_list_calendars", {}, "https://www.googleapis.com/calendar/v3/users/me/calendarList?alt=json"),
}


def _google_error_body(status: int, message: str, reason: str | None, *, rpc_status: str = "PERMISSION_DENIED") -> bytes:
    error: dict[str, Any] = {"code": status, "message": message, "status": rpc_status}
    if reason is not None:
        error["errors"] = [{"message": message, "domain": "global", "reason": reason}]
    return json.dumps({"error": error}).encode()


# Realistic Gmail 403 for a token without the needed scope: ``errors[]`` (legacy
# reason) and ``details[]`` (google.rpc.ErrorInfo) are both present.
INSUFFICIENT_SCOPE_BODY = json.dumps(
    {
        "error": {
            "code": 403,
            "message": "Request had insufficient authentication scopes.",
            "errors": [{"message": "Insufficient Permission", "domain": "global", "reason": "insufficientPermissions"}],
            "status": "PERMISSION_DENIED",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
                    "domain": "googleapis.com",
                    "metadata": {"service": "gmail.googleapis.com", "method": "google.gmail.v1.GmailApi.ListLabels"},
                }
            ],
        }
    }
).encode()

# (label, status, body, expected code, retryable, expected required_action subset)
MATRIX: list[tuple[str, int, bytes, str, bool, dict[str, Any]]] = [
    ("400", 400, _google_error_body(400, "Invalid value for pageSize", "invalid", rpc_status="INVALID_ARGUMENT"), "invalid_input", False, {"action": "correct_arguments"}),
    ("401", 401, _google_error_body(401, "Request had invalid authentication credentials.", "authError", rpc_status="UNAUTHENTICATED"), "reauth_required", False, {"tool": "connect_google_workspace"}),
    ("403-insufficientPermissions", 403, INSUFFICIENT_SCOPE_BODY, "missing_capability", False, {"tool": "connect_google_workspace"}),
    ("403-forbidden", 403, _google_error_body(403, "The caller does not have permission", "forbidden"), "permission_denied", False, {"action": "request_access_or_choose_another_resource"}),
    ("403-accessNotConfigured", 403, _google_error_body(403, "Gmail API has not been used in project 1234 before or it is disabled.", "accessNotConfigured"), "permission_denied", False, {"action": "enable_api_in_google_cloud_project"}),
    ("403-userRateLimitExceeded", 403, _google_error_body(403, "User-rate limit exceeded.", "userRateLimitExceeded", rpc_status="RESOURCE_EXHAUSTED"), "rate_limited", True, {"action": "retry"}),
    ("404", 404, _google_error_body(404, "Requested entity was not found.", "notFound", rpc_status="NOT_FOUND"), "not_found", False, {"action": "verify_resource_id_or_search_again"}),
    ("409", 409, _google_error_body(409, "The requested identifier already exists.", "duplicate", rpc_status="ALREADY_EXISTS"), "conflict", False, {"action": "refresh_resource_and_retry"}),
    ("412", 412, _google_error_body(412, "Precondition Failed", "conditionNotMet", rpc_status="FAILED_PRECONDITION"), "conflict", False, {"action": "refresh_resource_and_retry"}),
    ("429", 429, _google_error_body(429, "Too many requests", "rateLimitExceeded", rpc_status="RESOURCE_EXHAUSTED"), "rate_limited", True, {"action": "retry"}),
    ("500", 500, _google_error_body(500, "Backend Error", "backendError", rpc_status="INTERNAL"), "provider_unavailable", True, {"action": "retry"}),
    ("503", 503, _google_error_body(503, "The service is currently unavailable.", "backendError", rpc_status="UNAVAILABLE"), "provider_unavailable", True, {"action": "retry"}),
]


class _FailingService:
    def __init__(self, error: HttpError) -> None:
        self._error = error

    def __getattr__(self, name: str) -> "_FailingService":
        if name.startswith("__"):
            raise AttributeError(name)
        return self

    def __call__(self, *_args: Any, **_kwargs: Any) -> "_FailingService":
        return self

    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise self._error


async def _call(tool: str, arguments: dict[str, Any]) -> Any:
    async with Client(workspace_mcp) as client:
        return await client.call_tool(tool, arguments, raise_on_error=False)


@pytest.mark.parametrize("namespace", sorted(NAMESPACES))
@pytest.mark.parametrize(("label", "status", "body", "code", "retryable", "action"), MATRIX, ids=[cell[0] for cell in MATRIX])
def test_raw_google_failures_are_structured_tool_errors(
    namespace: str, label: str, status: int, body: bytes, code: str, retryable: bool, action: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool, arguments, uri = NAMESPACES[namespace]
    error = HttpError(Response({"status": str(status)}), body, uri=uri)
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _FailingService(error))

    result = anyio.run(_call, tool, arguments)

    assert result.is_error is True, result.content
    envelope = result.structured_content
    assert isinstance(envelope, dict)
    assert envelope["code"] == code, envelope
    assert envelope["retryable"] is retryable
    for key, value in action.items():
        assert envelope["required_action"][key] == value, envelope["required_action"]
    if status == 401:
        # execute_google_request turns a 401 into the reauth signal before
        # classification; the status itself is not carried.
        return
    assert envelope["provider_status"] == status
    if code == "missing_capability":
        assert envelope["required_action"]["arguments"] == {"capabilities": [namespace]}
        assert envelope["details"]["provider_reason"] == "ACCESS_TOKEN_SCOPE_INSUFFICIENT"
    assert f"HTTP {status}" in envelope["message"]
    assert f"[code: {code}]" in result.content[0].text


def test_403_and_404_are_distinguishable_and_never_internal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    tool, arguments, uri = NAMESPACES["gmail"]
    codes: dict[int, str] = {}
    for status in (403, 404):
        error = HttpError(Response({"status": str(status)}), _google_error_body(status, "x", None), uri=uri)
        monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, _error=error, **_k: _FailingService(_error))
        result = anyio.run(_call, tool, arguments)
        assert result.is_error is True
        codes[status] = result.structured_content["code"]
    assert codes == {403: "permission_denied", 404: "not_found"}


# ---------------------------------------------------------------------------
# Unit level: reason reading, capability inference, parity with provider_tool_error
# ---------------------------------------------------------------------------


def test_error_details_reason_is_read_from_both_google_shapes() -> None:
    legacy = HttpError(Response({"status": "403"}), _google_error_body(403, "Insufficient Permission", "insufficientPermissions"))
    assert provider_error_reason(legacy) == "insufficientPermissions"
    modern = HttpError(Response({"status": "403"}), INSUFFICIENT_SCOPE_BODY)
    assert provider_error_reason(modern) == "ACCESS_TOKEN_SCOPE_INSUFFICIENT"
    assert provider_error_reason(HttpError(Response({"status": "403"}), b"not json")) is None


@pytest.mark.parametrize(
    ("uri", "capability"),
    [
        ("https://gmail.googleapis.com/gmail/v1/users/me/messages?alt=json", "gmail"),
        ("https://www.googleapis.com/drive/v3/files?q=x&alt=json", "drive"),
        ("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&alt=json", "drive"),
        ("https://www.googleapis.com/calendar/v3/calendars/primary/events?alt=json", "calendar"),
        ("https://sheets.googleapis.com/v4/spreadsheets/s1?alt=json", "sheets"),
        ("https://people.googleapis.com/v1/people/me/connections?alt=json", "people"),
        ("https://tasks.googleapis.com/tasks/v1/users/@me/lists?alt=json", "tasks"),
        ("https://example.invalid/v1/thing", None),
        (None, None),
    ],
)
def test_capability_is_inferred_from_the_request_url(uri: str | None, capability: str | None) -> None:
    error = HttpError(Response({"status": "403"}), b"{}", uri=uri)
    assert provider_capability(error) == capability


def test_classify_error_and_provider_tool_error_agree_on_a_scope_403() -> None:
    error = HttpError(
        Response({"status": "403"}), INSUFFICIENT_SCOPE_BODY,
        uri="https://gmail.googleapis.com/gmail/v1/users/me/labels?alt=json",
    )
    raw_rpc, raw = classify_error(error)
    explicit_rpc, explicit = classify_error(provider_tool_error(error))
    assert raw_rpc is None and explicit_rpc is None
    for envelope in (raw, explicit):
        assert envelope["code"] == "missing_capability"
        assert envelope["required_action"] == {"tool": "connect_google_workspace", "arguments": {"capabilities": ["gmail"]}}
        assert envelope["provider_status"] == 403
        assert envelope["details"]["provider_reason"] == "ACCESS_TOKEN_SCOPE_INSUFFICIENT"
    assert raw["message"] == explicit["message"]


def test_full_drive_storage_is_not_a_retryable_rate_limit() -> None:
    """``storageQuotaExceeded`` names a quota, but waiting never frees space."""
    message = "The user's Drive storage quota has been exceeded."
    error = HttpError(
        Response({"status": "403"}),
        _google_error_body(403, message, "storageQuotaExceeded"),
        uri="https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&alt=json",
    )
    for envelope in (classify_error(error)[1], classify_error(provider_tool_error(error))[1]):
        assert envelope["code"] == "permission_denied"
        assert envelope["retryable"] is False
        assert envelope["required_action"]["action"] != "retry"


PAGED_URI = "https://www.googleapis.com/drive/v3/files?pageToken=opaque-token&q=name+contains+%27budget%27&alt=json"


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (403, INSUFFICIENT_SCOPE_BODY, "missing_capability"),
        (429, _google_error_body(429, "Too many requests", "rateLimitExceeded"), "rate_limited"),
        (404, _google_error_body(404, "File not found: f1.", "notFound"), "not_found"),
    ],
    ids=["403-scope", "429", "404"],
)
def test_a_paged_request_failure_is_not_read_as_a_bad_page_token(status: int, body: bytes, code: str) -> None:
    """``str(HttpError)`` carries the URL, and with it ``pageToken=``."""
    error = HttpError(Response({"status": str(status)}), body, uri=PAGED_URI)
    rpc_code, envelope = classify_error(error)
    assert rpc_code is None
    assert envelope["code"] == code
    assert "opaque-token" not in envelope["message"]
    assert "budget" not in envelope["message"]


def test_a_rejected_page_token_keeps_its_recovery_step() -> None:
    legacy = HttpError(
        Response({"status": "400"}),
        json.dumps(
            {"error": {"code": 400, "message": "Invalid Value", "errors": [
                {"message": "Invalid Value", "domain": "global", "reason": "invalid", "location": "pageToken", "locationType": "parameter"}
            ]}}
        ).encode(),
        uri=PAGED_URI,
    )
    worded = HttpError(Response({"status": "400"}), _google_error_body(400, "Invalid page token.", "invalid"), uri=PAGED_URI)
    for error in (legacy, worded):
        envelope = classify_error(error)[1]
        assert envelope["code"] == "invalid_page_token"
        assert envelope["required_action"] == {"action": "retry_without_page_token"}
        assert "opaque-token" not in envelope["message"]


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "invalid_input"), (401, "reauth_required"), (403, "permission_denied"), (404, "not_found"), (409, "conflict"), (412, "conflict"), (418, "provider_error")],
)
def test_classify_error_maps_raw_statuses_like_provider_tool_error(status: int, code: str) -> None:
    error = HttpError(Response({"status": str(status)}), _google_error_body(status, "message", None))
    rpc_code, envelope = classify_error(error)
    assert rpc_code is None
    assert envelope["code"] == code
    assert envelope["provider_status"] == status
    assert envelope["required_action"]


# ---------------------------------------------------------------------------
# No double classification: an already-classified protocol error is forwarded
# ---------------------------------------------------------------------------


def test_unwrap_tool_error_returns_the_wrapped_mcp_error() -> None:
    inner = protocol_error(RPC_SERVER_UNAVAILABLE, {"code": "server_draining", "message": "draining", "required_action": None})
    wrapper = ToolError("Error calling tool 'x': draining")
    wrapper.__cause__ = inner
    assert unwrap_tool_error(wrapper) is inner


def test_middleware_forwards_a_classified_protocol_error_unchanged() -> None:
    """The guard's JSON-RPC error survives FastMCP's ToolError wrapper intact."""
    envelope = {
        "code": "server_draining",
        "message": "The server is draining.",
        "retryable": True,
        "retry_after": 5,
        "required_action": {"action": "retry", "after_seconds": 5},
        "provider_status": None,
        "field_errors": [],
    }
    toy = FastMCP("error-toy")
    toy.add_middleware(StructuredToolErrorMiddleware())

    @toy.tool()
    def draining() -> str:
        raise protocol_error(RPC_SERVER_UNAVAILABLE, envelope)

    async def call() -> McpError:
        async with Client(toy) as client:
            with pytest.raises(McpError) as raised:
                await client.call_tool("draining", {})
        return raised.value

    error = anyio.run(call)
    assert error.error.code == RPC_SERVER_UNAVAILABLE
    assert error.error.data["code"] == "server_draining"
    assert error.error.data["required_action"] == {"action": "retry", "after_seconds": 5}
