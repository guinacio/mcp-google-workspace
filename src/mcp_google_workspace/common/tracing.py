"""Trace propagation from MCP request ``_meta`` with untrusted input bounded.

MCP carries W3C trace context in the request ``_meta`` (``traceparent`` and
``tracestate``). The values come from the client, so they are validated before
they parent a span: a malformed ``traceparent`` (wrong shape, the invalid
all-zero IDs, or the reserved ``ff`` version) is ignored; ``tracestate`` is
kept only when it is a well-formed list of at most 32 members and 512
characters. W3C ``baggage`` is never accepted from a client (``_meta`` or HTTP
header) and any ambient baggage is cleared for the duration of a tool call, so
client-chosen key/values cannot flow into downstream calls or telemetry.

Nothing here reads or records arguments, tokens, mail content or continuation
state; span attributes are limited to the tool name, a hashed principal, a
correlation ID and the round/outcome classification.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import re
from typing import Any, Final

from opentelemetry import baggage
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_TRACEPARENT: Final = re.compile(r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_TRACESTATE_MEMBER: Final = re.compile(
    r"^(?:[a-z][_0-9a-z\-*/]{0,255}|[a-z0-9][_0-9a-z\-*/]{0,240}@[a-z][_0-9a-z\-*/]{0,13})"
    r"=[\x20-\x2b\x2d-\x3c\x3e-\x7e]{0,255}[\x21-\x2b\x2d-\x3c\x3e-\x7e]$"
)
MAX_TRACESTATE_LENGTH: Final[int] = 512
MAX_TRACESTATE_MEMBERS: Final[int] = 32
_PROPAGATOR: Final = TraceContextTextMapPropagator()


def valid_traceparent(value: object) -> str | None:
    if not isinstance(value, str) or len(value) != 55:
        return None
    match = _TRACEPARENT.fullmatch(value)
    if match is None:
        return None
    version, trace_id, span_id, _flags = match.groups()
    if version == "ff" or trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    return value


def bounded_tracestate(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > MAX_TRACESTATE_LENGTH:
        return None
    members = [member.strip() for member in value.split(",") if member.strip()]
    if not members or len(members) > MAX_TRACESTATE_MEMBERS:
        return None
    if not all(_TRACESTATE_MEMBER.fullmatch(member) for member in members):
        return None
    return ",".join(members)


def _meta_mapping(meta: Any) -> Mapping[str, Any]:
    if meta is None:
        return {}
    if isinstance(meta, Mapping):
        return meta
    dump = getattr(meta, "model_dump", None)
    if callable(dump):
        dumped = dump(by_alias=True)
        return dumped if isinstance(dumped, Mapping) else {}
    return {}


def request_meta() -> Mapping[str, Any]:
    """The current request's ``_meta`` (empty outside a request)."""
    try:
        from fastmcp.server.dependencies import fastmcp_request_ctx
    except ImportError:  # pragma: no cover - framework layout guard
        return {}
    request_context = fastmcp_request_ctx.get()
    return _meta_mapping(getattr(request_context, "meta", None))


def parent_context_from_meta(meta: Any) -> Context | None:
    """Validated remote parent context from ``_meta``, or ``None``.

    The returned context is built from an empty context, so it carries no
    baggage (client-supplied or ambient).
    """
    values = _meta_mapping(meta)
    traceparent = valid_traceparent(values.get("traceparent"))
    if traceparent is None:
        return None
    carrier = {"traceparent": traceparent}
    tracestate = bounded_tracestate(values.get("tracestate"))
    if tracestate is not None:
        carrier["tracestate"] = tracestate
    extracted = _PROPAGATOR.extract(carrier, context=Context())
    span_context = trace.get_current_span(extracted).get_span_context()
    return extracted if span_context.is_valid else None


def tool_span_context(meta: Any) -> Context:
    """Context to start a tool span in.

    Keeps an already-valid active span (FastMCP's request span, itself parented
    from the same ``_meta``); otherwise adopts the validated ``_meta`` parent.
    Baggage is always removed.
    """
    current = otel_context.get_current()
    if trace.get_current_span(current).get_span_context().is_valid:
        return baggage.clear(context=current)
    parent = parent_context_from_meta(meta)
    return parent if parent is not None else baggage.clear(context=current)


@contextmanager
def without_baggage() -> Iterator[None]:
    """Clear ambient baggage for the enclosed tool execution."""
    token = otel_context.attach(baggage.clear())
    try:
        yield
    finally:
        otel_context.detach(token)


__all__ = [
    "MAX_TRACESTATE_LENGTH",
    "MAX_TRACESTATE_MEMBERS",
    "bounded_tracestate",
    "parent_context_from_meta",
    "request_meta",
    "tool_span_context",
    "valid_traceparent",
    "without_baggage",
]
