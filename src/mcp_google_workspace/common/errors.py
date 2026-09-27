"""Machine-readable MCP error envelopes and protocol-vs-tool classification.

MCP 2026-07-28 (server/tools, "Error Handling") separates two mechanisms:

* **Protocol errors** — JSON-RPC ``error`` responses for problems with the
  request itself or the server's ability to service it: an unknown tool, a
  malformed request, a request the server refuses to admit (rate limit,
  draining), a caller that is not (or no longer) authorized, and unexpected
  server faults.
* **Tool execution errors** — a normal ``tools/call`` result with
  ``isError: true`` for everything that happened while (or instead of)
  running the tool that the model can act on: Google API failures, input
  validation failures, business-rule rejections, missing Google consent,
  confirmation outcomes and deadlines.

Both carry the same stable envelope (``code``, ``message``, ``retryable``,
``retry_after``, ``required_action``, ``provider_status``, ``field_errors`` and
optional ``details``): as ``structuredContent`` of the error result, or as the
JSON-RPC ``error.data``.

Tool bodies convert their own exceptions through :func:`tool_error_result`
(installed around every tool by ``common.execution``), which therefore also
covers mounted subservers, nested calls and background-task workers.
:class:`StructuredToolErrorMiddleware` classifies what escapes outside a tool
body (argument validation, unknown tools, admission and authorization gates).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any, Final

import mcp.types as mt
from fastmcp.exceptions import FastMCPError, McpError, NotFoundError, ToolError
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from pydantic import ValidationError as PydanticValidationError

LOGGER = logging.getLogger("mcp_google_workspace.errors")

# ---------------------------------------------------------------------------
# JSON-RPC error codes
# ---------------------------------------------------------------------------
#
# MCP 2026-07-28 leaves -32000..-32019 to implementations and reserves
# -32020..-32099 for the specification (the SDK emits HeaderMismatch -32020,
# MissingRequiredClientCapability -32021, UnsupportedProtocolVersion -32022).
# -32002 and -32042 are reserved-never-reused. Inside the implementation band
# the installed SDK (mcp 2.2.0, ``mcp_types.jsonrpc``) allocates -32000
# (CONNECTION_CLOSED) and -32001 (REQUEST_TIMEOUT), and FastMCP 4.0.10 emits
# -32000/-32001/-32002 from its optional middleware and tool timeouts. The
# application therefore uses -32005..-32008 only, and only for protocol-level
# rejections: every tool execution failure is an ``isError`` result instead.

JSONRPC_INVALID_PARAMS: Final[int] = -32602
"""Standard JSON-RPC: unknown tool (MCP 2026-07-28 tools/call error handling)."""

JSONRPC_INTERNAL_ERROR: Final[int] = -32603
"""Standard JSON-RPC: unexpected server fault; details stay in server logs."""

RPC_RATE_LIMITED: Final[int] = -32005
"""Admission refused: the caller exceeded its request rate (was -32029 in FastMCP 3)."""

RPC_SERVER_UNAVAILABLE: Final[int] = -32006
"""Admission refused: the server is draining or out of capacity; retry elsewhere/later."""

RPC_UNAUTHORIZED: Final[int] = -32007
"""The caller is not authorized to run anything: principal revoked, or a
background task whose submitting caller can no longer be restored."""

RPC_AUTHORIZATION_UNAVAILABLE: Final[int] = -32008
"""Authorization state (principal revocation) could not be verified; fail closed."""

APPLICATION_RPC_CODES: Final[frozenset[int]] = frozenset(
    {RPC_RATE_LIMITED, RPC_SERVER_UNAVAILABLE, RPC_UNAUTHORIZED, RPC_AUTHORIZATION_UNAVAILABLE}
)
"""Every implementation-defined JSON-RPC code this application emits."""

#: Envelope ``code`` -> JSON-RPC code for codes that are always protocol-level,
#: even when raised as a plain :class:`RecoverableToolError`. ``rate_limited``
#: is deliberately absent: an *admission* rate limit is a
#: :class:`ProtocolRejection` (-32005), a *Google* 429 is a tool execution error.
PROTOCOL_ERROR_CODES: Final[dict[str, int]] = {
    "server_draining": RPC_SERVER_UNAVAILABLE,
    "server_overloaded": RPC_SERVER_UNAVAILABLE,
    "principal_revoked": RPC_UNAUTHORIZED,
    "task_caller_unavailable": RPC_UNAUTHORIZED,
    "authorization_backend_unavailable": RPC_AUTHORIZATION_UNAVAILABLE,
}

_CAPABILITY_NAMES: Final[tuple[str, ...]] = (
    "gmail", "calendar", "drive", "sheets", "docs", "tasks", "people", "forms",
    "slides", "keep", "chat", "meet",
)
_MAX_FIELD_ERRORS: Final[int] = 20


class RecoverableToolError(RuntimeError):
    """Tool failure with an explicit model-executable recovery step."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        required_action: dict[str, Any] | None,
        retryable: bool = False,
        retry_after: float | None = None,
        details: dict[str, Any] | None = None,
        provider_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = code
        self.required_action = required_action
        self.retryable = retryable
        self.retry_after = retry_after
        self.details = details
        self.provider_status = provider_status


