"""Durable operation records for consequential actions (W4b).

One store records every prepare/commit action and every multi-round-trip
confirmation, replacing destructive commit-token consumption and the W4a
single-use replay set. Each record follows

``prepared -> awaiting_input -> executing -> succeeded | failed | outcome_unknown``

* ``prepared``        a ``prepare_workspace_action`` token, not yet committed.
* ``awaiting_input``  a confirmation question is outstanding (asked by the
  adapter, or by the tool a commit is running).
* ``executing``       exclusively claimed by one request (``claim_id``) until
  ``lease_until``. A second request gets ``operation_in_progress``; after the
  lease the record becomes ``outcome_unknown`` (a crash or lost worker between
  claim and completion is never re-executed automatically).
* ``succeeded``       finished; a minimized copy of the result is saved and
  returned to any repeat of the same commit or confirmed continuation, without
  re-executing. A declined confirmation also finishes here (the tool's
  ``status: cancelled`` result, ``answer: decline``).
* ``failed``          Google definitively rejected a non-repeatable call; the
  saved failure is repeated.
* ``outcome_unknown`` a non-repeatable Google call may or may not have been
  applied (timeout, disconnect, cancellation, 5xx, lost worker). Repeats run
  the cheap reconciliation check when one exists (``common/reconciliation.py``)
  and otherwise return ``outcome_unknown`` with verification guidance.

Transitions are compare-and-set on the record revision (the W3
:class:`~mcp_google_workspace.common.app_state.AppStateStore`: a process lock
in memory, Lua scripts in Redis), so of two simultaneous claims exactly one
wins. Stored fields are listed in ``docs/migration/W4_CONFIRMATION_POLICY.md``;
records never hold Google tokens, continuation strings, confirmation prompts
or answers' content, and bound commit arguments are dropped once the record
is terminal. Redis bodies are Fernet-encrypted with the deployment key ring.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from typing import Any, Final, Literal

import anyio
import mcp.types as mt
import mcp_types
import pydantic_core
from fastmcp.exceptions import McpError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import InputRequiredToolResult, ToolResult

from ..auth.identity import current_principal
from .app_state import AppStateStore, MemoryAppStateStore, RedisAppStateStore, app_state_backend_url
from .crypto import FernetKeyring
from .errors import OperationOutcomeError
from .repeat_safety import MutationTracker, ProviderCall, RepeatSafety, current_tracker, tracking_scope

LOGGER = logging.getLogger("mcp_google_workspace.operations")

OperationState = Literal[
    "prepared", "awaiting_input", "executing", "succeeded", "failed", "outcome_unknown"
]
OperationKind = Literal["commit", "confirmation", "call", "task"]
#: ``uncertain_calls`` entry of a task whose previous delivery started and
#: never finished (its worker stopped; Docket delivered the task again).
REDELIVERED_TASK: Final[str] = "(task redelivered after its worker stopped mid-execution)"
TERMINAL_STATES: Final[frozenset[str]] = frozenset({"succeeded", "failed", "outcome_unknown"})

RETENTION_ENV: Final[str] = "MCP_OPERATION_RETENTION_SECONDS"
LEASE_ENV: Final[str] = "MCP_OPERATION_LEASE_SECONDS"
RESULT_MAX_BYTES_ENV: Final[str] = "MCP_OPERATION_RESULT_MAX_BYTES"
DEFAULT_RETENTION_SECONDS: Final[int] = 86_400
DEFAULT_LEASE_SECONDS: Final[int] = 900
DEFAULT_RESULT_MAX_BYTES: Final[int] = 65_536
REDIS_PREFIX: Final[str] = "mcp:operation:v1"
OPERATION_META_KEY: Final[str] = "mcp-google-workspace/operation"

_RECORD_VERSION: Final[int] = 1
_CAS_ATTEMPTS: Final[int] = 16
_SETTLE_TIMEOUT_SECONDS: Final[float] = 10.0
_OMITTED: Final[str] = "[not retained in the operation record]"
# String values under these keys are message content, not identifiers; saved
# results keep their type (so replays still match the tool's output schema)
# but not their text.
_CONTENT_KEYS: Final[frozenset[str]] = frozenset(
    {"raw", "text", "text_body", "html_body", "body", "snippet", "formattedText", "argumentText", "textContent"}
)


def _int_env(name: str, default: int, low: int, high: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}.")
    return value


def retention_seconds() -> int:
    """How long terminal records are kept for replay and reconciliation (default 24 h)."""
    return _int_env(RETENTION_ENV, DEFAULT_RETENTION_SECONDS, 300, 2_592_000)


def lease_seconds() -> int:
    """Executing lease; keep it above ``MCP_TOOL_DEADLINE_SECONDS``/``MCP_EXPENSIVE_DEADLINE_SECONDS``."""
    return _int_env(LEASE_ENV, DEFAULT_LEASE_SECONDS, 30, 86_400)


def result_max_bytes() -> int:
    return _int_env(RESULT_MAX_BYTES_ENV, DEFAULT_RESULT_MAX_BYTES, 1_024, 1_048_576)


def pending_ttl_seconds() -> int:
    """Lifetime of ``prepared`` and ``awaiting_input`` records.

    Both follow ``MCP_CONFIRMATION_TTL_SECONDS`` (default 600 s), so a commit
    token and the confirmation question asked while committing it expire
    together (W4a had 300 s tokens and 600 s questions).
    """
    from .confirmation import confirmation_ttl_seconds

    return confirmation_ttl_seconds()


# ---------------------------------------------------------------------------
# Result minimization
# ---------------------------------------------------------------------------


def _redact(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {name: _redact(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, str) and key in _CONTENT_KEYS and value:
        return _OMITTED
    return value


def minimize_result(value: Any) -> tuple[Any, bool]:
    """Return ``(saved, retained)``: the JSON result without message text, size-capped."""
    try:
        jsonable = pydantic_core.to_jsonable_python(value)
    except (TypeError, ValueError, pydantic_core.PydanticSerializationError):
        return None, False
    redacted = _redact(jsonable)
    try:
        encoded = json.dumps(redacted, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return None, False
    if len(encoded.encode("utf-8")) > result_max_bytes():
        return None, False
    return redacted, True


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def operation_key(principal: str, operation_id: str) -> str:
    """Record key: principal scope + SHA-256 of the raw id (never stored raw)."""
    return f"{principal}:{hashlib.sha256(operation_id.encode('utf-8')).hexdigest()}"


def operation_ref(operation_id: str) -> str:
    """Public, non-reversible reference to an operation, safe to show the model."""
    return "op_" + hashlib.sha256(b"ref\x00" + operation_id.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class OperationHandle:
    """One request's exclusive claim on an operation."""

    key: str
    claim_id: str
    ref: str
    kind: str
    tool: str


