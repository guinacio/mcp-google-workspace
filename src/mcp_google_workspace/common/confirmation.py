"""One confirmation adapter for every consequential action, on both MCP eras.

Every confirmation site calls :func:`confirm_destructive_action` after it has
validated its arguments and computed the exact action and preview text, and
before it performs any provider mutation. The adapter picks one branch:

* **MCP 2026-07-28 (multi round-trip, SEP-2322)** and in-task execution.
  There is no server-initiated request, so the first round *returns* an
  ``InputRequiredResult`` carrying one form elicitation and a continuation
  (``requestState``). The client answers and retries the same call; the
  second round verifies the continuation and the answer and only then lets
  the site proceed. Nothing is mutated on the asking round.
* **Handshake-era (legacy) requests.** Imperative ``ctx.elicit`` with the same
  schemas as before (``bool``, or an explicit ``confirm`` checkbox).
* **Anything else** (no elicitation capability, unknown protocol version, no
  request at all): fail closed with a ``confirmation_required`` tool result.
  Unavailable confirmation is never treated as consent.

The asking round travels as an exception (:class:`ConfirmationInputRequired`)
from the adapter to the confirmation guard that
``apply_default_tool_annotations`` installs around every async tool body; the
guard returns it as the tool's ``InputRequiredResult``. The guard also records
the tool identity and the validated call arguments, so call sites only pass
the action name and preview. If the exception ever escapes without a guard it
is still a :class:`ConfirmationRequiredError`, so the call fails closed.

Continuation state
------------------
The plaintext continuation is ``cw1.<payload>.<key id>.<mac>``: base64url JSON
claims plus an HMAC-SHA256 under a key derived (HKDF, separate label) from the
request-state key ring. The claims bind:

``op``      random operation id; keys the durable operation record
            (``common/operations.py``) and is never stored or shown raw
``tool``    ``<module>:<registered tool name>`` of the guarded tool
``action``  the site's action name (``reply_email`` vs ``reply_all_email``)
``args``    SHA-256 of the canonical validated arguments (below)
``preview`` SHA-256 of the action name and exact confirmation text
``sub``     the ``(issuer, subject)`` principal digest (local stdio: the trusted
            local principal)
``iat``/``exp`` issue time and expiry (``MCP_CONFIRMATION_TTL_SECONDS``)
``kind``    which answer schema was asked (``value`` or ``confirm``)

On the wire FastMCP additionally seals the whole string with the
``RequestStateSecurity`` key ring (AES-GCM, request/target/principal binding);
the application MAC keeps the same guarantees on paths the wire boundary does
not cover (the Tasks extension stores in-task state server side) and binds the
*inner* tool when the wire call is a proxy (``call_tool`` or
``commit_workspace_action``). No message body, token or answer is ever put in
the state or logged.

Canonical arguments: the guarded tool's validated keyword arguments with
defaults applied and the injected ``Context`` removed, converted with
``pydantic_core.to_jsonable_python`` and serialized as sorted-key, compact,
ASCII JSON (``allow_nan=False``). The confirmation answer never appears in the
arguments: it arrives in ``inputResponses``.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import hashlib
import hmac
import inspect
import json
import logging
import os
import secrets
import threading
import time
from typing import Any, Final, Literal

import mcp_types
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastmcp import Context
from fastmcp.server.elicitation import parse_elicit_response_type
from fastmcp.utilities.types import find_kwarg_by_type
from mcp.server.request_state import RequestStateSecurity
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS
import pydantic_core

from fastmcp.tools import ToolResult

from ..auth.identity import current_principal
from .errors import ConfirmationRejectedError, ConfirmationRequiredError
from .operations import (
    get_operation_store,
    operation_key,
    operation_ref,
    resolve_claim,
    settle_after_failure,
    settle_after_return,
)
from .repeat_safety import current_tracker, tracking_scope

LOGGER = logging.getLogger("mcp_google_workspace.confirmation")

#: Key of the single elicitation inside ``InputRequiredResult.input_requests``.
REQUEST_KEY: Final[str] = "confirm"
REQUEST_STATE_KEYS_ENV: Final[str] = "MCP_REQUEST_STATE_KEYS"
CONFIRMATION_TTL_ENV: Final[str] = "MCP_CONFIRMATION_TTL_SECONDS"
DEFAULT_CONFIRMATION_TTL_SECONDS: Final[int] = 600
REQUEST_STATE_AUDIENCE: Final[str] = "google-workspace-mcp"

_STATE_PREFIX: Final[str] = "cw1"
_STATE_VERSION: Final[int] = 1
_MAC_INFO: Final[bytes] = b"mcp-google-workspace/confirmation-continuation/v1"
_FUTURE_SKEW_SECONDS: Final[int] = 60
_MIN_KEY_BYTES: Final[int] = 32

AnswerKind = Literal["value", "confirm"]


@dataclass
class Confirmation:
    """Elicitation schema with an explicit ``confirm`` checkbox (send/reply)."""

    confirm: bool


# ---------------------------------------------------------------------------
# Configuration: TTL and the shared request-state key ring
# ---------------------------------------------------------------------------


def confirmation_ttl_seconds(environ: Mapping[str, str] | None = None) -> int:
    """Continuation lifetime in seconds (default 10 minutes)."""
    env = os.environ if environ is None else environ
    raw = env.get(CONFIRMATION_TTL_ENV, "").strip()
    if not raw:
        return DEFAULT_CONFIRMATION_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{CONFIRMATION_TTL_ENV} must be an integer number of seconds.") from exc
    if not 30 <= value <= 86_400:
        raise ValueError(f"{CONFIRMATION_TTL_ENV} must be between 30 and 86400 seconds.")
    return value


def configured_request_state_keys(environ: Mapping[str, str] | None = None) -> list[str]:
    """Parse ``MCP_REQUEST_STATE_KEYS``: comma-separated secrets, first = active.

    Each entry is used verbatim as key material (FastMCP's ``RequestStateSecurity``
    accepts any string of at least 32 bytes); generate one with
    ``python -c "import secrets; print(secrets.token_hex(32))"``.
    """
    env = os.environ if environ is None else environ
    keys = [value.strip() for value in env.get(REQUEST_STATE_KEYS_ENV, "").split(",") if value.strip()]
    for index, key in enumerate(keys):
        if len(key.encode("utf-8")) < _MIN_KEY_BYTES:
            raise ValueError(
                f"{REQUEST_STATE_KEYS_ENV} entry {index} is shorter than {_MIN_KEY_BYTES} bytes; "
                'generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
            )
    if len(set(keys)) != len(keys):
        raise ValueError(f"{REQUEST_STATE_KEYS_ENV} lists the same key more than once.")
    return keys


def shared_request_state_keys_configured(environ: Mapping[str, str] | None = None) -> bool:
    try:
        return bool(configured_request_state_keys(environ))
    except ValueError:
        return False


def build_request_state_security(
    *, audience: str = REQUEST_STATE_AUDIENCE, environ: Mapping[str, str] | None = None
) -> RequestStateSecurity:
    """FastMCP ``requestState`` sealing policy shared by every replica.

    With ``MCP_REQUEST_STATE_KEYS`` set, every replica seals and verifies with
    the same ring (``keys[0]`` seals, every listed key verifies). Without it,
    the key is ephemeral and per process: correct for stdio and single-process
    development, but a continuation minted by one replica will not verify on
    another (``server_http`` warns, and multi-worker readiness fails).
    """
    keys = configured_request_state_keys(environ)
    ttl = float(confirmation_ttl_seconds(environ))
    if keys:
        return RequestStateSecurity(keys=keys, ttl=ttl, audience=audience)
    return RequestStateSecurity.ephemeral(ttl=ttl, audience=audience)


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64u_decode(text: str) -> bytes:
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if _b64u(raw) != text:
        raise ValueError("non-canonical base64url")
    return raw


class _ContinuationSigner:
    """HMAC-SHA256 over continuation claims under a derived key ring."""

    def __init__(self, secrets_: Sequence[bytes]) -> None:
        if not secrets_:
            raise ValueError("at least one continuation key is required")
        self._ring: dict[str, bytes] = {}
        self._active = ""
        for index, secret in enumerate(secrets_):
            key = HKDF(algorithm=SHA256(), length=32, salt=None, info=_MAC_INFO).derive(secret)
            kid = _b64u(hashlib.sha256(b"kid:" + key).digest()[:6])
            self._ring[kid] = key
            if index == 0:
                self._active = kid

    def sign(self, payload: bytes) -> tuple[str, str]:
        key = self._ring[self._active]
        return self._active, _b64u(hmac.new(key, payload, hashlib.sha256).digest())

    def verify(self, payload: bytes, kid: str, mac: str) -> bool:
        key = self._ring.get(kid)
        if key is None:
            return False
        expected = _b64u(hmac.new(key, payload, hashlib.sha256).digest())
        return hmac.compare_digest(expected, mac)


_SIGNER: _ContinuationSigner | None = None
_SIGNER_LOCK = threading.Lock()


def _signer() -> _ContinuationSigner:
    global _SIGNER
    with _SIGNER_LOCK:
        if _SIGNER is None:
            keys = configured_request_state_keys()
            material = [key.encode("utf-8") for key in keys] or [os.urandom(32)]
            _SIGNER = _ContinuationSigner(material)
        return _SIGNER


def reset_confirmation_keys() -> None:
    """Re-read the key ring on next use (tests and in-process key rotation)."""
    global _SIGNER
    with _SIGNER_LOCK:
        _SIGNER = None


# ---------------------------------------------------------------------------
# Tool binding captured by the guard
# ---------------------------------------------------------------------------


def canonical_arguments(arguments: Mapping[str, Any]) -> str:
    """Sorted-key compact ASCII JSON of validated tool arguments."""
    jsonable = pydantic_core.to_jsonable_python(dict(arguments))
    return json.dumps(jsonable, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def arguments_digest(arguments: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_arguments(arguments).encode("ascii")).hexdigest()


@dataclass
class _ToolBinding:
    tool: str
    arguments: dict[str, Any]
    _digest: str | None = field(default=None, repr=False)

    def digest(self) -> str:
        if self._digest is None:
            self._digest = arguments_digest(self.arguments)
        return self._digest


_BINDING: ContextVar[_ToolBinding | None] = ContextVar("mcp_confirmation_binding", default=None)


class ConfirmationInputRequired(ConfirmationRequiredError):
    """The asking round of a multi-round-trip confirmation.

    Raised by the adapter and turned into the tool's ``InputRequiredResult`` by
    the confirmation guard. It subclasses ``ConfirmationRequiredError`` so that,
    should it ever escape a guard, the call still fails closed.
    """

    def __init__(
        self,
        action_name: str,
        prompt: str,
        *,
        binding: _ToolBinding,
        input_required: mcp_types.InputRequiredResult,
    ) -> None:
        super().__init__(action_name, prompt)
        self.binding = binding
        self.input_required = input_required


class OperationReplay(Exception):
    """A repeat of a completed confirmed operation: return its saved result.

    Raised by the adapter and returned by this call's guard, so the tool body
    never runs again.
    """

    def __init__(self, binding: _ToolBinding, result: ToolResult) -> None:
        super().__init__("operation replay")
        self.binding = binding
        self.result = result


def _safe_digest(binding: _ToolBinding | None) -> str | None:
    if binding is None:
        return None
    try:
        return binding.digest()
    except (TypeError, ValueError, pydantic_core.PydanticSerializationError):
        return None


def install_confirmation_guard(component: Any, tool_name: str) -> None:
    """Wrap an async tool body so the adapter can bind and ask (idempotent).

    Records ``(tool identity, validated arguments)`` for the duration of the
    call and converts this call's :class:`ConfirmationInputRequired` into the
    ``InputRequiredResult`` the tool returns. The signature, annotations and
    source (via ``__wrapped__``) are preserved, so the published input and
    output schemas do not change.
    """
    original = component.fn
    if getattr(original, "_workspace_confirmation_guard", False):
        return
    if not inspect.iscoroutinefunction(original):
        return
    signature = inspect.signature(original)
    context_kwarg = find_kwarg_by_type(original, Context)
    module = getattr(inspect.unwrap(original), "__module__", "unknown")
    tool_key = f"{module}:{tool_name}"

    @wraps(original)
    async def guarded(*args: Any, **kwargs: Any) -> Any:
        try:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            arguments = {
                name: value
                for name, value in bound.arguments.items()
                if name != context_kwarg and not isinstance(value, Context)
            }
            binding: _ToolBinding | None = _ToolBinding(tool_key, arguments)
        except TypeError:
            binding = None
        token = _BINDING.set(binding)
        try:
            with tracking_scope() as tracker:
                try:
                    result = await original(*args, **kwargs)
                except ConfirmationInputRequired as ask:
                    if binding is None or ask.binding is not binding:
                        raise
                    return ask.input_required
                except OperationReplay as replay:
                    if binding is None or replay.binding is not binding:
                        raise
                    return replay.result
                except GeneratorExit:
                    # The coroutine is being closed, not cancelled: it may not
                    # await again. A claimed record expires through its lease.
                    raise
                except BaseException as exc:
                    # Settle the operation this call claimed (W4b): release,
                    # failed, or outcome_unknown when a non-repeatable Google
                    # call may have been applied. Cancellation is re-raised.
                    replacement = await settle_after_failure(
                        tracker, exc, tool=tool_key, args_digest=_safe_digest(binding)
                    )
                    if replacement is not None and isinstance(exc, Exception):
                        raise replacement from exc
                    raise
                replacement = await settle_after_return(
                    tracker, result, tool=tool_key, args_digest=_safe_digest(binding)
                )
                if replacement is not None:
                    raise replacement
                return result
        finally:
            _BINDING.reset(token)

    setattr(guarded, "_workspace_confirmation_guard", True)
    component.fn = guarded


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


def _request_era(ctx: Context | None) -> Literal["modern", "legacy", "task"] | None:
    if ctx is None:
        return None
    if getattr(ctx, "is_background_task", False) is True:
        return "task"
    request_context = getattr(ctx, "request_context", None)
    version = getattr(request_context, "protocol_version", None)
    if version in MODERN_PROTOCOL_VERSIONS:
        return "modern"
    if version in HANDSHAKE_PROTOCOL_VERSIONS:
        return "legacy"
    return None


def _client_can_elicit(ctx: Context) -> bool:
    """Whether this request's client declared the elicitation capability."""
    try:
        capabilities = ctx.session.client_capabilities
    except Exception:  # noqa: BLE001 - no session means no way to ask
        return False
    return capabilities is not None and getattr(capabilities, "elicitation", None) is not None


