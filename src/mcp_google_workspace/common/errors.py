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


def _provider_status(exc: Exception) -> int | None:
    status_raw = getattr(getattr(exc, "resp", None), "status", None)
    try:
        return int(status_raw) if status_raw is not None else None
    except (TypeError, ValueError):
        return None


def is_provider_http_error(exc: Exception) -> bool:
    """Whether *exc* is an HTTP error response from a Google API client.

    Duck-typed on the ``googleapiclient.errors.HttpError`` shape (``resp``
    with a status plus ``content``), so this module needs no provider import.
    """
    return _provider_status(exc) is not None and hasattr(exc, "content")


# Machine-readable reasons Google attaches to a 403 (``error.errors[].reason``
# or ``error.details[].reason``), grouped by what the caller can do about them.
_SCOPE_REASONS: Final[frozenset[str]] = frozenset(
    {"insufficientpermissions", "access_token_scope_insufficient", "insufficientscopes"}
)
_QUOTA_REASONS: Final[frozenset[str]] = frozenset(
    {
        "ratelimitexceeded", "userratelimitexceeded", "quotaexceeded",
        "dailylimitexceeded", "rate_limit_exceeded", "resource_exhausted",
    }
)
_API_DISABLED_REASONS: Final[frozenset[str]] = frozenset(
    {"accessnotconfigured", "service_disabled"}
)
# A full Drive is a quota that waiting does not restore: never ``rate_limited``,
# although its message ("storage quota has been exceeded") names a quota.
_EXHAUSTED_STORAGE_REASONS: Final[frozenset[str]] = frozenset({"storagequotaexceeded"})

# Google API host (or ``www.googleapis.com`` path prefix) -> capability name.
_HOST_CAPABILITIES: Final[dict[str, str]] = {
    "gmail.googleapis.com": "gmail",
    "sheets.googleapis.com": "sheets",
    "docs.googleapis.com": "docs",
    "tasks.googleapis.com": "tasks",
    "people.googleapis.com": "people",
    "forms.googleapis.com": "forms",
    "slides.googleapis.com": "slides",
    "keep.googleapis.com": "keep",
    "chat.googleapis.com": "chat",
    "meet.googleapis.com": "meet",
}
_PATH_CAPABILITIES: Final[dict[str, str]] = {
    "/calendar/": "calendar",
    "/drive/": "drive",
    # Media uploads (drive_upload_file, including content replacement).
    "/upload/drive/": "drive",
    "/gmail/": "gmail",
    "/tasks/": "tasks",
}