ClaimStatus = Literal[
    "claimed", "saved", "in_progress", "unknown", "failed", "missing", "answer_changed", "payload_changed"
]


@dataclass(frozen=True, slots=True)
class ClaimOutcome:
    status: ClaimStatus
    handle: OperationHandle | None = None
    record: dict[str, Any] | None = None
    key: str | None = None


class OperationStore:
    """Typed lifecycle over a revisioned :class:`AppStateStore` backend."""

    def __init__(self, backend: AppStateStore, *, clock: Callable[[], float] = time.time) -> None:
        self._backend = backend
        self._clock = clock

    @property
    def backend_name(self) -> str:
        return self._backend.backend_name

    def now(self) -> float:
        return self._clock()

    async def get(self, key: str) -> dict[str, Any] | None:
        record = await self._backend.get(key)
        return None if record is None else record.value

    async def create(self, key: str, record: dict[str, Any], *, ttl_seconds: float) -> bool:
        return await self._backend.create(key, record, ttl_seconds=ttl_seconds) is not None

    async def _update(
        self,
        key: str,
        decide: Callable[[dict[str, Any]], tuple[dict[str, Any] | None, float, Any]],
        *,
        missing: Any = None,
    ) -> Any:
        """Optimistic read-decide-CAS loop; ``decide`` returns (new, ttl, outcome)."""
        for _ in range(_CAS_ATTEMPTS):
            current = await self._backend.get(key)
            if current is None:
                return missing
            new, ttl, outcome = decide(dict(current.value))
            if new is None:
                return outcome
            result = await self._backend.compare_and_set(
                key, current.revision, new, ttl_seconds=max(1.0, ttl)
            )
            if result.status == "ok":
                return outcome
            if result.status == "missing":
                return missing
        raise RuntimeError("operation record is contended; retry the request")

    # -- creation ---------------------------------------------------------

    def _base(self, kind: OperationKind, state: OperationState, tool: str, **fields: Any) -> dict[str, Any]:
        now = self.now()
        return {
            "v": _RECORD_VERSION,
            "kind": kind,
            "state": state,
            "tool": tool,
            "created_at": now,
            "updated_at": now,
            **fields,
        }

    async def open_commit(
        self, key: str, tool: str, arguments: dict[str, Any], args_digest: str
    ) -> float:
        ttl = pending_ttl_seconds()
        expires_at = self.now() + ttl
        record = self._base(
            "commit",
            "prepared",
            tool,
            arguments=arguments,
            args_digest=args_digest,
            pending_expires_at=expires_at,
        )
        if not await self.create(key, record, ttl_seconds=ttl):
            raise RuntimeError("operation id collision")
        return expires_at

    async def open_confirmation(
        self, key: str, *, tool: str, action: str, args_digest: str, preview_digest: str, ttl_seconds: int
    ) -> None:
        record = self._base(
            "confirmation",
            "awaiting_input",
            tool,
            action=action,
            args_digest=args_digest,
            preview_digest=preview_digest,
            answer=None,
            pending_expires_at=self.now() + ttl_seconds,
        )
        if not await self.create(key, record, ttl_seconds=ttl_seconds):
            raise RuntimeError("operation id collision")

    async def record_uncertain_call(
        self,
        key: str,
        *,
        tool: str,
        args_digest: str | None,
        calls: list[ProviderCall],
        hints: list[dict[str, Any]],
    ) -> None:
        """Evidence for a plain (untracked) call whose outcome is unknown."""
        record = self._base(
            "call",
            "outcome_unknown",
            tool,
            args_digest=args_digest,
            uncertain_calls=sorted({call.method_id for call in calls}),
            reconcile=hints,
        )
        await self.create(key, record, ttl_seconds=retention_seconds())

    # -- claim ------------------------------------------------------------

    async def claim(
        self,
        key: str,
        *,
        kind: OperationKind,
        ref: str,
        answer: str | None = None,
        args_digest: str | None = None,
        preview_digest: str | None = None,
    ) -> ClaimOutcome:
        claim_id = secrets.token_hex(16)

        def decide(record: dict[str, Any]) -> tuple[dict[str, Any] | None, float, ClaimOutcome]:
            now = self.now()
            if record.get("kind") != kind:
                return None, 0, ClaimOutcome("missing", key=key)
            if args_digest is not None and record.get("args_digest") != args_digest:
                return None, 0, ClaimOutcome("payload_changed", record=record, key=key)
            if preview_digest is not None and record.get("preview_digest") != preview_digest:
                return None, 0, ClaimOutcome("payload_changed", record=record, key=key)
            recorded = record.get("answer")
            if answer is not None and recorded is not None and recorded != answer:
                return None, 0, ClaimOutcome("answer_changed", record=record, key=key)
            state = record.get("state")
            if state in ("prepared", "awaiting_input"):
                if float(record.get("pending_expires_at") or 0) <= now:
                    return None, 0, ClaimOutcome("missing", key=key)
                updated = {
                    **record,
                    "state": "executing",
                    "resume_state": state,
                    "claim_id": claim_id,
                    "lease_until": now + lease_seconds(),
                    "answer": answer if answer is not None else recorded,
                    "updated_at": now,
                }
                handle = OperationHandle(key, claim_id, ref, kind, str(record.get("tool", "")))
                return updated, lease_seconds() + retention_seconds(), ClaimOutcome(
                    "claimed", handle=handle, record=updated, key=key
                )
            if state == "executing":
                if float(record.get("lease_until") or 0) > now:
                    return None, 0, ClaimOutcome("in_progress", record=record, key=key)
                # The claimant never completed (crash, lost worker, stuck
                # thread). Its effect is unknown; never re-execute blindly.
                updated = {
                    **record,
                    "state": "outcome_unknown",
                    "lease_expired": True,
                    "arguments": None,
                    "updated_at": now,
                }
                return updated, retention_seconds(), ClaimOutcome("unknown", record=updated, key=key)
            if state == "succeeded":
                return None, 0, ClaimOutcome("saved", record=record, key=key)
            if state == "failed":
                return None, 0, ClaimOutcome("failed", record=record, key=key)
            return None, 0, ClaimOutcome("unknown", record=record, key=key)

        outcome: ClaimOutcome = await self._update(key, decide, missing=ClaimOutcome("missing", key=key))
        return outcome

    async def claim_task_delivery(self, key: str, *, tool: str, ref: str) -> ClaimOutcome:
        """Claim one Docket delivery of a background task (W7a).

        The first delivery creates an ``executing`` record. Docket delivers a
        task again only when the worker running it stopped renewing its lease
        (crash, SIGKILL, lost Redis), so finding the record still
        ``executing`` means the previous run started and never finished: it
        becomes ``outcome_unknown`` and the body is not run again. A record
        released before any non-repeatable call (``prepared``) or parked on a
        question (``awaiting_input``) is claimed again; ``succeeded`` /
        ``failed`` are replayed.
        """
        claim_id = secrets.token_hex(16)
        handle = OperationHandle(key, claim_id, ref, "task", tool)
        now = self.now()
        record = self._base(
            "task",
            "executing",
            tool,
            claim_id=claim_id,
            lease_until=now + lease_seconds(),
            resume_state="prepared",
            pending_expires_at=now + lease_seconds(),
        )
        if await self.create(key, record, ttl_seconds=lease_seconds() + retention_seconds()):
            return ClaimOutcome("claimed", handle=handle, record=record, key=key)

        def decide(current: dict[str, Any]) -> tuple[dict[str, Any] | None, float, ClaimOutcome]:
            now = self.now()
            state = current.get("state")
            if current.get("kind") != "task":
                return None, 0, ClaimOutcome("missing", key=key)
            if state == "executing":
                updated = {
                    **current,
                    "state": "outcome_unknown",
                    "lease_expired": True,
                    "redelivered": True,
                    "uncertain_calls": [REDELIVERED_TASK],
                    "updated_at": now,
                }
                return updated, retention_seconds(), ClaimOutcome("unknown", record=updated, key=key)
            if state in ("prepared", "awaiting_input"):
                updated = {
                    **current,
                    "state": "executing",
                    "resume_state": state,
                    "claim_id": claim_id,
                    "lease_until": now + lease_seconds(),
                    "pending_expires_at": now + lease_seconds(),
                    "updated_at": now,
                }
                return updated, lease_seconds() + retention_seconds(), ClaimOutcome(
                    "claimed", handle=handle, record=updated, key=key
                )
            if state == "succeeded":
                return None, 0, ClaimOutcome("saved", record=current, key=key)
            if state == "failed":
                return None, 0, ClaimOutcome("failed", record=current, key=key)
            return None, 0, ClaimOutcome("unknown", record=current, key=key)

        outcome: ClaimOutcome = await self._update(key, decide, missing=ClaimOutcome("missing", key=key))
        return outcome

    # -- settlement -------------------------------------------------------

    async def _settle(
        self,
        handle: OperationHandle,
        *,
        allowed: frozenset[str],
        change: Callable[[dict[str, Any], float], tuple[dict[str, Any], float]],
    ) -> bool:
        def decide(record: dict[str, Any]) -> tuple[dict[str, Any] | None, float, bool]:
            if record.get("claim_id") != handle.claim_id or record.get("state") not in allowed:
                return None, 0, False
            updated, ttl = change(record, self.now())
            return {**updated, "updated_at": self.now()}, ttl, True

        settled: bool = await self._update(handle.key, decide, missing=False)
        if not settled:
            LOGGER.warning("operation settlement skipped ref=%s (claim superseded or expired)", handle.ref)
        return settled

    async def succeed(self, handle: OperationHandle, result: Any) -> None:
        saved, retained = minimize_result(result)

        def change(record: dict[str, Any], _now: float) -> tuple[dict[str, Any], float]:
            return (
                {
                    **record,
                    "state": "succeeded",
                    "result": saved,
                    "result_retained": retained,
                    "arguments": None,
                    "lease_until": None,
                },
                float(retention_seconds()),
            )

        await self._settle(handle, allowed=frozenset({"executing", "outcome_unknown"}), change=change)

    async def fail(self, handle: OperationHandle, error: dict[str, Any]) -> None:
        def change(record: dict[str, Any], _now: float) -> tuple[dict[str, Any], float]:
            return (
                {**record, "state": "failed", "error": error, "arguments": None, "lease_until": None},
                float(retention_seconds()),
            )

        await self._settle(handle, allowed=frozenset({"executing", "outcome_unknown"}), change=change)

    async def mark_unknown(
        self, handle: OperationHandle, calls: list[ProviderCall], hints: list[dict[str, Any]]
    ) -> None:
        def change(record: dict[str, Any], _now: float) -> tuple[dict[str, Any], float]:
            return (
                {
                    **record,
                    "state": "outcome_unknown",
                    "uncertain_calls": sorted({call.method_id for call in calls}),
                    "reconcile": hints,
                    "arguments": None,
                    "lease_until": None,
                },
                float(retention_seconds()),
            )

        await self._settle(handle, allowed=frozenset({"executing"}), change=change)

    async def park(self, handle: OperationHandle) -> None:
        """The claimed call asked a confirmation question: nothing ran yet."""
        ttl = pending_ttl_seconds()

        def change(record: dict[str, Any], now: float) -> tuple[dict[str, Any], float]:
            return (
                {
                    **record,
                    "state": "awaiting_input",
                    "claim_id": None,
                    "lease_until": None,
                    "pending_expires_at": now + ttl,
                },
                float(ttl),
            )

        await self._settle(handle, allowed=frozenset({"executing"}), change=change)

    async def release(self, handle: OperationHandle) -> None:
        """Nothing non-repeatable reached Google: return to the pre-claim state."""

        def change(record: dict[str, Any], now: float) -> tuple[dict[str, Any], float]:
            expires_at = float(record.get("pending_expires_at") or now)
            return (
                {
                    **record,
                    "state": record.get("resume_state") or "prepared",
                    "claim_id": None,
                    "lease_until": None,
                },
                max(1.0, expires_at - now),
            )

        await self._settle(handle, allowed=frozenset({"executing"}), change=change)

    async def resolve_unknown(self, key: str, result: Any) -> dict[str, Any] | None:
        """Reconciliation proved an ``outcome_unknown`` operation succeeded."""
        saved, retained = minimize_result(result)

        def decide(record: dict[str, Any]) -> tuple[dict[str, Any] | None, float, dict[str, Any] | None]:
            if record.get("state") != "outcome_unknown":
                return None, 0, record if record.get("state") == "succeeded" else None
            updated = {
                **record,
                "state": "succeeded",
                "result": saved,
                "result_retained": retained,
                "reconciled": True,
                "updated_at": self.now(),
            }
            return updated, float(retention_seconds()), updated

        resolved: dict[str, Any] | None = await self._update(key, decide, missing=None)
        return resolved


