"""Repeat safety of individual Google API calls, and per-call mutation tracking.

Whether a lost response may be retried is a property of the **Google method
that ran**, not of the MCP tool that called it: ``gmail_send_email`` and
``gmail_reply_email`` both end in ``users.messages.send``; ``drive_delete_file``
is a harmless-to-repeat ``files.update`` (trash) or ``files.delete``;
``calendar_create_event`` is safe to repeat only when the caller supplied an
``idempotency_key`` (the event is then inserted under a deterministic id).
:data:`METHOD_POLICIES` is the one table that records this, keyed by the
discovery ``methodId`` (``gmail.users.messages.send``). Anything not listed
falls back by method name: ``get``/``list``/``search``/... are reads, and every
other method is treated as **non-idempotent** (fail safe).

Classes (:class:`RepeatSafety`):

``read``             no provider state change.
``idempotent``       repeating yields the same end state (label/trash/modify,
                     delete, full or partial replace).
``caller_keyed``     the provider deduplicates on a key the *caller* sends again
                     on a retry (Calendar ``events.insert`` with a body ``id``
                     derived from ``idempotency_key``; Drive ``drives.create``
                     ``requestId``). Retrying the same tool call is safe.
``transport_keyed``  the provider deduplicates on a key this server generates
                     per tool call (Chat ``spaces.messages.create`` ``requestId``):
                     HTTP-level retries inside one call are safe, but a new tool
                     call carries a new key, so the *call* is not repeat safe.
``non_idempotent``   a repeat can duplicate the effect (send, create, append,
                     batch update).

Two consequences are enforced here:

* :func:`http_retry_budget` gives googleapiclient's transport retries (5xx,
  429, socket timeouts) only to calls whose repeat is safe at that level.
  Retrying a ``users.messages.send`` after a socket timeout could send twice.
* :class:`MutationTracker` records every non-read call a tool body starts and
  how it ended. When a non-repeatable call may have reached Google and the tool
  call then fails, times out, or is cancelled, the operation layer
  (``common/operations.py``) reports ``outcome_unknown`` instead of a
  retryable error.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
import json
import threading
import uuid
from typing import Any, Final
from urllib.parse import parse_qs, urlsplit


class RepeatSafety(StrEnum):
    READ = "read"
    IDEMPOTENT = "idempotent"
    CALLER_KEYED = "caller_keyed"
    TRANSPORT_KEYED = "transport_keyed"
    NON_IDEMPOTENT = "non_idempotent"


#: Safe to repeat as a *new tool call* with the same arguments.
CALL_REPEAT_SAFE: Final[frozenset[RepeatSafety]] = frozenset(
    {RepeatSafety.READ, RepeatSafety.IDEMPOTENT, RepeatSafety.CALLER_KEYED}
)
#: Safe for googleapiclient to resend the identical HTTP request.
TRANSPORT_REPEAT_SAFE: Final[frozenset[RepeatSafety]] = CALL_REPEAT_SAFE | {
    RepeatSafety.TRANSPORT_KEYED
}


@dataclass(frozen=True, slots=True)
class MethodPolicy:
    """Repeat safety of one Google method.

    ``key`` names the request parameter that makes the call deduplicated by the
    provider (``body.id`` or a query parameter such as ``requestId``); when it
    is absent the call is classified as ``unkeyed``.
    """

    safety: RepeatSafety
    reason: str
    key: str | None = None
    unkeyed: RepeatSafety = RepeatSafety.NON_IDEMPOTENT
    #: A key this server generated per tool call (see :func:`generated_request_id`)
    #: only deduplicates transport retries, not a new tool call.
    generated: RepeatSafety | None = None


_I = RepeatSafety.IDEMPOTENT
_N = RepeatSafety.NON_IDEMPOTENT


def _idem(reason: str) -> MethodPolicy:
    return MethodPolicy(_I, reason)


def _non(reason: str) -> MethodPolicy:
    return MethodPolicy(_N, reason)


#: Every mutating Google method this server calls (plus close relatives), by
#: discovery ``methodId``. Reads are recognised by name (see ``_READ_PREFIXES``).
METHOD_POLICIES: Final[dict[str, MethodPolicy]] = {
    # Gmail
    "gmail.users.messages.send": _non("Each call sends a new email."),
    "gmail.users.drafts.send": _non("Sends the draft; the outcome of a lost response cannot be told from a retry."),
    "gmail.users.drafts.create": _non("Each call creates another draft."),
    "gmail.users.drafts.update": _idem("Replaces the draft content."),
    "gmail.users.drafts.delete": _idem("Deleting a deleted draft changes nothing."),
    "gmail.users.messages.modify": _idem("Adds/removes labels (set semantics)."),
    "gmail.users.messages.batchModify": _idem("Adds/removes labels (set semantics)."),
    "gmail.users.messages.trash": _idem("Trashing a trashed message changes nothing."),
    "gmail.users.messages.untrash": _idem("Untrashing is a state set."),
    "gmail.users.messages.delete": _idem("Deleting a deleted message changes nothing."),
    "gmail.users.messages.batchDelete": _idem("Deleting deleted messages changes nothing."),
    "gmail.users.threads.modify": _idem("Adds/removes labels (set semantics)."),
    "gmail.users.threads.trash": _idem("State set."),
    "gmail.users.threads.untrash": _idem("State set."),
    "gmail.users.threads.delete": _idem("Deleting a deleted thread changes nothing."),
    "gmail.users.labels.create": _non("Creates a label; a repeat conflicts or duplicates."),
    "gmail.users.labels.patch": _idem("Partial replace."),
    "gmail.users.labels.update": _idem("Full replace."),
    "gmail.users.labels.delete": _idem("Delete."),
    "gmail.users.settings.filters.create": _non("Each call creates another filter."),
    "gmail.users.settings.filters.delete": _idem("Delete."),
    "gmail.users.settings.forwardingAddresses.create": _non("Creates the address and sends a verification email."),
    "gmail.users.settings.forwardingAddresses.delete": _idem("Delete."),
    "gmail.users.settings.updateVacation": _idem("Full replace of the vacation settings."),
    # Calendar
    "calendar.events.insert": MethodPolicy(
        RepeatSafety.CALLER_KEYED,
        "With a client-supplied event id (idempotency_key) a repeat returns 409 and the tool "
        "returns the existing event; without one each call creates another event.",
        key="body.id",
    ),
    "calendar.events.patch": _idem("Partial replace (guest update emails may be re-sent)."),
    "calendar.events.update": _idem("Full replace (guest update emails may be re-sent)."),
    "calendar.events.delete": _idem("Delete; a repeat returns 410."),
    "calendar.events.quickAdd": _non("Each call creates another event."),
    "calendar.events.import": _non("Imports another copy unless iCalUID matches."),
    "calendar.events.move": _idem("Moves to a fixed destination."),
    # Drive
    "drive.files.create": MethodPolicy(
        RepeatSafety.CALLER_KEYED,
        "Only with an id from files.generateIds in the body; otherwise each call creates another file.",
        key="body.id",
    ),
    "drive.files.copy": MethodPolicy(
        RepeatSafety.CALLER_KEYED,
        "Only with an id from files.generateIds in the body; otherwise each call creates another copy.",
        key="body.id",
    ),
    "drive.files.update": _idem("Metadata/content replace, trash flag, parent set."),
    "drive.files.delete": _idem("Delete."),
    "drive.files.emptyTrash": _idem("State set."),
    "drive.permissions.create": _non("May send another sharing notification email and can transfer ownership."),
    "drive.permissions.update": _idem("Replace role."),
    "drive.permissions.delete": _idem("Delete."),
    "drive.drives.create": MethodPolicy(
        RepeatSafety.CALLER_KEYED,
        "requestId (required by the API) deduplicates shared-drive creation.",
        key="requestId",
    ),
    "drive.drives.update": _idem("Partial replace."),
    "drive.drives.delete": _idem("Delete."),
    "drive.drives.hide": _idem("State set."),
    "drive.drives.unhide": _idem("State set."),
    "drive.comments.create": _non("Each call adds another comment."),
    "drive.replies.create": _non("Each call adds another reply."),
    # Sheets (no request id or write control in the Sheets API)
    "sheets.spreadsheets.create": _non("Each call creates another spreadsheet."),
    "sheets.spreadsheets.batchUpdate": _non("Requests such as addSheet/insertDimension/appendCells repeat their effect."),
    "sheets.spreadsheets.values.append": _non("Each call appends more rows."),
    "sheets.spreadsheets.values.update": _idem("Writes fixed cells."),
    "sheets.spreadsheets.values.batchUpdate": _idem("Writes fixed cells."),
    "sheets.spreadsheets.values.clear": _idem("Clears fixed cells."),
    "sheets.spreadsheets.values.batchClear": _idem("Clears fixed cells."),
    # Docs / Slides / Forms: writeControl.requiredRevisionId would make a repeat
    # fail instead of applying twice; this server does not send it today.
    "docs.documents.create": _non("Each call creates another document."),
    "docs.documents.batchUpdate": MethodPolicy(
        RepeatSafety.CALLER_KEYED,
        "insertText and similar requests repeat their effect unless writeControl.requiredRevisionId "
        "pins the revision (a repeat is then rejected with 400).",
        key="body.writeControl.requiredRevisionId",
    ),
    "slides.presentations.create": _non("Each call creates another presentation."),
    "slides.presentations.batchUpdate": MethodPolicy(
        RepeatSafety.CALLER_KEYED,
        "Creates/inserts repeat their effect unless writeControl.requiredRevisionId pins the revision.",
        key="body.writeControl.requiredRevisionId",
    ),
    "forms.forms.create": _non("Each call creates another form."),
    "forms.forms.batchUpdate": MethodPolicy(
        RepeatSafety.CALLER_KEYED,
        "createItem repeats its effect unless writeControl.requiredRevisionId pins the revision.",
        key="body.writeControl.requiredRevisionId",
    ),
    "forms.forms.setPublishSettings": _idem("State set."),
    # Google Tasks
    "tasks.tasklists.insert": _non("Each call creates another task list."),
    "tasks.tasklists.patch": _idem("Partial replace."),
    "tasks.tasklists.update": _idem("Full replace."),
    "tasks.tasklists.delete": _idem("Delete."),
    "tasks.tasks.insert": _non("Each call creates another task."),
    "tasks.tasks.patch": _idem("Partial replace."),
    "tasks.tasks.update": _idem("Full replace."),
    "tasks.tasks.delete": _idem("Delete."),
    "tasks.tasks.move": _idem("Moves to a fixed position."),
    "tasks.tasks.clear": _idem("State set."),
    # People
    "people.people.createContact": _non("Each call creates another contact."),
    "people.people.batchCreateContacts": _non("Each call creates more contacts."),
    "people.people.updateContact": _idem("etag-guarded replace; a stale repeat is rejected."),
    "people.people.deleteContact": _idem("Delete."),
    "people.contactGroups.create": _non("Creates a group; a repeat conflicts or duplicates."),
    "people.contactGroups.update": _idem("Replace."),
    "people.contactGroups.delete": _idem("Delete."),
    "people.contactGroups.members.modify": _idem("Adds/removes members (set semantics)."),
    # Keep
    "keep.notes.create": _non("Each call creates another note."),
    "keep.notes.delete": _idem("Delete."),
    "keep.notes.permissions.batchCreate": _idem("Grants fixed collaborators."),
    "keep.notes.permissions.batchDelete": _idem("Removes fixed collaborators."),
    # Chat
    "chat.spaces.messages.create": MethodPolicy(
        RepeatSafety.CALLER_KEYED,
        "requestId makes creation idempotent (a repeat returns the existing message). A caller "
        "that passes request_id can retry safely; otherwise the server sends a per-call id, so "
        "HTTP retries are safe but a new tool call posts again.",
        key="requestId",
        generated=RepeatSafety.TRANSPORT_KEYED,
    ),
    "chat.spaces.messages.patch": _idem("Partial replace."),
    "chat.spaces.messages.update": _idem("Replace."),
    "chat.spaces.messages.delete": _idem("Delete."),
    "chat.spaces.create": MethodPolicy(
        RepeatSafety.TRANSPORT_KEYED, "requestId deduplicates space creation.", key="requestId"
    ),
    "chat.spaces.setup": _non("Each call sets up another space."),
    "chat.spaces.members.create": _non("Adds a member; a repeat conflicts."),
    "chat.spaces.messages.reactions.create": _non("Each call adds another reaction."),
    # Meet
    "meet.spaces.create": _non("Each call creates another meeting space."),
    "meet.spaces.patch": _idem("Partial replace."),
    "meet.spaces.endActiveConference": _idem("Ending an ended conference changes nothing."),
}

#: Prefix of the per-call idempotency keys this server generates.
GENERATED_KEY_PREFIX: Final[str] = "mcpcall-"

_READ_PREFIXES: Final[tuple[str, ...]] = (
    "get",
    "list",
    "search",
    "query",
    "batchGet",
    "export",
    "find",
)


def _last_segment(method_id: str) -> str:
    return method_id.rsplit(".", 1)[-1]


def _lookup(params: Mapping[str, Any], dotted: str) -> Any:
    value: Any = params
    for part in dotted.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value


def classify(method_id: str, params: Mapping[str, Any] | None = None) -> RepeatSafety:
    """Repeat safety of one call of ``method_id`` with discovery-style ``params``.

    ``params`` mirrors the client call (``body`` plus query parameters). Unknown
    methods are reads when their name says so, otherwise non-idempotent.
    """
    policy = METHOD_POLICIES.get(method_id)
    if policy is None:
        name = _last_segment(method_id)
        if name.startswith(_READ_PREFIXES) or name in {"getProfile", "getVacation", "getThumbnail"}:
            return RepeatSafety.READ
        return RepeatSafety.NON_IDEMPOTENT
    if policy.key is None:
        return policy.safety
    key_value = _lookup(params or {}, policy.key)
    if key_value in (None, ""):
        return policy.unkeyed
    if policy.generated is not None and str(key_value).startswith(GENERATED_KEY_PREFIX):
        return policy.generated
    return policy.safety


def generated_request_id() -> str:
    """A per-call provider idempotency key (recognisable by :func:`classify`)."""
    return f"{GENERATED_KEY_PREFIX}{uuid.uuid4()}"


def is_mutating(safety: RepeatSafety) -> bool:
    return safety is not RepeatSafety.READ


def http_retry_budget(method_id: str | None, params: Mapping[str, Any] | None, configured: int) -> int:
    """Transport retries googleapiclient may spend on this call."""
    if not method_id:
        return configured
    return configured if classify(method_id, params) in TRANSPORT_REPEAT_SAFE else 0


def http_request_params(uri: str | None, body: Any) -> dict[str, Any]:
    """Discovery-style params (query parameters + parsed JSON ``body``) of a built request."""
    params: dict[str, Any] = {}
    if uri:
        for name, values in parse_qs(urlsplit(uri).query).items():
            if values:
                params[name] = values[0]
    if isinstance(body, (bytes, str)) and body:
        try:
            parsed = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            params["body"] = parsed
    return params


# ---------------------------------------------------------------------------
# Per-call tracking
# ---------------------------------------------------------------------------

#: Only these request parameters are copied into tracking state (and from
#: there into outcome_unknown guidance): identifiers, never content.
_ID_PARAMS: Final[tuple[str, ...]] = (
    "calendarId",
    "documentId",
    "fileId",
    "formId",
    "parent",
    "presentationId",
    "spreadsheetId",
    "tasklist",
)


class LateProviderCallError(RuntimeError):
    """A mutating Google call was attempted after its tool call had ended."""

    def __init__(self, method_id: str) -> None:
        super().__init__(f"Refused to start {method_id}: the tool call already ended.")


@dataclass(slots=True)
class ProviderCall:
    method_id: str
    safety: RepeatSafety
    ids: dict[str, str] = field(default_factory=dict)

    @property
    def repeat_safe(self) -> bool:
        return self.safety in CALL_REPEAT_SAFE


class MutationTracker:
    """What non-read Google calls one tool call started and how they ended.

    One tracker exists per tool call layer (middleware and confirmation guard);
    events are recorded on the innermost tracker and every ancestor, so an
    outer layer (a commit wrapping a nested tool) sees the nested calls too.
    Provider calls run on worker threads (contextvars are copied there, the
    tracker object is shared), so updates take a lock.
    """

    def __init__(self, parent: MutationTracker | None = None) -> None:
        self.parent = parent
        self._lock = threading.Lock()
        self._next = 0
        self.in_flight: dict[int, ProviderCall] = {}
        self.applied: list[ProviderCall] = []
        self.rejected: list[ProviderCall] = []
        self.uncertain: list[ProviderCall] = []
        self.reconcile: list[dict[str, Any]] = []
        #: Operation claimed for execution by this layer (adapter or commit).
        self.operation: Any = None
        #: Public reference of the outcome_unknown record written for this call.
        self.outcome_ref: str | None = None
        self.closed = False

    def chain(self) -> Iterator[MutationTracker]:
        tracker: MutationTracker | None = self
        while tracker is not None:
            yield tracker
            tracker = tracker.parent

    def _start(self, call: ProviderCall) -> list[tuple[MutationTracker, int]]:
        tokens: list[tuple[MutationTracker, int]] = []
        for tracker in self.chain():
            with tracker._lock:
                if tracker.closed:
                    refused = True
                else:
                    refused = False
                    tracker._next += 1
                    tracker.in_flight[tracker._next] = call
                    tokens.append((tracker, tracker._next))
            if refused:
                for started, token in tokens:
                    with started._lock:
                        started.in_flight.pop(token, None)
                raise LateProviderCallError(call.method_id)
        return tokens

    def close(self) -> None:
        """The tool-call layer has ended: refuse mutating calls started later.

        A worker thread abandoned on cancellation can still be building its
        client when the call is settled; it must not start a send nobody will
        account for. Closing and starting are serialized on the lock, so a
        call either shows up in the snapshot taken after ``close`` or is
        refused.
        """
        with self._lock:
            self.closed = True

    @staticmethod
    def _finish(tokens: list[tuple[MutationTracker, int]], call: ProviderCall, outcome: str) -> None:
        for tracker, token in tokens:
            with tracker._lock:
                tracker.in_flight.pop(token, None)
                getattr(tracker, outcome).append(call)

    def note_reconciliation(self, hint: dict[str, Any]) -> None:
        for tracker in self.chain():
            with tracker._lock:
                tracker.reconcile.append(dict(hint))

    def set_outcome_ref(self, ref: str) -> None:
        for tracker in self.chain():
            if tracker.outcome_ref is None:
                tracker.outcome_ref = ref

    def active_operation(self) -> Any:
        """The nearest operation claimed by this layer or an enclosing one."""
        for tracker in self.chain():
            if tracker.operation is not None:
                return tracker.operation
        return None

    def _snapshot(self) -> tuple[list[ProviderCall], list[ProviderCall], list[ProviderCall], list[ProviderCall]]:
        with self._lock:
            return list(self.in_flight.values()), list(self.applied), list(self.rejected), list(self.uncertain)

    def uncertain_calls(self, *, body_failed: bool) -> list[ProviderCall]:
        """Non-repeatable calls that may have changed Google state unseen.

        A call still in flight (its thread was abandoned on cancellation or a
        deadline) or one that ended ambiguously is always uncertain. A call
        that completed is uncertain too when the tool body then failed: the
        change happened but the caller never received the result.
        """
        in_flight, applied, _, uncertain = self._snapshot()
        candidates = in_flight + uncertain + (applied if body_failed else [])
        return [call for call in candidates if not call.repeat_safe]

    def attempted_mutation(self) -> bool:
        in_flight, applied, rejected, uncertain = self._snapshot()
        return bool(in_flight or applied or rejected or uncertain)

    def rejected_non_repeatable(self) -> bool:
        _, _, rejected, _ = self._snapshot()
        return any(not call.repeat_safe for call in rejected)


_TRACKER: ContextVar[MutationTracker | None] = ContextVar("mcp_mutation_tracker", default=None)


def current_tracker() -> MutationTracker | None:
    return _TRACKER.get()


@contextmanager
def tracking_scope() -> Iterator[MutationTracker]:
    """Open a child tracker for one tool-call layer."""
    tracker = MutationTracker(parent=_TRACKER.get())
    token = _TRACKER.set(tracker)
    try:
        yield tracker
    finally:
        _TRACKER.reset(token)


def note_reconciliation(hint: dict[str, Any]) -> None:
    """Register a cheap verification for the mutation about to be attempted.

    Hints carry identifiers only (e.g. the RFC 822 Message-ID this server set),
    never message content.
    """
    tracker = _TRACKER.get()
    if tracker is not None:
        tracker.note_reconciliation(hint)


def _definitive_rejection(exc: BaseException) -> bool:
    """Whether Google answered and the answer means "not executed".

    An HTTP 4xx (other than 408 request timeout) is a provider decision about
    this request. 5xx, timeouts, connection resets and cancellation leave the
    outcome unknown for a non-idempotent call.
    """
    status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        code = int(status) if status is not None else None
    except (TypeError, ValueError):
        code = None
    return code is not None and 400 <= code < 500 and code != 408


@contextmanager
def track_provider_call(method_id: str, params: Mapping[str, Any] | None = None) -> Iterator[RepeatSafety]:
    """Record one Google call on the current tracker (no-op for reads/untracked)."""
    safety = classify(method_id, params)
    tracker = _TRACKER.get()
    if tracker is None or not is_mutating(safety):
        yield safety
        return
    ids = {
        name: str(value)
        for name in _ID_PARAMS
        if isinstance(value := (params or {}).get(name), (str, int)) and value != ""
    }
    call = ProviderCall(method_id, safety, ids)
    tokens = tracker._start(call)
    try:
        yield safety
    except BaseException as exc:
        MutationTracker._finish(tokens, call, "rejected" if _definitive_rejection(exc) else "uncertain")
        raise
    MutationTracker._finish(tokens, call, "applied")


__all__ = [
    "CALL_REPEAT_SAFE",
    "LateProviderCallError",
    "METHOD_POLICIES",
    "MethodPolicy",
    "MutationTracker",
    "ProviderCall",
    "RepeatSafety",
    "TRANSPORT_REPEAT_SAFE",
    "classify",
    "generated_request_id",
    "current_tracker",
    "http_request_params",
    "http_retry_budget",
    "is_mutating",
    "note_reconciliation",
    "track_provider_call",
    "tracking_scope",
]