def provider_error_reason(exc: Exception) -> str | None:
    """The first machine-readable ``reason`` Google attached to *exc*, if any.

    ``googleapiclient`` stores ``error.errors`` / ``error.details`` in
    ``error_details``; each entry may carry ``reason`` (legacy shape) or
    ``metadata`` / ``@type`` (``google.rpc`` shape).
    """
    details = getattr(exc, "error_details", None)
    if isinstance(details, dict):
        details = [details]
    if not isinstance(details, list):
        return None
    for entry in details:
        if not isinstance(entry, dict):
            continue
        reason = entry.get("reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()[:100]
    return None


def _rejects_page_token(exc: Exception, reason: str) -> bool:
    """Whether Google rejected the page token itself, not merely a paged request.

    Reads the provider message and ``errors[].location`` only, never the
    request URL (which carries ``pageToken=`` on every paged call).
    """
    lowered = reason.lower()
    if "page token" in lowered or "pagetoken" in lowered:
        return True
    details = getattr(exc, "error_details", None)
    entries = details if isinstance(details, list) else [details]
    return any(
        isinstance(entry, dict) and str(entry.get("location", "")).lower() == "pagetoken"
        for entry in entries
    )


def provider_capability(exc: Exception) -> str | None:
    """The Workspace capability behind the request URL of *exc*, if recognisable."""
    uri = getattr(exc, "uri", None)
    if not isinstance(uri, str) or not uri:
        return None
    lowered = uri.lower()
    for host, capability in _HOST_CAPABILITIES.items():
        if f"//{host}/" in lowered:
            return capability
    if "//www.googleapis.com/" in lowered:
        for prefix, capability in _PATH_CAPABILITIES.items():
            if f"//www.googleapis.com{prefix}" in lowered:
                return capability
    return None


def classify_provider_status(
    status: int | None,
    *,
    reason: str,
    provider_reason: str | None,
    retry_after: float | None,
    capability: str | None,
) -> tuple[str, bool, dict[str, Any]]:
    """Map a Google HTTP failure to ``(code, retryable, required_action)``.

    Shared by :func:`provider_tool_error` (tools that classify explicitly)
    and :func:`classify_error` (raw errors escaping a tool body), so both
    paths agree on what a 403 with ``insufficientPermissions`` means.
    """
    lowered = reason.lower()
    reason_key = (provider_reason or "").lower()
    if status == 429 or (
        status == 403
        and reason_key not in _EXHAUSTED_STORAGE_REASONS
        and (reason_key in _QUOTA_REASONS or "rate limit" in lowered or "quota" in lowered)
    ):
        return "rate_limited", True, {"action": "retry", "after_seconds": retry_after or 5}
    if status is not None and status >= 500:
        return "provider_unavailable", True, {"action": "retry", "after_seconds": retry_after or 5}
    if status == 401:
        return "reauth_required", False, {"tool": "connect_google_workspace", "arguments": {}}
    if status == 403:
        if reason_key in _SCOPE_REASONS or "insufficient authentication scopes" in lowered:
            arguments: dict[str, Any] = {"capabilities": [capability]} if capability else {}
            return "missing_capability", False, {"tool": "connect_google_workspace", "arguments": arguments}
        if reason_key in _API_DISABLED_REASONS:
            return "permission_denied", False, {"action": "enable_api_in_google_cloud_project"}
        return "permission_denied", False, {"action": "request_access_or_choose_another_resource"}
    if status in {404, 410}:
        return "not_found", False, {"action": "verify_resource_id_or_search_again"}
    if status in {409, 412}:
        return "conflict", False, {"action": "refresh_resource_and_retry"}
    if status == 400:
        return "invalid_input", False, {"action": "correct_arguments", "field_errors": []}
    return "provider_error", False, {"action": "check_arguments_or_retry_later"}


def provider_tool_error(exc: Exception, **context: Any) -> ProviderToolError:
    """Classify a Google API failure as a tool execution error.

    Use as ``raise provider_tool_error(exc, document_id=document_id) from exc``
    in place of returning an ``{"error": ...}`` dictionary as a successful
    result. ``context`` identifies the request (IDs, names); it must never
    contain message bodies, recipients, or credentials.
    """
    status = _provider_status(exc)
    reason = _provider_reason(exc)
    provider_reason = provider_error_reason(exc)
    retry_after = _provider_retry_after(exc)
    code, retryable, action = classify_provider_status(
        status,
        reason=reason,
        provider_reason=provider_reason,
        retry_after=retry_after,
        capability=provider_capability(exc),
    )
    prefix = f"Google API request failed (HTTP {status})" if status else "Google API request failed"
    details: dict[str, Any] = {}
    if context:
        details["context"] = dict(context)
    if provider_reason:
        details["provider_reason"] = provider_reason
    return ProviderToolError(
        code,
        f"{prefix}: {reason}",
        required_action=action,
        retryable=retryable,
        retry_after=retry_after,
        details=details or None,
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
    """A consequential call needs consent that this request cannot collect.

    Raised when the client declared no elicitation capability, the protocol
    version is unknown, there is no live request, or ``MCP_CONSENT`` is
    ``preview_only`` (fail closed: unavailable confirmation is never consent).
    The consent gate sets ``required_action`` to the
    ``prepare_workspace_action`` call that previews the same action;
    ``details.preview`` carries the exact preview text.
    """

    def __init__(
        self,
        action_name: str,
        prompt: str,
        *,
        required_action: dict[str, Any] | None = None,
        reason: str = "the client did not declare elicitation support",
    ) -> None:
        next_step = (
            " Call prepare_workspace_action with required_action.arguments, show the user the "
            "returned preview, then run it with commit_workspace_action."
            if required_action is not None
            else ""
        )
        super().__init__(
            "confirmation_required",
            (
                f"{action_name} needs the user's consent, which this request cannot ask for "
                f"({reason}). No changes were made.{next_step}"
            ),
            required_action=required_action,
            details={"action": action_name, "preview": prompt},
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


class OperationOutcomeError(RecoverableToolError):
    """The state of a recorded operation, reported as a tool result (W4b).

    Codes: ``outcome_unknown`` (a non-idempotent Google call may or may not
    have been applied; verify before retrying), ``operation_in_progress``
    (another request is executing the same operation) and ``operation_failed``
    (the operation already failed; its saved failure is repeated, nothing was
    re-executed). Like confirmation outcomes these are tool results
    (``isError``), never protocol errors. See ``common/operations.py``.
    """


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
        provider_status = _provider_status(error)
    message = str(error)
    lowered = message.lower()
    error_type = error.__class__.__name__
    explicit_code = getattr(error, "error_code", None)
    field_errors: list[dict[str, str]] = []
    rpc_code: int | None = None
    action: dict[str, Any] | None = getattr(error, "required_action", None)
    details: dict[str, Any] | None = getattr(error, "details", None)
    if isinstance(error, ProtocolRejection):
        code, retryable, rpc_code = error.error_code, error.retryable, error.rpc_code
    elif isinstance(explicit_code, str):
        code = explicit_code
        retryable = bool(getattr(error, "retryable", False))
        if not isinstance(error, (ConfirmationError, OperationOutcomeError)):
            # Confirmation and operation outcomes are always tool results.
            rpc_code = PROTOCOL_ERROR_CODES.get(code)
    elif isinstance(error, FastMCPValidationError):
        code, retryable = "invalid_input", False
        field_errors = _field_errors(error)
        message = _validation_message(field_errors) if field_errors else message
    elif error_type == "GoogleAccountConnectionRequired" and "scope" in lowered:
        code, retryable = "missing_capability", False
    elif error_type in {"GoogleAccountConnectionRequired", "GoogleAccountReauthenticationRequired"}:
        code, retryable = "reauth_required", False
    elif is_provider_http_error(error):
        # A Google HTTP error that escaped a tool body unclassified (issue
        # #51): the same mapping as provider_tool_error, so a 403 for missing
        # scopes names the reconnect step and 404 is distinguishable from 403.
        # Checked before the page-token text match below: ``str(HttpError)``
        # embeds the request URL, so any failure of a paged request would
        # otherwise read as a bad page token and echo that URL.
        reason = _provider_reason(error)
        provider_reason = provider_error_reason(error)
        code, retryable, action = classify_provider_status(
            provider_status,
            reason=reason,
            provider_reason=provider_reason,
            retry_after=_provider_retry_after(error),
            capability=provider_capability(error),
        )
        if provider_status == 400 and _rejects_page_token(error, reason):
            code, action = "invalid_page_token", {"action": "retry_without_page_token"}
        message = f"Google API request failed (HTTP {provider_status}): {reason}"
        if provider_reason:
            details = {"provider_reason": provider_reason}
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
    ``required_action``; classify that rather than the generic wrapper. A
    wrapped :class:`McpError` is returned as-is: the execution guard already
    classified it and its ``data`` is the finished envelope.
    """
    current = error
    while isinstance(current, ToolError) and isinstance(current.__cause__, Exception):
        cause = current.__cause__
        if isinstance(cause, McpError):
            return cause
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
            if isinstance(error, McpError):
                # Already classified by the execution guard (common.execution)
                # and wrapped by FastMCP on the way out: forward the finished
                # JSON-RPC error instead of classifying the wrapper a second
                # time, which reduced every protocol error to internal_error.
                raise error from raised
            rpc_code, envelope = classify_error(error)
            if rpc_code is None:
                # Confirmation outcomes (W4a), operation outcomes (W4b:
                # outcome_unknown / operation_in_progress / operation_failed)
                # and every other tool execution failure: an isError result.
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
    "OperationOutcomeError",
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
    "classify_provider_status",
    "error_tool_result",
    "is_provider_http_error",
    "protocol_error",
    "provider_capability",
    "provider_error_reason",
    "provider_tool_error",
    "render_error_message",
    "tool_error_result",
    "unwrap_tool_error",
]