# ---------------------------------------------------------------------------
# Process-wide store
# ---------------------------------------------------------------------------


def memory_operation_store(*, clock: Callable[[], float] = time.time) -> OperationStore:
    """In-process store (stdio, single process, tests); ``clock`` drives TTLs and leases."""
    return OperationStore(MemoryAppStateStore(max_entries=100_000, clock=clock), clock=clock)


def build_operation_store() -> OperationStore:
    """Memory for stdio/tests; Redis (encrypted) when ``MCP_REDIS_URL`` is set outside the bundle."""
    url = app_state_backend_url()
    if url is None:
        return memory_operation_store()
    try:
        keyring = FernetKeyring.from_environment()
    except ValueError as exc:
        raise ValueError(
            "Operation records in Redis require the token encryption key ring "
            "(MCP_SECRET_FILE, MCP_TOKEN_ENCRYPTION_KEYS or MCP_TOKEN_ENCRYPTION_KEY)."
        ) from exc
    import redis.asyncio as redis_asyncio

    return OperationStore(
        RedisAppStateStore(redis_asyncio.Redis.from_url(url), prefix=REDIS_PREFIX, keyring=keyring)
    )


_STORE: OperationStore | None = None
_STORE_LOCK = threading.Lock()


def get_operation_store() -> OperationStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = build_operation_store()
        return _STORE