class ProtocolRejection(RecoverableToolError):
    """A request the server refuses before (or instead of) running the tool.

    Always surfaces as a JSON-RPC error with :attr:`rpc_code`, never as an
    ``isError`` result: the call was not serviced at all.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        rpc_code: int,
        required_action: dict[str, Any] | None,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(
            code,
            message,
            required_action=required_action,
            retryable=retryable,
            retry_after=retry_after,
        )
        self.rpc_code = rpc_code


class ProviderToolError(RecoverableToolError):
    """A failed Google API call, as a tool execution error.

    Carries the provider HTTP status and the identifying arguments the tool
    passed in (never message bodies or tokens) under ``details.context``.
    """


def _provider_reason(exc: Exception) -> str:
    reason = getattr(exc, "reason", None)
    if isinstance(reason, str) and reason.strip():
        return reason.strip()[:500]
    return str(exc)[:500] or exc.__class__.__name__


def _provider_retry_after(exc: Exception) -> float | None:
    response = getattr(exc, "resp", None)
    getter = getattr(response, "get", None)
    if getter is None:
        return None
    raw = getter("retry-after")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def provider_tool_error(exc: Exception, **context: Any) -> ProviderToolError:
    """Classify a Google API failure as a tool execution error.

    Use as ``raise provider_tool_error(exc, document_id=document_id) from exc``
    in place of returning an ``{"error": ...}`` dictionary as a successful
    result. ``context`` identifies the request (IDs, names); it must never
    contain message bodies, recipients, or credentials.
    """
    status_raw = getattr(getattr(exc, "resp", None), "status", None)
    try:
        status = int(status_raw) if status_raw is not None else None
    except (TypeError, ValueError):
        status = None
    reason = _provider_reason(exc)
    lowered = reason.lower()
    retry_after = _provider_retry_after(exc)
    action: dict[str, Any] | None
    retryable = False
    if status == 429 or (status == 403 and "rate limit" in lowered):
        code, retryable = "rate_limited", True
        action = {"action": "retry", "after_seconds": retry_after or 5}
    elif status is not None and status >= 500:
        code, retryable = "provider_unavailable", True
        action = {"action": "retry", "after_seconds": retry_after or 5}
    elif status == 401:
        code = "reauth_required"
        action = {"tool": "connect_google_workspace", "arguments": {}}
    elif status == 403:
        code = "permission_denied"
        action = {"action": "request_access_or_choose_another_resource"}
    elif status in {404, 410}:
        code = "not_found"
        action = {"action": "verify_resource_id_or_search_again"}
    elif status in {409, 412}:
        code = "conflict"
        action = {"action": "refresh_resource_and_retry"}
    elif status == 400:
        code = "invalid_input"
        action = {"action": "correct_arguments", "field_errors": []}
    else:
        code = "provider_error"
        action = {"action": "check_arguments_or_retry_later"}
    prefix = f"Google API request failed (HTTP {status})" if status else "Google API request failed"
    return ProviderToolError(
        code,
        f"{prefix}: {reason}",
        required_action=action,
        retryable=retryable,
        retry_after=retry_after,
        details={"context": dict(context)} if context else None,
        provider_status=status,
    )


class ConfirmationError(RecoverableToolError):
    """Base for confirmation outcomes that are tool results, never protocol errors.

    Raised before any provider mutation and returned as an ``isError`` tool
    result carrying the structured envelope, so the model can explain what
    was not done.
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


