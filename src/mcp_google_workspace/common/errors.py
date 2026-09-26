"""Machine-readable MCP tool error envelopes."""

from __future__ import annotations

import json
import logging
from typing import Any, Final

import mcp.types as mt
from fastmcp.exceptions import FastMCPError, McpError, ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult

LOGGER = logging.getLogger("mcp_google_workspace.errors")

# JSON-RPC codes for uncaught tool failures. MCP 2026-07-28 reserves
# -32020..-32099 for specification-defined codes (the SDK already emits
# HeaderMismatch -32020, MissingRequiredClientCapability -32021 and
# UnsupportedProtocolVersion -32022), leaving -32000..-32019 to implementations.
# The retired FastMCP 3 rate-limit code -32029 sat inside the reserved band.
RPC_RATE_LIMITED: Final[int] = -32005
"""Application code for a provider or admission rate limit (was -32029)."""


def tool_error_payload(exc: Exception, **context: Any) -> dict[str, Any]:
    """Structured in-tool error return for a failed Google API call.

    Mirrors the calendar namespace's ``{"error": ...}`` convention so LLM
    callers receive a readable failure payload (plus the identifying context
    they passed in) instead of a raw traceback or an opaque protocol error.
    Context kwargs are nested under ``context`` so the payload always matches
    the closed error-envelope shape declared in the tool output schemas.
    """
    payload: dict[str, Any] = {"error": str(exc)}
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is not None:
        payload["provider_status"] = int(status)
    if context:
        payload["context"] = dict(context)
    return payload


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


class ConfirmationError(RecoverableToolError):
    """Base for confirmation outcomes that are tool results, never protocol errors.

    Raised before any provider mutation. ``StructuredToolErrorMiddleware``
    returns it as an ``isError`` tool result carrying the structured envelope,
    so the model can explain what was not done.
    """

    action_name: str


class ConfirmationRequiredError(ConfirmationError):
    """An action needs user confirmation that this request cannot collect.

    Used when the client declared no elicitation capability, the protocol
    version is unknown, or there is no live request (fail closed: unavailable
    confirmation is never consent). The envelope carries the exact prompt.
    """

    def __init__(self, action_name: str, prompt: str) -> None:
        super().__init__(
            "confirmation_required",
            (
                f"{action_name} requires explicit user confirmation, which this request "
                "cannot collect (the client did not declare elicitation support). "
                "No changes were made."
            ),
            required_action={
                "action": "request_host_confirmation",
                "operation": action_name,
                "prompt": prompt,
            },
        )
        self.action_name = action_name
        self.prompt = prompt


class ConfirmationRejectedError(ConfirmationError):
    """A multi-round-trip confirmation answer or continuation failed verification.

    Covers tampered, expired, foreign-principal, changed-argument, replayed and
    malformed continuations and wrong or missing answers. Nothing is executed;
    the client must start a fresh confirmation by calling the tool again
    without ``requestState``/``inputResponses``.
    """

    def __init__(self, action_name: str, reason: str) -> None:
        super().__init__(
            "confirmation_invalid",
            (
                f"The confirmation for {action_name} could not be verified ({reason}). "
                "No changes were made."
            ),
            required_action={
                "action": "restart_confirmation",
                "operation": action_name,
                "reason": reason,
            },
        )
        self.action_name = action_name
        self.reason = reason


def _error_envelope(error: Exception) -> tuple[int, dict[str, Any]]:
    provider_status = getattr(getattr(error, "resp", None), "status", None)
    message = str(error)
    lowered = message.lower()
    error_type = error.__class__.__name__
    explicit_code = getattr(error, "error_code", None)
    if isinstance(explicit_code, str):
        code = explicit_code
        rpc_code = RPC_RATE_LIMITED if code == "rate_limited" else -32000
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
        code, rpc_code, retryable = "rate_limited", RPC_RATE_LIMITED, True
    elif provider_status in {500, 502, 503, 504}:
        code, rpc_code, retryable = "provider_unavailable", -32002, True
    elif isinstance(error, TimeoutError):
        code, rpc_code, retryable = "timeout", -32000, True
    elif "reauth_required" in lowered or "oauth" in lowered:
        code, rpc_code, retryable = "reauth_required", -32001, False
    else:
        code, rpc_code, retryable = "internal_error", -32603, False
        message = (
            "The Workspace tool failed unexpectedly. Check server logs for details."
        )
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


def render_error_message(envelope: dict[str, Any]) -> str:
    """Human-readable error text carrying the stable code and next step."""
    text = f"{envelope['message']} [code: {envelope['code']}]"
    action = envelope.get("required_action")
    if action:
        text += f" Next step: {json.dumps(action, separators=(',', ':'), sort_keys=True)}"
    return text


def unwrap_tool_error(error: Exception) -> Exception:
    """Return the application exception behind FastMCP's tool-call wrapper.

    FastMCP 4 re-raises a failing tool body as ``ToolError("Error calling tool
    ...") from exc``. This package never raises ``ToolError`` itself, so the
    wrapped cause is what carries the machine-readable ``error_code`` and
    ``required_action``; classify that rather than the generic wrapper.
    """
    current = error
    while (
        isinstance(current, ToolError)
        and isinstance(current.__cause__, Exception)
        and not isinstance(current.__cause__, McpError)
    ):
        cause = current.__cause__
        if isinstance(cause, FastMCPError) and not isinstance(cause, ToolError):
            break
        current = cause
    return current


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
        except Exception as raised:
            error = unwrap_tool_error(raised)
            rpc_code, envelope = _error_envelope(error)
            if isinstance(error, ConfirmationError):
                # A missing or rejected confirmation is a tool outcome, not a
                # malformed request: return an isError result the model can
                # read and explain. (W5 extends this classification.)
                return ToolResult(
                    content=[mt.TextContent(type="text", text=render_error_message(envelope))],
                    structured_content=envelope,
                    is_error=True,
                )
            if envelope.get("code") == "internal_error":
                LOGGER.exception(
                    "Unhandled tool exception (%s): %s",
                    type(error).__name__,
                    error,
                )
            # The complete envelope travels as structured JSON-RPC error data;
            # the message stays human-readable but still names the stable code
            # and the next step for clients that surface only the message.
            raise McpError(
                code=rpc_code,
                message=render_error_message(envelope),
                data=envelope,
            ) from raised