def set_operation_store(store: OperationStore | None) -> None:
    """Install a store (``None`` re-resolves from the environment on next use)."""
    global _STORE
    with _STORE_LOCK:
        _STORE = store


def current_principal_key() -> str:
    return current_principal(require_authenticated=False).storage_key


# ---------------------------------------------------------------------------
# Outcome errors and replay
# ---------------------------------------------------------------------------


def _verify_step(call: ProviderCall) -> dict[str, Any]:
    method, ids = call.method_id, call.ids
    service = method.split(".", 1)[0]
    if method in ("gmail.users.messages.send", "gmail.users.drafts.send"):
        return {
            "tool": "gmail_search_emails",
            "arguments": {"query": "in:sent newer_than:1d"},
            "check": "Look for the message by recipient and subject before sending again.",
        }
    if method == "gmail.users.drafts.create":
        return {"tool": "gmail_list_drafts", "arguments": {}}
    if method.startswith("gmail.users.labels."):
        return {"tool": "gmail_list_labels", "arguments": {}}
    if method.startswith("gmail.users.settings.filters."):
        return {"tool": "gmail_list_filters", "arguments": {}}
    if method.startswith("gmail.users.settings.forwardingAddresses."):
        return {"tool": "gmail_list_forwarding_addresses", "arguments": {}}
    if service == "calendar":
        return {
            "tool": "calendar_search_events",
            "arguments": {"calendar_id": ids.get("calendarId", "primary")},
            "check": "Search the event's time window. Pass idempotency_key to calendar_create_event so a retry is safe.",
        }
    if method.startswith("drive.permissions."):
        return {"tool": "drive_list_permissions", "arguments": {"file_id": ids.get("fileId")}}
    if service == "drive":
        return {
            "tool": "drive_list_files",
            "arguments": {"order_by": "createdTime desc", "page_size": 10},
            "check": "Look for the new file or copy by name.",
        }
    if service == "sheets" and "spreadsheetId" in ids:
        return {"tool": "sheets_get_spreadsheet", "arguments": {"spreadsheet_id": ids["spreadsheetId"]}}
    if service == "sheets":
        return {"tool": "drive_list_files", "arguments": {"order_by": "createdTime desc", "page_size": 10}}
    if service == "docs" and "documentId" in ids:
        return {"tool": "docs_get_document", "arguments": {"document_id": ids["documentId"]}}
    if service == "slides" and "presentationId" in ids:
        return {"tool": "slides_get_presentation", "arguments": {"presentation_id": ids["presentationId"]}}
    if service == "forms" and "formId" in ids:
        return {"tool": "forms_get_form", "arguments": {"form_id": ids["formId"]}}
    if method.startswith("tasks.tasks.") and "tasklist" in ids:
        return {"tool": "tasks_list_tasks", "arguments": {"tasklist_id": ids["tasklist"]}}
    if service == "tasks":
        return {"tool": "tasks_list_tasklists", "arguments": {}}
    if method.startswith("people.contactGroups."):
        return {"tool": "people_list_contact_groups", "arguments": {}}
    if service == "people":
        return {"tool": "people_list_contacts", "arguments": {}}
    if service == "keep":
        return {"tool": "keep_list_notes", "arguments": {"request": {}}}
    if service == "chat" and "parent" in ids:
        return {"tool": "chat_list_messages", "arguments": {"request": {"space_name": ids["parent"]}}}
    return {"check": f"Inspect the target of {method} with the service's read tools."}