def _field_errors(error: Exception) -> list[dict[str, str]]:
    """Bounded ``[{field, message}]`` list from a pydantic validation failure."""
    source: Exception | None = error
    while source is not None and not isinstance(source, PydanticValidationError):
        source = source.__cause__ if isinstance(source.__cause__, Exception) else None
    if not isinstance(source, PydanticValidationError):
        return []
    fields: list[dict[str, str]] = []
    for item in source.errors()[:_MAX_FIELD_ERRORS]:
        location = ".".join(str(part) for part in item.get("loc", ())) or "(arguments)"
        fields.append({"field": location, "message": str(item.get("msg", "invalid value"))[:300]})
    return fields


def _validation_message(fields: Iterable[dict[str, str]]) -> str:
    parts = [f"{entry['field']}: {entry['message']}" for entry in fields]
    if not parts:
        return "The tool arguments are invalid."
    return "The tool arguments are invalid: " + "; ".join(parts)


def classify_error(error: Exception) -> tuple[int | None, dict[str, Any]]:
    """Return ``(json_rpc_code, envelope)`` for *error*.

    ``json_rpc_code`` is ``None`` for a tool execution error (an ``isError``
    result) and the JSON-RPC code for a protocol-level error.
    """
    provider_status = getattr(error, "provider_status", None)
    if provider_status is None:
        provider_status = getattr(getattr(error, "resp", None), "status", None)
    message = str(error)
    lowered = message.lower()
    error_type = error.__class__.__name__
    explicit_code = getattr(error, "error_code", None)
    field_errors: list[dict[str, str]] = []
    rpc_code: int | None = None
    if isinstance(error, ProtocolRejection):
        code, retryable, rpc_code = error.error_code, error.retryable, error.rpc_code
    elif isinstance(explicit_code, str):
        code = explicit_code
        retryable = bool(getattr(error, "retryable", False))
        rpc_code = PROTOCOL_ERROR_CODES.get(code)
    elif isinstance(error, FastMCPValidationError):
        code, retryable = "invalid_input", False
        field_errors = _field_errors(error)
        message = _validation_message(field_errors) if field_errors else message
    elif error_type == "GoogleAccountConnectionRequired" and "scope" in lowered:
        code, retryable = "missing_capability", False
    elif error_type in {"GoogleAccountConnectionRequired", "GoogleAccountReauthenticationRequired"}:
        code, retryable = "reauth_required", False
    elif "page token" in lowered or "pagetoken" in lowered:
        code, retryable = "invalid_page_token", False
    elif "confirmation" in lowered or "elicitation" in lowered:
        code, retryable = "confirmation_required", False
    elif isinstance(error, FileNotFoundError):
        code, retryable = "not_found", False
    elif isinstance(error, PermissionError):
        code, retryable = "permission_denied", False
    elif isinstance(error, (ValueError, TypeError)):
        code, retryable = "invalid_input", False
        field_errors = _field_errors(error)
    elif provider_status == 429 or "rate limit" in lowered:
        code, retryable = "rate_limited", True
    elif provider_status in {500, 502, 503, 504}:
        code, retryable = "provider_unavailable", True
    elif isinstance(error, TimeoutError):
        code, retryable = "timeout", True
    elif "reauth_required" in lowered or "oauth" in lowered:
        code, retryable = "reauth_required", False
    else:
        code, retryable = "internal_error", False
        rpc_code = JSONRPC_INTERNAL_ERROR
        message = "The Workspace tool failed unexpectedly. Check server logs for details."
    action: dict[str, Any] | None = getattr(error, "required_action", None)
    if code == "reauth_required":
        action = action or {"tool": "connect_google_workspace", "arguments": {}}
    elif code == "missing_capability":
        capability = next((name for name in _CAPABILITY_NAMES if name in lowered), None)
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
        action = action or {"action": "correct_arguments", "field_errors": field_errors}
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
        "field_errors": field_errors,
    }
    details = getattr(error, "details", None)
    if isinstance(details, dict) and details:
        envelope["details"] = details
    return rpc_code, envelope