async def confirm_destructive_action(
    ctx: Context | None,
    action_name: str,
    message: str,
    *,
    explicit_confirm_field: bool = False,
) -> bool:
    """Gate a consequential action on explicit user confirmation.

    Call after validating arguments and computing ``message`` (the exact
    action preview), before any mutation. Returns ``True`` only for an
    accepted, affirmative answer; ``False`` means declined/cancelled and the
    caller must not mutate anything. On a modern asking round it raises
    :class:`ConfirmationInputRequired` (returned by the tool guard as an
    ``InputRequiredResult``); it raises :class:`ConfirmationRequiredError`
    when the host cannot confirm and :class:`ConfirmationRejectedError` for an
    invalid, expired, foreign, replayed or malformed continuation.
    """
    era = _request_era(ctx)
    if ctx is None or era is None:
        raise ConfirmationRequiredError(action_name, message)
    # A background task has no live request to read capabilities from. Its ask
    # parks the task in ``input_required`` (answered via ``tasks/update``); a
    # client that cannot answer leaves it parked until expiry, which is still
    # "not performed", never consent.
    if era != "task" and not _client_can_elicit(ctx):
        raise ConfirmationRequiredError(action_name, message)
    if era == "legacy":
        return await _legacy_confirm(ctx, message, explicit_confirm_field)
    return await _multi_round_confirm(ctx, action_name, message, explicit_confirm_field)


