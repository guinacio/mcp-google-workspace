"""Machine-readable MCP tool error envelopes."""

from __future__ import annotations

import json
from typing import Any

import mcp.types as mt
from fastmcp.exceptions import McpError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import ErrorData


class RecoverableToolError(RuntimeError):
    """Tool failure with an explicit model-executable recovery step."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        required_action: dict[str, Any],
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = code
        self.required_action = required_action
        self.retryable = retryable
        self.retry_after = retry_after


def _error_envelope(error: Exception) -> tuple[int, dict[str, Any]]:
    provider_status = getattr(getattr(error, "resp", None), "status", None)
    message = str(error)
    lowered = message.lower()
    error_type = error.__class__.__name__
    explicit_code = getattr(error, "error_code", None)
    if isinstance(explicit_code, str):
        code = explicit_code
        rpc_code = -32029 if code == "rate_limited" else -32000
        retryable = bool(getattr(error, "retryable", True))
    elif error_type == "GoogleAccountConnectionRequired" and "scope" in lowered:
        code, rpc_code, retryable = "missing_capability", -32001, False
    elif error_type in {"GoogleAccountConnectionRequired", "GoogleAccountReauthenticationRequired"}:
        code, rpc_code, retryable = "reauth_required", -32001, False
    elif "page token" in lowered or "pagetoken" in lowered:
        code, rpc_code, retryable = "invalid_page_token", -32602, False
    elif "confirmation" in lowered or "elicitation" in lowered:
        code, rpc_code, retryable = "confirmation_required", -32010, False
    elif isinstance(error, FileNotFoundError):
        code, rpc_code, retryable = "not_found", -32004, False
    elif isinstance(error, PermissionError):
        code, rpc_code, retryable = "permission_denied", -32003, False
    elif isinstance(error, (ValueError, TypeError)):
        code, rpc_code, retryable = "invalid_input", -32602, False
    elif provider_status == 429 or "rate limit" in lowered:
        code, rpc_code, retryable = "rate_limited", -32029, True
    elif provider_status in {500, 502, 503, 504}:
        code, rpc_code, retryable = "provider_unavailable", -32002, True
    elif isinstance(error, TimeoutError):
        code, rpc_code, retryable = "timeout", -32000, True
    elif "reauth_required" in lowered or "oauth" in lowered:
        code, rpc_code, retryable = "reauth_required", -32001, False
    else:
        code, rpc_code, retryable = "internal_error", -32603, False
        message = "The Workspace tool failed unexpectedly. Check server logs for details."
    action: dict[str, Any] | None = getattr(error, "required_action", None)
    if code == "reauth_required":
        action = action or {"tool": "connect_google_workspace", "arguments": {}}
    elif code == "missing_capability":
        capability = next(
            (name for name in ("gmail", "calendar", "drive", "sheets", "docs", "tasks", "people", "forms", "slides", "keep", "chat", "meet") if name in lowered),
            None,
        )
        action = action or {
            "tool": "connect_google_workspace",
            "arguments": {"capabilities": [capability]} if capability else {},
        }
    elif code == "invalid_page_token":
        action = action or {"action": "retry_without_page_token"}
    elif code == "confirmation_required":
        action = action or {"action": "request_host_confirmation"}
    elif retryable:
        action = action or {
            "action": "retry",
            "after_seconds": getattr(error, "retry_after", None) or 1,
        }
    elif code == "invalid_input":
        action = action or {"action": "correct_arguments", "field_errors": []}
    elif code == "permission_denied":
        action = action or {"action": "request_access_or_choose_another_resource"}
    elif code == "not_found":
        action = action or {"action": "verify_resource_id_or_search_again"}
    envelope: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
        "retry_after": getattr(error, "retry_after", None)
        or getattr(getattr(error, "resp", None), "retry_after", None),
        "required_action": action,
        "provider_status": provider_status,
        "field_errors": [],
    }
    return rpc_code, envelope


class StructuredToolErrorMiddleware(Middleware):
    """Convert every uncaught tool exception to one stable JSON envelope."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        try:
            return await call_next(context)
        except McpError:
            raise
        except Exception as error:
            rpc_code, envelope = _error_envelope(error)
            raise McpError(
                ErrorData(
                    code=rpc_code,
                    message=json.dumps(envelope, separators=(",", ":")),
                )
            ) from error