def _error_envelope(error: Exception) -> tuple[int | None, dict[str, Any]]:
    """Backward-compatible alias of :func:`classify_error`."""
    return classify_error(error)


def render_error_message(envelope: dict[str, Any]) -> str:
    """Human-readable error text carrying the stable code and next step."""
    text = f"{envelope['message']} [code: {envelope['code']}]"
    action = envelope.get("required_action")
    if action:
        text += f" Next step: {json.dumps(action, separators=(',', ':'), sort_keys=True)}"
    return text


def error_tool_result(envelope: dict[str, Any]) -> ToolResult:
    """An ``isError`` tool result carrying *envelope* as structured content."""
    return ToolResult(
        content=[mt.TextContent(type="text", text=render_error_message(envelope))],
        structured_content=envelope,
        is_error=True,
    )


def protocol_error(rpc_code: int, envelope: dict[str, Any]) -> McpError:
    """A JSON-RPC error whose ``data`` is the complete envelope."""
    return McpError(code=rpc_code, message=render_error_message(envelope), data=envelope)


def tool_error_result(error: Exception) -> ToolResult | None:
    """Tool execution error result for *error*, or ``None`` for a protocol error.

    Used by the execution guard around every tool body. Unexpected faults
    (``internal_error``) and protocol rejections return ``None`` so the caller
    re-raises them as JSON-RPC errors.
    """
    rpc_code, envelope = classify_error(error)
    if rpc_code is not None:
        return None
    return error_tool_result(envelope)


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
    """Classify every exception that escapes a ``tools/call`` into one envelope.

    * An unknown tool name on the wire becomes the JSON-RPC ``-32602`` the
      specification prescribes (a proxied unknown name, e.g. through the
      ``call_tool`` search proxy, is the model's argument and stays a tool
      execution error).
    * Protocol rejections (admission, authorization) and unexpected faults
      become JSON-RPC errors with the envelope as ``error.data``.
    * Everything else, including argument validation, becomes an ``isError``
      result the model can act on.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        try:
            return await call_next(context)
        except McpError:
            raise
        except NotFoundError as raised:
            envelope = {
                "code": "unknown_tool",
                "message": f"Unknown tool: {context.message.name!r}",
                "retryable": False,
                "retry_after": None,
                "required_action": {"action": "list_tools_and_choose_an_advertised_tool"},
                "provider_status": None,
                "field_errors": [],
            }
            raise protocol_error(JSONRPC_INVALID_PARAMS, envelope) from raised
        except Exception as raised:
            error = unwrap_tool_error(raised)
            rpc_code, envelope = classify_error(error)
            if rpc_code is None:
                return error_tool_result(envelope)
            if envelope.get("code") == "internal_error":
                LOGGER.exception(
                    "Unhandled tool exception (%s): %s",
                    type(error).__name__,
                    error,
                )
            raise protocol_error(rpc_code, envelope) from raised


__all__ = [
    "APPLICATION_RPC_CODES",
    "ConfirmationError",
    "ConfirmationRejectedError",
    "ConfirmationRequiredError",
    "JSONRPC_INTERNAL_ERROR",
    "JSONRPC_INVALID_PARAMS",
    "PROTOCOL_ERROR_CODES",
    "ProtocolRejection",
    "ProviderToolError",
    "RPC_AUTHORIZATION_UNAVAILABLE",
    "RPC_RATE_LIMITED",
    "RPC_SERVER_UNAVAILABLE",
    "RPC_UNAUTHORIZED",
    "RecoverableToolError",
    "StructuredToolErrorMiddleware",
    "classify_error",
    "error_tool_result",
    "protocol_error",
    "provider_tool_error",
    "render_error_message",
    "tool_error_result",
    "unwrap_tool_error",
]