async def _legacy_confirm(ctx: Context, message: str, explicit_confirm_field: bool) -> bool:
    if explicit_confirm_field:
        response = await ctx.elicit(message, response_type=Confirmation)
        return response.action == "accept" and bool(getattr(response.data, "confirm", False))
    answer = await ctx.elicit(message, response_type=bool)
    return answer.action == "accept" and bool(getattr(answer, "data", False))


def _preview_digest(action_name: str, message: str) -> str:
    return hashlib.sha256(f"{action_name}\x00{message}".encode("utf-8", "surrogatepass")).hexdigest()


def _principal_digest() -> str:
    return current_principal(require_authenticated=False).storage_key


def _reject(action_name: str, reason: str) -> ConfirmationRejectedError:
    # Reason codes only; never the state, the answer or the preview text.
    LOGGER.warning("confirmation rejected action=%s reason=%s", action_name, reason)
    return ConfirmationRejectedError(action_name, reason)


def _seal_state(claims: dict[str, Any]) -> str:
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    kid, mac = _signer().sign(payload)
    return f"{_STATE_PREFIX}.{_b64u(payload)}.{kid}.{mac}"


def _open_state(state: str) -> dict[str, Any] | None:
    parts = state.split(".")
    if len(parts) != 4 or parts[0] != _STATE_PREFIX:
        return None
    try:
        payload = _b64u_decode(parts[1])
    except ValueError:
        return None
    if not _signer().verify(payload, parts[2], parts[3]):
        return None
    try:
        claims = json.loads(payload)
    except ValueError:
        return None
    return claims if isinstance(claims, dict) else None