def _hint_step(hint: Mapping[str, Any]) -> dict[str, Any] | None:
    if hint.get("kind") == "gmail_sent" and hint.get("rfc822_message_id"):
        return {
            "tool": "gmail_search_emails",
            "arguments": {"query": f"in:sent rfc822msgid:{hint['rfc822_message_id']}"},
            "check": "A match means the email was sent; do not send it again.",
        }
    return None


def verification_steps(methods: list[str], calls: list[ProviderCall], hints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    hinted: set[str] = set()
    for hint in hints:
        step = _hint_step(hint)
        if step is not None and step not in steps:
            steps.append(step)
            hinted.add("gmail.users.messages.send")
    known = {call.method_id: call for call in calls}
    for method in methods:
        if method in hinted:
            continue
        step = _verify_step(known.get(method) or ProviderCall(method, RepeatSafety.NON_IDEMPOTENT))
        if step not in steps:
            steps.append(step)
    return steps


def outcome_unknown_error(
    tool: str,
    *,
    ref: str | None,
    methods: list[str],
    calls: list[ProviderCall] | None = None,
    hints: list[dict[str, Any]] | None = None,
) -> OperationOutcomeError:
    display = tool.rsplit(":", 1)[-1]
    return OperationOutcomeError(
        "outcome_unknown",
        (
            f"The outcome of {display} is unknown: a Google call that is not safe to repeat "
            f"({', '.join(methods) or 'unknown method'}) may or may not have been applied before "
            "the request timed out, was cancelled, or lost its connection. Nothing was retried."
        ),
        required_action={
            "action": "verify_before_retry",
            "operation_ref": ref,
            "uncertain_calls": methods,
            "verify": verification_steps(methods, calls or [], hints or []),
            "instructions": (
                "Check whether the change exists with the listed read tools before doing it again. "
                "Repeating the same commit token or confirmed request re-checks this operation; "
                "it never re-executes it."
            ),
        },
        retryable=False,
    )


def in_progress_error(tool: str, ref: str) -> OperationOutcomeError:
    return OperationOutcomeError(
        "operation_in_progress",
        f"{tool.rsplit(':', 1)[-1]} is already being executed by another request for this operation.",
        required_action={"action": "retry", "after_seconds": 2, "operation_ref": ref},
        retryable=True,
        retry_after=2,
    )


def failed_error(tool: str, ref: str, record: Mapping[str, Any]) -> OperationOutcomeError:
    saved = record.get("error")
    error: dict[str, Any] = saved if isinstance(saved, dict) else {}
    return OperationOutcomeError(
        "operation_failed",
        (
            f"{tool.rsplit(':', 1)[-1]} already failed for this operation "
            f"({error.get('code', 'error')}: {error.get('message', 'no details')}). It was not re-executed."
        ),
        required_action={"action": "correct_arguments_and_start_a_new_operation", "operation_ref": ref},
        retryable=False,
    )


def not_retained_error(tool: str, ref: str) -> OperationOutcomeError:
    return OperationOutcomeError(
        "operation_already_succeeded",
        (
            f"{tool.rsplit(':', 1)[-1]} already succeeded for this operation. Its result was too large "
            "to retain, and it was not re-executed."
        ),
        required_action={"action": "read_the_result_with_a_read_tool", "operation_ref": ref},
        retryable=False,
    )


def replay_result(record: Mapping[str, Any], ref: str) -> ToolResult:
    """The saved result of a succeeded operation, marked as a replay in ``_meta``."""
    if not record.get("result_retained"):
        raise not_retained_error(str(record.get("tool", "")), ref)
    saved = record.get("result")
    meta = {
        OPERATION_META_KEY: {
            "operation_ref": ref,
            "state": "succeeded",
            "replayed": True,
            "reconciled": bool(record.get("reconciled")),
        }
    }
    if isinstance(saved, dict):
        return ToolResult(structured_content=saved, meta=meta)
    return ToolResult(content=saved, meta=meta)


def unknown_outcome_error_from_record(record: Mapping[str, Any], ref: str) -> OperationOutcomeError:
    methods = [str(item) for item in record.get("uncertain_calls") or []] or ["(lease expired)"]
    hints = [dict(item) for item in record.get("reconcile") or [] if isinstance(item, dict)]
    return outcome_unknown_error(str(record.get("tool", "")), ref=ref, methods=methods, hints=hints)


async def resolve_claim(outcome: ClaimOutcome, *, tool: str, ref: str) -> ToolResult:
    """Turn a non-``claimed`` outcome into the saved result or an outcome error.

    ``outcome_unknown`` records first run their cheap reconciliation check;
    only a positive match upgrades them to ``succeeded``.
    """
    record = outcome.record or {}
    if outcome.status == "saved":
        return replay_result(record, ref)
    if outcome.status == "in_progress":
        raise in_progress_error(tool, ref)
    if outcome.status == "failed":
        raise failed_error(tool, ref, record)
    if outcome.status == "unknown":
        from .reconciliation import reconcile

        confirmed = await reconcile(record)
        if confirmed is not None and outcome.key is not None:
            resolved = await get_operation_store().resolve_unknown(outcome.key, confirmed)
            if resolved is not None:
                return replay_result(resolved, ref)
        raise unknown_outcome_error_from_record(record, ref)
    raise RuntimeError(f"unexpected claim outcome {outcome.status}")


# ---------------------------------------------------------------------------
# Settlement of the operation a tool-call layer claimed
# ---------------------------------------------------------------------------


def error_code_of(error: BaseException | None = None, result: Any = None) -> str | None:
    code: Any = None
    if isinstance(error, McpError):
        data = error.error.data
        code = data.get("code") if isinstance(data, dict) else None
    elif error is not None:
        code = getattr(error, "error_code", None)
    elif isinstance(result, ToolResult) and isinstance(result.structured_content, dict):
        code = result.structured_content.get("code")
    return code if isinstance(code, str) else None


async def _shielded(action: Callable[[], Awaitable[Any]]) -> Any:
    """Run a store write even while the surrounding request is being cancelled."""
    with anyio.CancelScope(shield=True):
        with anyio.move_on_after(_SETTLE_TIMEOUT_SECONDS):
            try:
                return await action()
            except Exception:  # noqa: BLE001 - never mask the call's own outcome
                LOGGER.exception("operation record update failed")
    return None


async def record_uncertain_outcome(
    tracker: MutationTracker, tool: str, calls: list[ProviderCall], *, args_digest: str | None = None
) -> str:
    """Record ``outcome_unknown`` for the operation this call belongs to.

    Marks the claimed operation (this layer's or an enclosing one's) when there
    is one; otherwise writes an evidence record for the plain call. Returns the
    public reference; idempotent per tracker chain.
    """
    if tracker.outcome_ref is not None:
        return tracker.outcome_ref
    hints = list(tracker.reconcile)
    handle = tracker.operation
    if isinstance(handle, OperationHandle):
        await _shielded(lambda: get_operation_store().mark_unknown(handle, calls, hints))
        ref = handle.ref
    else:
        enclosing = tracker.active_operation()
        if isinstance(enclosing, OperationHandle):
            # The enclosing layer (a commit) settles its own record.
            return enclosing.ref
        evidence_id = "call_" + secrets.token_urlsafe(18)
        ref = operation_ref(evidence_id)
        try:
            key = operation_key(current_principal_key(), evidence_id)
        except Exception:  # noqa: BLE001 - no principal: nothing to scope evidence to
            key = None
        if key is not None:
            await _shielded(
                lambda: get_operation_store().record_uncertain_call(
                    key, tool=tool, args_digest=args_digest, calls=calls, hints=hints
                )
            )
    LOGGER.warning(
        "outcome_unknown tool=%s ref=%s calls=%s",
        tool.rsplit(":", 1)[-1],
        ref,
        ",".join(sorted({call.method_id for call in calls})),
    )
    tracker.set_outcome_ref(ref)
    return ref


async def settle_after_failure(
    tracker: MutationTracker, error: BaseException, *, tool: str, args_digest: str | None = None
) -> OperationOutcomeError | None:
    """Settle this layer's operation after its body raised.

    Returns the ``outcome_unknown`` error to raise instead of ``error`` when a
    non-repeatable call may have reached Google; ``None`` otherwise.
    """
    tracker.close()
    if isinstance(error, OperationOutcomeError) and error.error_code == "outcome_unknown":
        handle = tracker.operation
        if isinstance(handle, OperationHandle):
            await _shielded(lambda: get_operation_store().mark_unknown(handle, [], list(tracker.reconcile)))
        return None
    uncertain = tracker.uncertain_calls(body_failed=True)
    if uncertain:
        ref = await record_uncertain_outcome(tracker, tool, uncertain, args_digest=args_digest)
        return outcome_unknown_error(
            tool, ref=ref, methods=sorted({c.method_id for c in uncertain}), calls=uncertain,
            hints=list(tracker.reconcile),
        )
    handle = tracker.operation
    if isinstance(handle, OperationHandle):
        store = get_operation_store()
        code = error_code_of(error)
        if tracker.rejected_non_repeatable() and code not in _pre_execution_codes():
            envelope = {"code": code or "provider_rejected", "message": _short(str(error))}
            await _shielded(lambda: store.fail(handle, envelope))
        else:
            await _shielded(lambda: store.release(handle))
    return None


async def settle_after_return(
    tracker: MutationTracker, result: Any, *, tool: str, args_digest: str | None = None
) -> OperationOutcomeError | None:
    """Settle this layer's operation after its body returned ``result``."""
    tracker.close()
    handle = tracker.operation
    if isinstance(result, (mcp_types.InputRequiredResult, InputRequiredToolResult)):
        if isinstance(handle, OperationHandle):
            await _shielded(lambda: get_operation_store().park(handle))
        return None
    uncertain = tracker.uncertain_calls(body_failed=False)
    if uncertain:
        # The body swallowed an ambiguous failure (e.g. returned an error
        # payload after a timeout): the change may exist, so do not report a
        # plain error the model might retry.
        ref = await record_uncertain_outcome(tracker, tool, uncertain, args_digest=args_digest)
        return outcome_unknown_error(
            tool, ref=ref, methods=sorted({c.method_id for c in uncertain}), calls=uncertain,
            hints=list(tracker.reconcile),
        )
    if not isinstance(handle, OperationHandle):
        return None
    store = get_operation_store()
    if isinstance(result, ToolResult) and result.is_error:
        code = error_code_of(result=result)
        if code == "outcome_unknown":
            await _shielded(lambda: store.mark_unknown(handle, [], list(tracker.reconcile)))
            return outcome_unknown_error(tool, ref=handle.ref, methods=[], hints=list(tracker.reconcile))
        if tracker.rejected_non_repeatable() and code not in _pre_execution_codes():
            envelope = {"code": code or "provider_rejected", "message": _short(_result_text(result))}
            await _shielded(lambda: store.fail(handle, envelope))
        else:
            await _shielded(lambda: store.release(handle))
        return None
    await _shielded(lambda: store.succeed(handle, result.structured_content if isinstance(result, ToolResult) else result))
    return None


def _pre_execution_codes() -> frozenset[str]:
    from .approvals import PRE_EXECUTION_ERROR_CODES

    return PRE_EXECUTION_ERROR_CODES


def _short(text: str) -> str:
    return text[:300]


def _result_text(result: ToolResult) -> str:
    for item in result.content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            return text
    return ""


# ---------------------------------------------------------------------------
# Middleware: turn a deadline over an uncertain mutation into outcome_unknown
# ---------------------------------------------------------------------------


class OperationOutcomeMiddleware(Middleware):
    """Opens a mutation tracker per tool call and reports uncertain outcomes.

    Install it outside ``ProductionControlMiddleware`` (whose deadline turns a
    cancelled tool body into a retryable ``deadline_exceeded``) and inside
    ``StructuredToolErrorMiddleware``. When the call failed for any reason
    while a non-repeatable Google call may have reached the provider, the
    failure becomes ``outcome_unknown`` so the model verifies instead of
    retrying blindly.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        with tracking_scope() as tracker:
            try:
                return await call_next(context)
            except OperationOutcomeError:
                raise
            except Exception as exc:
                tracker.close()
                uncertain = tracker.uncertain_calls(body_failed=True)
                if not uncertain:
                    raise
                ref = await record_uncertain_outcome(tracker, context.message.name, uncertain)
                raise outcome_unknown_error(
                    context.message.name,
                    ref=ref,
                    methods=sorted({call.method_id for call in uncertain}),
                    calls=uncertain,
                    hints=list(tracker.reconcile),
                ) from exc


__all__ = [
    "ClaimOutcome",
    "OPERATION_META_KEY",
    "OperationHandle",
    "OperationOutcomeMiddleware",
    "OperationStore",
    "TERMINAL_STATES",
    "build_operation_store",
    "current_principal_key",
    "current_tracker",
    "get_operation_store",
    "lease_seconds",
    "memory_operation_store",
    "minimize_result",
    "operation_key",
    "operation_ref",
    "outcome_unknown_error",
    "pending_ttl_seconds",
    "record_uncertain_outcome",
    "replay_result",
    "resolve_claim",
    "retention_seconds",
    "set_operation_store",
    "settle_after_failure",
    "settle_after_return",
]