def _ask(
    action_name: str,
    message: str,
    *,
    binding: _ToolBinding,
    kind: AnswerKind,
    principal: str,
    preview: str,
    operation_id: str,
    ttl_seconds: int,
) -> ConfirmationInputRequired:
    now = int(time.time())
    claims = {
        "v": _STATE_VERSION,
        "op": operation_id,
        "tool": binding.tool,
        "action": action_name,
        "args": binding.digest(),
        "preview": preview,
        "sub": principal,
        "kind": kind,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    schema = parse_elicit_response_type(Confirmation if kind == "confirm" else bool).schema
    request = mcp_types.ElicitRequest(
        params=mcp_types.ElicitRequestFormParams(message=message, requested_schema=schema)
    )
    result = mcp_types.InputRequiredResult(
        input_requests={REQUEST_KEY: request},
        request_state=_seal_state(claims),
    )
    return ConfirmationInputRequired(action_name, message, binding=binding, input_required=result)


def _verify_claims(
    claims: dict[str, Any] | None,
    *,
    action_name: str,
    binding: _ToolBinding,
    kind: AnswerKind,
    principal: str,
    preview: str,
) -> tuple[str, int]:
    if claims is None:
        raise _reject(action_name, "tampered")
    if claims.get("v") != _STATE_VERSION:
        raise _reject(action_name, "malformed")
    operation_id, issued, expires = claims.get("op"), claims.get("iat"), claims.get("exp")
    if not isinstance(operation_id, str) or not isinstance(issued, int) or not isinstance(expires, int):
        raise _reject(action_name, "malformed")
    now = int(time.time())
    if not (issued <= now + _FUTURE_SKEW_SECONDS and now < expires):
        raise _reject(action_name, "expired")
    if not hmac.compare_digest(str(claims.get("sub", "")), principal):
        raise _reject(action_name, "principal_mismatch")
    if claims.get("tool") != binding.tool or claims.get("action") != action_name:
        raise _reject(action_name, "tool_mismatch")
    if claims.get("kind") != kind:
        raise _reject(action_name, "tool_mismatch")
    if not hmac.compare_digest(str(claims.get("args", "")), binding.digest()):
        raise _reject(action_name, "arguments_changed")
    if not hmac.compare_digest(str(claims.get("preview", "")), preview):
        raise _reject(action_name, "preview_changed")
    return operation_id, expires - now


def _read_answer(
    responses: Mapping[str, Any] | None, *, action_name: str, kind: AnswerKind
) -> bool:
    if not responses or REQUEST_KEY not in responses:
        raise _reject(action_name, "missing_answer")
    answer = responses[REQUEST_KEY]
    if not isinstance(answer, mcp_types.ElicitResult):
        raise _reject(action_name, "wrong_answer_type")
    if answer.action in ("decline", "cancel"):
        return False
    if answer.action != "accept":
        raise _reject(action_name, "wrong_answer_type")
    content = answer.content
    field_name = "confirm" if kind == "confirm" else "value"
    if not isinstance(content, dict) or set(content) != {field_name}:
        raise _reject(action_name, "wrong_answer_type")
    value = content[field_name]
    if not isinstance(value, bool):
        raise _reject(action_name, "wrong_answer_type")
    return value


async def _multi_round_confirm(
    ctx: Context, action_name: str, message: str, explicit_confirm_field: bool
) -> bool:
    binding = _BINDING.get()
    if binding is None:
        # Not running inside a guarded tool body: there is nothing to bind the
        # continuation to, so the request cannot be resumed safely.
        LOGGER.warning("confirmation guard missing for action=%s; failing closed", action_name)
        raise ConfirmationRequiredError(action_name, message)
    try:
        binding.digest()
    except (TypeError, ValueError, pydantic_core.PydanticSerializationError):
        LOGGER.warning("confirmation arguments not canonicalizable for action=%s", action_name)
        raise ConfirmationRequiredError(action_name, message) from None
    kind: AnswerKind = "confirm" if explicit_confirm_field else "value"
    principal = _principal_digest()
    preview = _preview_digest(action_name, message)
    try:
        state = ctx.request_state
        responses = ctx.input_responses
    except Exception:  # noqa: BLE001 - unparseable continuation fields
        raise _reject(action_name, "malformed") from None

    if state is None:
        if responses:
            # An answer with no continuation is not consent to anything.
            raise _reject(action_name, "answer_without_continuation")
        # Asking round: open the operation record (awaiting_input) that the
        # answering round will claim. The record holds digests only.
        operation_id = secrets.token_urlsafe(18)
        ttl = confirmation_ttl_seconds()
        await get_operation_store().open_confirmation(
            operation_key(principal, operation_id),
            tool=binding.tool,
            action=action_name,
            args_digest=binding.digest(),
            preview_digest=preview,
            ttl_seconds=ttl + _FUTURE_SKEW_SECONDS,
        )
        raise _ask(
            action_name,
            message,
            binding=binding,
            kind=kind,
            principal=principal,
            preview=preview,
            operation_id=operation_id,
            ttl_seconds=ttl,
        )

    operation_id, _remaining = _verify_claims(
        _open_state(state),
        action_name=action_name,
        binding=binding,
        kind=kind,
        principal=principal,
        preview=preview,
    )
    accepted = _read_answer(responses, action_name=action_name, kind=kind)
    ref = operation_ref(operation_id)
    outcome = await get_operation_store().claim(
        operation_key(principal, operation_id),
        kind="confirmation",
        ref=ref,
        answer="accept" if accepted else "decline",
        args_digest=binding.digest(),
        preview_digest=preview,
    )
    if outcome.status == "claimed":
        tracker = current_tracker()
        if tracker is not None:
            # The guard settles this claim when the tool body finishes.
            tracker.operation = outcome.handle
        return accepted
    if outcome.status == "missing":
        raise _reject(action_name, "expired")
    if outcome.status == "answer_changed":
        # One continuation carries one answer: a decline cannot become an
        # accept (or the reverse) by replaying it.
        raise _reject(action_name, "replayed")
    if outcome.status == "payload_changed":
        raise _reject(action_name, "arguments_changed")
    # Already executed (or executing): never run the body again. Return the
    # saved result, "in progress", the saved failure, or outcome_unknown.
    raise OperationReplay(binding, await resolve_claim(outcome, tool=binding.tool, ref=ref))


__all__ = [
    "Confirmation",
    "ConfirmationInputRequired",
    "OperationReplay",
    "REQUEST_KEY",
    "build_request_state_security",
    "canonical_arguments",
    "confirm_destructive_action",
    "confirmation_ttl_seconds",
    "configured_request_state_keys",
    "install_confirmation_guard",
    "reset_confirmation_keys",
    "shared_request_state_keys_configured",
]
