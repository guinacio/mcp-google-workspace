"""One consent model for every tool call: the consequential-call table.

Every published tool is classified here, from its name and its validated
arguments, into one of three risk classes:

``read``
    Changes nothing.
``write``
    A reversible change: a draft, a label, a document edit, a move to Trash,
    a few messages relabelled.
``consequential``
    Sends mail, invitations or chat messages; shares or unshares; deletes
    permanently; changes forwarding, filters that forward or trash, or the
    auto-reply; disconnects the account; or changes ``BULK_THRESHOLD`` or
    more items in one call.

A consequential call runs only after consent, whatever its arguments say: no
tool argument can skip it. The confirmation guard around every async tool
body (``common/confirmation.py``) calls :func:`enforce_consent` before the
body runs, on every execution path (direct call, the ``call_tool`` search
proxy, ``commit_workspace_action``, background tasks). Consent comes from:

1. **Elicitation**, when the client declared the capability: the user sees
   the preview text and accepts or declines (multi round-trip on MCP
   2026-07-28 and in tasks, ``ctx.elicit`` on handshake-era sessions).
2. **Prepare/commit** otherwise: the call fails with
   ``confirmation_required`` whose ``required_action`` names
   ``prepare_workspace_action``; prepare returns the same preview text and a
   one-time token, and ``commit_workspace_action`` executes exactly the
   prepared call. The host's own permission prompt on
   ``commit_workspace_action`` is the human gate. A client that can elicit is
   still asked when it commits, so the preview is never the only check for it.

``MCP_CONSENT`` selects the policy: ``required`` (default, as above),
``preview_only`` (never elicit; every consequential call goes through
prepare/commit) or ``off`` (consequential calls run directly; logged as a
warning at startup).

The same table derives each tool's ``readOnlyHint``, ``destructiveHint`` and
``idempotentHint`` (``common/component_annotations.py``), the tool names
``prepare_workspace_action`` accepts, and the contract tests.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hmac
import logging
import os
from typing import TYPE_CHECKING, Any, Final, Literal, cast

from fastmcp.server.middleware import Middleware

if TYPE_CHECKING:  # pragma: no cover
    from fastmcp import Context

LOGGER = logging.getLogger("mcp_google_workspace.consent")

RiskClass = Literal["read", "write", "consequential"]
ConsentMode = Literal["required", "preview_only", "off"]
Arguments = Mapping[str, Any]
Renderer = Callable[[Arguments, str | None], Awaitable[str]]

CONSENT_ENV: Final[str] = "MCP_CONSENT"
CONSENT_MODES: Final[tuple[ConsentMode, ...]] = ("required", "preview_only", "off")
RECIPIENT_WARNING_ENV: Final[str] = "MCP_EMAIL_PREPARE_RECIPIENTS"

#: Items changed in one call at or above which a reversible bulk change is
#: consequential (messages modified or trashed, raw batchUpdate requests).
BULK_THRESHOLD: Final[int] = 10

_LIST_LIMIT = 5
_TEXT_LIMIT = 300


def consent_mode(environ: Mapping[str, str] | None = None) -> ConsentMode:
    """The active consent policy from ``MCP_CONSENT`` (default ``required``)."""
    env = os.environ if environ is None else environ
    value = env.get(CONSENT_ENV, "required").strip().lower() or "required"
    if value not in CONSENT_MODES:
        raise ValueError(f"{CONSENT_ENV} must be one of {', '.join(CONSENT_MODES)}; got {value!r}.")
    return cast(ConsentMode, value)


def log_consent_mode() -> ConsentMode:
    """Validate ``MCP_CONSENT`` and make a disabled consent gate visible."""
    mode = consent_mode()
    if mode == "off":
        LOGGER.warning(
            "MCP_CONSENT=off: consent is DISABLED. Consequential calls (sending mail, sharing, "
            "permanent deletion, forwarding, bulk changes) run without asking anyone."
        )
    elif mode == "preview_only":
        LOGGER.info("MCP_CONSENT=preview_only: consequential calls need prepare/commit; no elicitation.")
    return mode


def large_send_threshold() -> int:
    """Recipient count from which a send preview flags a large send."""
    raw = os.getenv(RECIPIENT_WARNING_ENV, "").strip()
    try:
        return max(1, int(raw)) if raw else 10
    except ValueError:
        return 10


# ---------------------------------------------------------------------------
# Table model
# ---------------------------------------------------------------------------


def _always(_arguments: Arguments) -> bool:
    return True


@dataclass(frozen=True)
class Trigger:
    """One condition under which a call of a tool is consequential."""

    #: Confirmation action name, shown to the user and bound in continuations.
    action: str
    when: Callable[[Arguments], bool]
    #: The predicate in words (docs, contract tests).
    condition: str
    #: Ask with an explicit ``confirm`` checkbox instead of a yes/no value.
    explicit_confirm: bool = False


@dataclass(frozen=True)
class ToolPolicy:
    """How one tool is classified and previewed."""

    #: Class of a call no trigger matches: ``read`` or ``write``.
    base: Literal["read", "write"]
    #: MCP ``idempotentHint`` for the tool's non-read calls.
    idempotent: bool = True
    triggers: tuple[Trigger, ...] = ()
    preview: Renderer | None = None
    #: The tool takes ``dry_run`` and the gate answers it with the preview.
    dry_run: bool = False
    #: ``destructiveHint`` override; derived from ``triggers`` when None.
    destructive: bool | None = None

    def trigger_for(self, arguments: Arguments) -> Trigger | None:
        for trigger in self.triggers:
            if trigger.when(arguments):
                return trigger
        return None

    @property
    def may_be_consequential(self) -> bool:
        return bool(self.triggers)


@dataclass(frozen=True)
class Classification:
    risk: RiskClass
    trigger: Trigger | None


def classify(tool: str, arguments: Arguments) -> Classification:
    """Risk class of one call (unknown tools are writes, never reads)."""
    policy = CONSENT_TABLE.get(tool)
    if policy is None:
        return Classification("write", None)
    trigger = policy.trigger_for(arguments)
    if trigger is not None:
        return Classification("consequential", trigger)
    return Classification(policy.base, None)


# ---------------------------------------------------------------------------
# Predicate helpers
# ---------------------------------------------------------------------------


def _unique(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(value) for value in values if value))


def _sub(arguments: Arguments, key: str = "request") -> Mapping[str, Any]:
    value = arguments.get(key)
    return value if isinstance(value, Mapping) else {}


def _is(arguments: Arguments, key: str, value: Any) -> bool:
    return arguments.get(key) == value


def _notifies(arguments: Arguments) -> bool:
    return arguments.get("send_updates") in ("all", "externalOnly")


def _bulk_messages(arguments: Arguments) -> bool:
    return len(_unique(arguments.get("message_ids"))) >= BULK_THRESHOLD


def _raw_batch_is_consequential(arguments: Arguments) -> bool:
    requests = arguments.get("requests")
    if not isinstance(requests, list):
        return False
    if len(requests) >= BULK_THRESHOLD:
        return True
    return any(
        str(kind).startswith("delete")
        for request in requests
        if isinstance(request, Mapping)
        for kind in request
    )


def _filter_forwards_or_trashes(arguments: Arguments) -> bool:
    if arguments.get("action") != "create":
        return False
    action = _sub(arguments, "filter_action")
    labels = {str(label).upper() for label in action.get("add_labels") or []}
    return bool(action.get("forward")) or "TRASH" in labels


def _keep_replaces_and_deletes(arguments: Arguments) -> bool:
    request = _sub(arguments)
    return bool(request.get("apply_via_replacement")) and bool(request.get("delete_original_on_apply"))


def _meet_opens_space(arguments: Arguments) -> bool:
    config = _sub(arguments, "config")
    return str(config.get("accessType") or config.get("access_type") or "").upper() == "OPEN"


# ---------------------------------------------------------------------------
# Preview text
# ---------------------------------------------------------------------------


def _listing(values: Sequence[str], *, empty: str = "(none)") -> str:
    if not values:
        return empty
    shown = ", ".join(values[:_LIST_LIMIT])
    rest = len(values) - _LIST_LIMIT
    return f"{shown} and {rest} more" if rest > 0 else shown


def _addresses(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values or []:
        address = str(value).strip()
        if address and address.casefold() not in seen:
            seen.add(address.casefold())
            result.append(address)
    return result


def _excerpt(text: Any) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= _TEXT_LIMIT:
        return value
    return f"{value[:_TEXT_LIMIT]}... ({len(value)} characters)"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _guest_notice(arguments: Arguments) -> str:
    return {
        "all": "Google emails every guest.",
        "externalOnly": "Google emails guests who do not use Google Calendar.",
        "none": "Google emails no one.",
    }.get(str(arguments.get("send_updates")), "Google emails no one (send_updates not set).")


def _body_line(arguments: Arguments) -> str:
    if arguments.get("text_body"):
        return f"Text: {_excerpt(arguments.get('text_body'))}"
    if arguments.get("html_body"):
        return f"HTML body: {len(str(arguments.get('html_body')))} characters"
    return "Body: (empty)"


def _recipient_lines(to: Sequence[str], cc: Sequence[str], bcc: Sequence[str]) -> list[str]:
    total = len(_addresses([*to, *cc, *bcc]))
    lines = [
        f"To: {_listing(to)}",
        f"Cc: {_listing(cc)}",
        f"Bcc: {_listing(bcc)}",
        f"Recipients: {total} in total",
    ]
    if total >= large_send_threshold():
        lines.append(f"This is a large send ({total} recipients).")
    return lines


async def _send_email_preview(arguments: Arguments, _action: str | None) -> str:
    from ..gmail.helpers import recipient_set

    # Validates every address before anyone is asked.
    recipients = recipient_set(to=arguments.get("to"), cc=arguments.get("cc"), bcc=arguments.get("bcc"))
    lines = ["Send this email?"]
    lines += _recipient_lines(
        _addresses(recipients.to), _addresses(recipients.cc), _addresses(recipients.bcc)
    )
    lines += [
        f"Subject: {arguments.get('subject')}",
        _body_line(arguments),
        f"Attachments: {len(arguments.get('attachments') or [])}",
    ]
    return "\n".join(lines)


async def _reply_preview(arguments: Arguments, _action: str | None) -> str:
    from ..gmail.tools.messages import load_reply_context

    reply_all = bool(arguments.get("reply_all"))
    context = await load_reply_context(str(arguments.get("message_id")), reply_all=reply_all)
    lines = [f"{'Reply all' if reply_all else 'Reply'} to this email?"]
    lines += _recipient_lines(context.to, context.cc, [])
    lines += [
        f"Subject: {context.subject}",
        _body_line(arguments),
        f"Attachments: {len(arguments.get('attachments') or [])}",
    ]
    return "\n".join(lines)


async def _send_draft_preview(arguments: Arguments, _action: str | None) -> str:
    from ..gmail.tools.drafts import load_draft_headers

    headers = await load_draft_headers(str(arguments.get("draft_id")))
    lines = [f"Send Gmail draft {arguments.get('draft_id')} as saved?"]
    lines += _recipient_lines(headers["to"], headers["cc"], headers["bcc"])
    lines.append(f"Subject: {headers['subject']}")
    return "\n".join(lines)


async def _gmail_delete_preview(arguments: Arguments, _action: str | None) -> str:
    ids = _unique(arguments.get("message_ids"))
    permanent = arguments.get("mode") == "permanent"
    if arguments.get("thread_id") and not ids:
        target = f"every message in Gmail thread {arguments.get('thread_id')}"
    else:
        target = f"{_plural(len(ids), 'Gmail message')} ({_listing(ids)})"
    if permanent:
        return f"Permanently delete {target}? This cannot be undone."
    return f"Move {target} to Trash? They can be restored from Trash for 30 days."


async def _gmail_modify_preview(arguments: Arguments, _action: str | None) -> str:
    ids = _unique(arguments.get("message_ids"))
    if arguments.get("thread_id") and not ids:
        target = f"every message in Gmail thread {arguments.get('thread_id')}"
    else:
        target = f"{_plural(len(ids), 'Gmail message')} ({_listing(ids)})"
    changes: list[str] = []
    if arguments.get("untrash"):
        changes.append("restore from Trash")
    if arguments.get("add_labels"):
        changes.append(f"add labels {_listing(_unique(arguments.get('add_labels')))}")
    if arguments.get("remove_labels"):
        changes.append(f"remove labels {_listing(_unique(arguments.get('remove_labels')))}")
    for key, when_true, when_false in (
        ("mark_read", "mark read", "mark unread"),
        ("archive", "archive", "move to the inbox"),
        ("spam", "report as spam", "take out of Spam"),
    ):
        if arguments.get(key) is True:
            changes.append(when_true)
        elif arguments.get(key) is False:
            changes.append(when_false)
    return f"Change {target}: {'; '.join(changes) or 'no change given'}?"


def _static(template: str) -> Renderer:
    async def render(arguments: Arguments, _action: str | None) -> str:
        flat = {**arguments, **_sub(arguments)}
        return template.format_map(_Defaulting(flat))

    return render


class _Defaulting(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "(not set)"


async def _manage_filters_preview(arguments: Arguments, _action: str | None) -> str:
    if arguments.get("action") == "delete":
        return f"Delete Gmail filter {arguments.get('filter_id')}?"
    criteria = {key: value for key, value in _sub(arguments, "criteria").items() if value not in (None, "", [], False)}
    action = _sub(arguments, "filter_action")
    effects: list[str] = []
    if action.get("forward"):
        effects.append(f"forward it to {action.get('forward')}")
    labels = _unique(action.get("add_labels"))
    if labels:
        effects.append(f"add labels {_listing(labels)}")
    removed = _unique(action.get("remove_labels"))
    if removed:
        effects.append(f"remove labels {_listing(removed)}")
    matched = ", ".join(f"{key}={value}" for key, value in sorted(criteria.items())) or "(no criteria)"
    return (
        "Create a Gmail filter for incoming mail that will "
        f"{' and '.join(effects) or 'change it'}? Matches: {matched}."
    )


async def _manage_forwarding_preview(arguments: Arguments, _action: str | None) -> str:
    email = arguments.get("forwarding_email")
    if arguments.get("action") == "create":
        return f"Add {email} as a Gmail forwarding address? Gmail may email it a verification link."
    return f"Delete Gmail forwarding address {email}?"


def _epoch_ms(value: Any) -> str:
    if not isinstance(value, int):
        return "(not set)"
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat(timespec="minutes")


async def _vacation_preview(arguments: Arguments, _action: str | None) -> str:
    audience = "every sender"
    if arguments.get("restrict_to_contacts"):
        audience = "senders in your contacts"
    if arguments.get("restrict_to_domain"):
        audience = "senders in your domain" if audience == "every sender" else f"{audience} in your domain"
    return "\n".join(
        [
            "Turn on the Gmail auto-reply? Gmail will answer incoming mail automatically.",
            f"Replies go to: {audience}",
            f"Subject: {arguments.get('response_subject') or '(none)'}",
            f"Text: {_excerpt(arguments.get('response_body_plain_text') or arguments.get('response_body_html')) or '(none)'}",
            f"From: {_epoch_ms(arguments.get('start_time'))}; until: {_epoch_ms(arguments.get('end_time'))}",
        ]
    )


def _guests(arguments: Arguments) -> list[str]:
    guests = arguments.get("attendees") or []
    return _addresses(guest.get("email") for guest in guests if isinstance(guest, Mapping))


async def _create_event_preview(arguments: Arguments, _action: str | None) -> str:
    guests = _guests(arguments)
    return "\n".join(
        [
            f"Create event '{arguments.get('summary')}' on calendar {arguments.get('calendar_id') or 'primary'} "
            f"with {_plural(len(guests), 'guest')}?",
            f"When: {arguments.get('start_datetime')} to {arguments.get('end_datetime')}",
            f"Guests: {_listing(guests)}",
            f"{_guest_notice(arguments)} The event also appears on the guests' Google calendars.",
        ]
    )


_EVENT_FIELDS = (
    ("summary", "title"),
    ("start_datetime", "start"),
    ("end_datetime", "end"),
    ("location", "location"),
    ("description", "description"),
    ("recurrence", "recurrence (every occurrence of the series)"),
    ("attachments", "attachments"),
    ("reminder_minutes", "reminders"),
    ("visibility", "visibility"),
    ("transparency", "busy/free"),
    ("add_meet_link", "Meet link"),
)


async def _update_event_preview(arguments: Arguments, _action: str | None) -> str:
    changed = [label for key, label in _EVENT_FIELDS if arguments.get(key) not in (None, False)]
    lines = [f"Update event {arguments.get('event_id')} on calendar {arguments.get('calendar_id') or 'primary'}?"]
    if arguments.get("attendees") is not None:
        guests = _guests(arguments)
        lines.append(f"Guest list replaced by {_plural(len(guests), 'guest')}: {_listing(guests)}")
    lines.append(f"Changes: {', '.join(changed) or '(guests only)'}")
    lines.append(_guest_notice(arguments))
    return "\n".join(lines)


async def _delete_event_preview(arguments: Arguments, _action: str | None) -> str:
    return (
        f"Delete event {arguments.get('event_id')} from calendar {arguments.get('calendar_id') or 'primary'}? "
        f"{_guest_notice(arguments)}"
    )


async def _event_attachments_preview(arguments: Arguments, _action: str | None) -> str:
    removed = [
        str(item.get("file_id") or item.get("file_url"))
        for item in arguments.get("remove") or []
        if isinstance(item, Mapping)
    ]
    added = [
        str(item.get("title") or item.get("file_url"))
        for item in arguments.get("add") or []
        if isinstance(item, Mapping)
    ]
    def part(verb: str, items: list[str]) -> str:
        return f"{verb} {len(items)}" + (f" ({_listing(items)})" if items else "")

    return (
        f"Change the attachments of event {arguments.get('event_id')}: {part('remove', removed)}, "
        f"{part('add', added)}? The files stay in Drive. {_guest_notice(arguments)}"
    )


async def _share_file_preview(arguments: Arguments, action: str | None) -> str:
    file_id = arguments.get("file_id")
    owner = bool(arguments.get("transfer_ownership")) or arguments.get("role") == "owner"
    if arguments.get("permission_id"):
        changes = []
        if arguments.get("role"):
            changes.append(f"role {arguments.get('role')}")
        if arguments.get("expiration_time"):
            changes.append(f"expires {arguments.get('expiration_time')}")
        if arguments.get("remove_expiration"):
            changes.append("no expiration")
        if arguments.get("allow_file_discovery") is not None:
            changes.append(f"discoverable {arguments.get('allow_file_discovery')}")
        text = (
            f"Change permission {arguments.get('permission_id')} on Drive file {file_id}: "
            f"{', '.join(changes) or 'no change given'}?"
        )
        return text + (" This transfers ownership of the file." if owner else "")
    kind = arguments.get("type")
    grantee = {
        "anyone": "anyone with the link (no sign-in needed)",
        "domain": f"everyone in the domain {arguments.get('domain') or '(not set)'}",
        None: "(type not set)",
    }.get(kind, f"{kind} {arguments.get('email_address') or '(no address)'}")
    lines = [f"Share Drive file {file_id} with {grantee} as {arguments.get('role') or '(no role)'}?"]
    if owner:
        lines.append("This transfers ownership of the file.")
    if arguments.get("expiration_time"):
        lines.append(f"Access expires {arguments.get('expiration_time')}.")
    if arguments.get("allow_file_discovery") is True:
        lines.append("The file can be found by search.")
    if kind in ("user", "group"):
        silent = arguments.get("send_notification_email") is False
        lines.append("No notification email." if silent else "Google emails the grantee.")
        if arguments.get("email_message") and not silent:
            lines.append(f"Message: {_excerpt(arguments.get('email_message'))}")
    return " ".join(lines)


def _request_kinds(arguments: Arguments) -> str:
    counts: dict[str, int] = {}
    for request in arguments.get("requests") or []:
        if isinstance(request, Mapping):
            for kind in request:
                counts[str(kind)] = counts.get(str(kind), 0) + 1
    return ", ".join(f"{count} {kind}" for kind, count in sorted(counts.items())) or "none"


def _batch_preview(service: str, id_key: str) -> Renderer:
    async def render(arguments: Arguments, _action: str | None) -> str:
        requests = arguments.get("requests") or []
        return (
            f"Apply {_plural(len(requests), 'raw ' + service + ' batchUpdate request')} to "
            f"{arguments.get(id_key)}: {_request_kinds(arguments)}?"
        )

    return render


async def _delete_task_preview(arguments: Arguments, _action: str | None) -> str:
    tasklist = arguments.get("tasklist_id") or "the default list"
    return f"Delete task {arguments.get('task_id')} from task list {tasklist}?"


async def _chat_post_preview(arguments: Arguments, action: str | None) -> str:
    request = _sub(arguments)
    if action == "reply_to_message":
        where = f"in the thread of Chat message {request.get('message_name')}"
    else:
        where = f"to Chat space {request.get('space_name')}"
        if request.get("thread_key"):
            where += f" (thread {request.get('thread_key')})"
        if request.get("private_message_viewer"):
            where += f", visible only to {request.get('private_message_viewer')}"
    return f"Post this message {where}?\nText: {_excerpt(request.get('text'))}"


async def _keep_create_preview(arguments: Arguments, _action: str | None) -> str:
    request = _sub(arguments)
    people = _addresses(request.get("collaborator_emails"))
    return (
        f"Create Keep note '{request.get('title') or '(untitled)'}' and give "
        f"{_plural(len(people), 'person')} writer access: {_listing(people)}?"
    )


async def _keep_share_preview(arguments: Arguments, _action: str | None) -> str:
    request = _sub(arguments)
    people = _addresses(request.get("collaborator_emails"))
    return f"Give {_plural(len(people), 'person')} writer access to Keep note {request.get('note_name')}: {_listing(people)}?"


async def _keep_unshare_preview(arguments: Arguments, _action: str | None) -> str:
    request = _sub(arguments)
    names = _unique(request.get("permission_names"))
    return f"Remove {_plural(len(names), 'collaborator permission')} from Keep note {request.get('note_name')}: {_listing(names)}?"


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

READ: Final = ToolPolicy("read")


def _write(*, idempotent: bool = False) -> ToolPolicy:
    return ToolPolicy("write", idempotent=idempotent)


def _always_ask(action: str, preview: Renderer, *, idempotent: bool, explicit: bool = False) -> ToolPolicy:
    return ToolPolicy(
        "write",
        idempotent=idempotent,
        triggers=(Trigger(action, _always, "always", explicit_confirm=explicit),),
        preview=preview,
    )


_BATCH_TRIGGER_TEXT = f"{BULK_THRESHOLD} or more requests, or any delete* request"

CONSENT_TABLE: Final[dict[str, ToolPolicy]] = {
    # --- root ---------------------------------------------------------------
    "commit_workspace_action": ToolPolicy("write", idempotent=True, destructive=True),
    "connect_google_workspace": _write(),
    "disconnect_google_workspace": _always_ask(
        "disconnect",
        _static(
            "Disconnect this Google account? The grant is revoked at Google and the stored credentials "
            "are deleted; reconnecting needs Google consent again."
        ),
        idempotent=True,
    ),
    "get_google_connection_status": READ,
    "get_mcp_apps_diagnostics": READ,
    "prepare_workspace_action": READ,
    "resolve_workspace_resource": READ,
    "search_workspace": READ,
    # --- uploads (picker staging area; app callbacks) ------------------------
    "uploads_delete_file": _write(idempotent=True),
    "uploads_file_manager": READ,
    "uploads_list_files": READ,
    "uploads_read_file": READ,
    "uploads_store_files": _write(),
    # --- gmail ----------------------------------------------------------------
    "gmail_check_mail_updates": READ,
    "gmail_delete": ToolPolicy(
        "write",
        idempotent=True,
        triggers=(
            Trigger(
                "delete_thread",
                lambda a: _is(a, "mode", "permanent") and bool(a.get("thread_id")) and not _unique(a.get("message_ids")),
                "mode 'permanent' with thread_id",
            ),
            Trigger(
                "delete_email",
                lambda a: _is(a, "mode", "permanent") and len(_unique(a.get("message_ids"))) <= 1,
                "mode 'permanent' with one message",
            ),
            Trigger("batch_delete", lambda a: _is(a, "mode", "permanent"), "mode 'permanent' with several messages"),
            Trigger("batch_trash", _bulk_messages, f"mode 'trash' with {BULK_THRESHOLD} or more distinct message_ids"),
        ),
        preview=_gmail_delete_preview,
        dry_run=True,
    ),
    "gmail_delete_draft": _always_ask(
        "delete_draft", _static("Permanently delete Gmail draft {draft_id}? It does not go to Trash."), idempotent=True
    ),
    "gmail_download_attachment": _write(),
    "gmail_get_mail_digest": READ,
    "gmail_get_thread": READ,
    "gmail_list_drafts": READ,
    "gmail_list_labels": READ,
    "gmail_manage_filters": ToolPolicy(
        "write",
        idempotent=False,
        triggers=(
            Trigger("delete_filter", lambda a: _is(a, "action", "delete"), "action 'delete'"),
            Trigger(
                "create_filter",
                _filter_forwards_or_trashes,
                "action 'create' with filter_action.forward or a TRASH label",
            ),
        ),
        preview=_manage_filters_preview,
    ),
    "gmail_manage_forwarding": ToolPolicy(
        "write",
        idempotent=False,
        triggers=(
            Trigger("create_forwarding_address", lambda a: _is(a, "action", "create"), "action 'create'"),
            Trigger("delete_forwarding_address", lambda a: _is(a, "action", "delete"), "action 'delete'"),
        ),
        preview=_manage_forwarding_preview,
    ),
    "gmail_manage_labels": ToolPolicy(
        "write",
        idempotent=False,
        triggers=(Trigger("delete_label", lambda a: _is(a, "action", "delete"), "action 'delete'"),),
        preview=_static("Delete Gmail label {label}? Messages keep their other labels."),
    ),
    "gmail_manage_vacation": ToolPolicy(
        "write",
        idempotent=True,
        triggers=(
            Trigger(
                "update_vacation",
                lambda a: _is(a, "action", "update") and a.get("enable_auto_reply") is True,
                "action 'update' with enable_auto_reply true",
            ),
        ),
        preview=_vacation_preview,
    ),
    "gmail_modify_messages": ToolPolicy(
        "write",
        idempotent=True,
        triggers=(
            Trigger("modify_messages", _bulk_messages, f"{BULK_THRESHOLD} or more distinct message_ids"),
        ),
        preview=_gmail_modify_preview,
        dry_run=True,
    ),
    "gmail_read_emails": READ,
    "gmail_reply": ToolPolicy(
        "write",
        idempotent=False,
        triggers=(
            Trigger("reply_all_email", lambda a: a.get("reply_all") is True, "reply_all true", explicit_confirm=True),
            Trigger("reply_email", _always, "always (reply_all false)", explicit_confirm=True),
        ),
        preview=_reply_preview,
    ),
    "gmail_save_draft": _write(),
    "gmail_search_emails": READ,
    "gmail_send_draft": _always_ask("send_draft", _send_draft_preview, idempotent=False, explicit=True),
    "gmail_send_email": _always_ask("send_email", _send_email_preview, idempotent=False, explicit=True),
    # --- calendar -------------------------------------------------------------
    "calendar_create_event": ToolPolicy(
        "write",
        idempotent=False,
        triggers=(Trigger("create_event", lambda a: bool(_guests(a)), "attendees not empty"),),
        preview=_create_event_preview,
    ),
    "calendar_delete_event": _always_ask("delete_event", _delete_event_preview, idempotent=True),
    "calendar_find_free_time": READ,
    "calendar_get_calendar_context": READ,
    "calendar_get_calendar_digest": READ,
    "calendar_read_events": READ,
    "calendar_respond_to_event": _write(idempotent=True),
    "calendar_search_events": READ,
    "calendar_set_event_attachments": ToolPolicy(
        "write",
        idempotent=True,
        triggers=(
            Trigger("remove_event_attachment", lambda a: bool(a.get("remove")), "remove not empty"),
            Trigger("update_event_attachments", _notifies, "send_updates 'all' or 'externalOnly'"),
        ),
        preview=_event_attachments_preview,
    ),
    "calendar_update_event": ToolPolicy(
        "write",
        idempotent=True,
        triggers=(
            Trigger(
                "update_event",
                lambda a: a.get("attendees") is not None or a.get("recurrence") is not None or _notifies(a),
                "attendees or recurrence given, or send_updates 'all' or 'externalOnly'",
            ),
        ),
        preview=_update_event_preview,
    ),
    # --- drive ----------------------------------------------------------------
    "drive_copy_file": _write(),
    "drive_create_folder": _write(),
    "drive_delete_file": ToolPolicy(
        "write",
        idempotent=True,
        triggers=(Trigger("delete_file", lambda a: _is(a, "delete_mode", "permanent"), "delete_mode 'permanent'"),),
        preview=_static("Permanently delete Drive file {file_id}? It skips the trash and cannot be undone."),
    ),
    "drive_download_file": _write(),
    "drive_get_file": READ,
    "drive_list_drives": READ,
    "drive_list_files": READ,
    "drive_list_permissions": READ,
    "drive_move_file": _write(),
    "drive_read_file": READ,
    "drive_share_file": ToolPolicy(
        "write",
        idempotent=False,
        triggers=(
            Trigger("update_permission", lambda a: bool(a.get("permission_id")), "permission_id given"),
            Trigger("create_permission", _always, "always (a new grant, any type)"),
        ),
        preview=_share_file_preview,
    ),
    "drive_unshare_file": _always_ask(
        "delete_permission", _static("Remove permission {permission_id} from Drive file {file_id}?"), idempotent=True
    ),
    "drive_update_drive": _write(idempotent=True),
    "drive_update_file_metadata": _write(idempotent=True),
    "drive_upload_file": _write(),
    # --- docs, sheets, slides, forms -----------------------------------------
    "docs_append_document_text": _write(),
    "docs_batch_update_document": ToolPolicy(
        "write",
        idempotent=False,
        triggers=(Trigger("batch_update_document", _raw_batch_is_consequential, _BATCH_TRIGGER_TEXT),),
        preview=_batch_preview("Docs", "document_id"),
        dry_run=True,
    ),
    "docs_create_document": _write(),
    "docs_read_document": READ,
    "docs_replace_document_text": _write(idempotent=True),
    "forms_batch_update_form": ToolPolicy(
        "write",
        idempotent=False,
        triggers=(Trigger("batch_update_form", _raw_batch_is_consequential, _BATCH_TRIGGER_TEXT),),
        preview=_batch_preview("Forms", "form_id"),
        dry_run=True,
    ),
    "forms_create_form": _write(),
    "forms_get_form": READ,
    "forms_list_form_responses": READ,
    "forms_set_form_publish_settings": _write(idempotent=True),
    "sheets_append_sheet_values": _write(),
    "sheets_batch_update_spreadsheet": ToolPolicy(
        "write",
        idempotent=False,
        triggers=(Trigger("batch_update_spreadsheet", _raw_batch_is_consequential, _BATCH_TRIGGER_TEXT),),
        preview=_batch_preview("Sheets", "spreadsheet_id"),
        dry_run=True,
    ),
    "sheets_create_spreadsheet": _write(),
    "sheets_get_spreadsheet": READ,
    "sheets_read_sheet_values": READ,
    "sheets_update_sheet_values": _write(idempotent=True),
    "slides_batch_update_presentation": ToolPolicy(
        "write",
        idempotent=False,
        triggers=(Trigger("batch_update_presentation", _raw_batch_is_consequential, _BATCH_TRIGGER_TEXT),),
        preview=_batch_preview("Slides", "presentation_id"),
        dry_run=True,
    ),
    "slides_create_presentation": _write(),
    "slides_get_slide_thumbnail": READ,
    "slides_read_presentation": READ,
    "slides_replace_text_in_presentation": _write(idempotent=True),
    # --- tasks, people --------------------------------------------------------
    "tasks_create_task": _write(),
    "tasks_create_tasklist": _write(),
    "tasks_delete_task": _always_ask("delete_task", _delete_task_preview, idempotent=True),
    "tasks_list_tasklists": READ,
    "tasks_list_tasks": READ,
    "tasks_tasks_digest": READ,
    "tasks_update_task": ToolPolicy(
        "write",
        idempotent=True,
        triggers=(Trigger("delete_task", lambda a: a.get("deleted") is True, "deleted true"),),
        preview=_delete_task_preview,
    ),
    "people_create_contact": _write(),
    "people_delete_contact": _always_ask(
        "delete_contact", _static("Permanently delete contact {person_name}?"), idempotent=True
    ),
    "people_get_contact": READ,
    "people_manage_contact_groups": _write(),
    "people_search_contacts": READ,
    "people_update_contact": _write(idempotent=True),
    # --- apps dashboard (optional) ------------------------------------------
    "apps_get_dashboard": READ,
    "apps_get_email_attachment": READ,
    "apps_get_email_detail": READ,
    "apps_get_event_detail": READ,
    "apps_get_state": READ,
    "apps_get_weekly_calendar_view": READ,
    "apps_next_range": _write(),
    "apps_patch_state": _write(idempotent=True),
    "apps_prev_range": _write(),
    "apps_set_state": _write(idempotent=True),
    "apps_today": _write(idempotent=True),
    # --- chat (optional) ------------------------------------------------------
    "chat_create_message": _always_ask("create_message", _chat_post_preview, idempotent=False),
    "chat_delete_message": _always_ask(
        "delete_message", _static("Delete Chat message {message_name}? This cannot be undone."), idempotent=True
    ),
    "chat_find_direct_message": READ,
    "chat_get_message": READ,
    "chat_get_space": READ,
    "chat_list_messages": READ,
    "chat_list_space_members": READ,
    "chat_list_spaces": READ,
    "chat_post_message_simple": _always_ask("post_message_simple", _chat_post_preview, idempotent=False),
    "chat_reply_to_message": _always_ask("reply_to_message", _chat_post_preview, idempotent=False),
    "chat_update_message": _write(idempotent=True),
    # --- gemini (optional; generated files are written locally) ---------------
    "gemini_analyze_audio": _write(),
    "gemini_describe_video": _write(),
    "gemini_edit_image": _write(),
    "gemini_generate_image": _write(),
    # --- keep (optional) ------------------------------------------------------
    # Tools that only report that Keep API v1 cannot do something change nothing.
    "keep_add_checklist_item": READ,
    "keep_append_note_content": ToolPolicy(
        "write",
        idempotent=False,
        triggers=(
            Trigger(
                "delete_note",
                _keep_replaces_and_deletes,
                "apply_via_replacement and delete_original_on_apply true",
            ),
        ),
        preview=_static("Replace Keep note {note_name} with a new note and permanently delete the original?"),
    ),
    "keep_archive_note": READ,
    "keep_create_keep_label": READ,
    "keep_create_note": ToolPolicy(
        "write",
        idempotent=False,
        triggers=(
            Trigger(
                "create_note",
                lambda a: bool(_sub(a).get("collaborator_emails")),
                "collaborator_emails not empty",
            ),
        ),
        preview=_keep_create_preview,
    ),
    "keep_delete_keep_label": READ,
    "keep_delete_note": _always_ask(
        "delete_note", _static("Permanently delete Keep note {note_name}? This cannot be undone."), idempotent=True
    ),
    "keep_get_note": READ,
    "keep_list_keep_labels": READ,
    "keep_list_notes": READ,
    "keep_patch_note_checklist": ToolPolicy(
        "write",
        idempotent=False,
        triggers=(
            Trigger(
                "delete_note",
                _keep_replaces_and_deletes,
                "apply_via_replacement and delete_original_on_apply true",
            ),
        ),
        preview=_static("Replace Keep note {note_name} with a new note and permanently delete the original?"),
    ),
    "keep_remove_checklist_item": READ,
    "keep_share_note": _always_ask("share_note", _keep_share_preview, idempotent=False),
    "keep_toggle_checklist_item": READ,
    "keep_unarchive_note": READ,
    "keep_unshare_note": _always_ask("unshare_note", _keep_unshare_preview, idempotent=True),
    "keep_update_note": READ,
    # --- meet (optional) ------------------------------------------------------
    "meet_create_space": _write(),
    "meet_end_active_conference": _always_ask(
        "end_active_conference",
        _static("End the conference running in Meet space {space_name}? Everyone in it is disconnected."),
        idempotent=True,
    ),
    "meet_get_conference_record": READ,
    "meet_get_space": READ,
    "meet_list_conference_participants": READ,
    "meet_list_conference_recordings": READ,
    "meet_list_conference_records": READ,
    "meet_list_conference_transcripts": READ,
    "meet_update_space": ToolPolicy(
        "write",
        idempotent=True,
        triggers=(Trigger("update_space", _meet_opens_space, "config.accessType 'OPEN'"),),
        preview=_static("Let anyone with the link join Meet space {space_name} without knocking (accessType OPEN)?"),
    ),
}


def consent_capable_tools() -> frozenset[str]:
    """Tools some call of which is consequential (prepare_workspace_action's choices)."""
    return frozenset(name for name, policy in CONSENT_TABLE.items() if policy.may_be_consequential)


# ---------------------------------------------------------------------------
# Per-call state: memoized reads, prepare and commit
# ---------------------------------------------------------------------------

_MEMO: ContextVar[dict[Any, Any] | None] = ContextVar("mcp_consent_memo", default=None)


@contextmanager
def consent_scope() -> Iterator[None]:
    """One tool call: reads made for the preview are reused by the tool body."""
    token = _MEMO.set({})
    try:
        yield
    finally:
        _MEMO.reset(token)


async def memoized(key: Any, factory: Callable[[], Awaitable[Any]]) -> Any:
    """``factory()`` once per tool call (preview and body share the result)."""
    memo = _MEMO.get()
    if memo is None:
        return await factory()
    if key not in memo:
        memo[key] = await factory()
    return memo[key]


@dataclass
class PrepareRequest:
    """``prepare_workspace_action`` running its call up to the consent gate."""

    tool: str
    used: bool = False
    action: str | None = None
    preview: str | None = None
    digest: str | None = None


@dataclass
class CommitGrant:
    """Consent ``commit_workspace_action`` carries for one prepared call."""

    tool: str
    digest: str
    used: bool = False


_PREPARE: ContextVar[PrepareRequest | None] = ContextVar("mcp_consent_prepare", default=None)
_COMMIT: ContextVar[CommitGrant | None] = ContextVar("mcp_consent_commit", default=None)


@contextmanager
def preparing(tool: str) -> Iterator[PrepareRequest]:
    request = PrepareRequest(tool)
    token = _PREPARE.set(request)
    try:
        yield request
    finally:
        _PREPARE.reset(token)


@contextmanager
def committing(tool: str, digest: str) -> Iterator[CommitGrant]:
    grant = CommitGrant(tool, digest)
    token = _COMMIT.set(grant)
    try:
        yield grant
    finally:
        _COMMIT.reset(token)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CallBinding:
    """What the confirmation guard knows about one call."""

    tool: str
    arguments: Mapping[str, Any]  # JSON-compatible, defaults applied
    digest: Callable[[], str]
    compact: Mapping[str, Any] = field(default_factory=dict)  # without default values


async def render_preview(tool: str, arguments: Arguments, action: str | None) -> str:
    policy = CONSENT_TABLE.get(tool)
    if policy is None or policy.preview is None:
        return f"Run {tool}?"
    return await policy.preview(arguments, action)


def _prepare_action(call: CallBinding) -> dict[str, Any]:
    return {
        "tool": "prepare_workspace_action",
        "arguments": {"tool_name": call.tool, "arguments": dict(call.compact)},
    }


def _consent_required(call: CallBinding, action: str, preview: str, *, preview_only: bool) -> Exception:
    from .errors import ConfirmationRequiredError

    return ConfirmationRequiredError(
        action,
        preview,
        required_action=_prepare_action(call),
        reason=(
            "this server requires prepare/commit for consequential calls (MCP_CONSENT=preview_only)"
            if preview_only
            else "the client did not declare elicitation support"
        ),
    )


def _client_can_be_asked(ctx: Context | None) -> bool:
    from .confirmation import _client_can_elicit, _request_era

    era = _request_era(ctx)
    if era is None or ctx is None:
        return False
    return era == "task" or _client_can_elicit(ctx)


async def enforce_consent(call: CallBinding, ctx: Context | None) -> dict[str, Any] | None:
    """Gate one call before its body runs.

    Returns ``None`` to let the body run, or the result to return instead
    (``cancelled``, ``dry_run``, or a prepared marker). Raises the multi
    round-trip ask, ``confirmation_required`` (with the prepare next step) or
    ``confirmation_invalid``.
    """
    from .confirmation import confirm_destructive_action
    from .errors import ConfirmationRejectedError, RecoverableToolError

    policy = CONSENT_TABLE.get(call.tool)
    if policy is None:
        return None
    arguments = call.arguments
    dry_run = policy.dry_run and arguments.get("dry_run") is True
    trigger = None if dry_run else policy.trigger_for(arguments)

    prepare = _PREPARE.get()
    if prepare is not None and not prepare.used and prepare.tool == call.tool:
        prepare.used = True
        if trigger is None:
            raise RecoverableToolError(
                "invalid_input",
                f"{call.tool} with these arguments does not need consent; call it directly.",
                required_action={"tool": call.tool, "arguments": dict(call.compact)},
            )
        prepare.action = trigger.action
        prepare.preview = await render_preview(call.tool, arguments, trigger.action)
        prepare.digest = call.digest()
        return {"status": "prepared"}

    if dry_run:
        would_trigger = policy.trigger_for({**arguments, "dry_run": False})
        return {
            "status": "dry_run",
            "consent_required": would_trigger is not None and consent_mode() != "off",
            "action": would_trigger.action if would_trigger is not None else None,
            "preview": await render_preview(
                call.tool, arguments, would_trigger.action if would_trigger is not None else None
            ),
        }
    if trigger is None:
        return None
    mode = consent_mode()
    if mode == "off":
        return None
    preview = await render_preview(call.tool, arguments, trigger.action)

    grant = _COMMIT.get()
    if grant is not None and not grant.used and grant.tool == call.tool:
        grant.used = True
        if not hmac.compare_digest(grant.digest, call.digest()):
            raise ConfirmationRejectedError(trigger.action, "arguments_changed")
        if mode == "preview_only" or not _client_can_be_asked(ctx):
            # The commit is the consent: the host's permission prompt on
            # commit_workspace_action is the human gate for this client.
            return None
        # A client that can ask still asks, with the same preview.
    elif mode == "preview_only":
        raise _consent_required(call, trigger.action, preview, preview_only=True)

    from .confirmation import ConfirmationInputRequired
    from .errors import ConfirmationRequiredError

    try:
        accepted = await confirm_destructive_action(
            ctx, trigger.action, preview, explicit_confirm_field=trigger.explicit_confirm
        )
    except ConfirmationInputRequired:
        raise  # the multi-round-trip ask itself
    except ConfirmationRequiredError:
        raise _consent_required(call, trigger.action, preview, preview_only=False) from None
    if not accepted:
        return {
            "status": "cancelled",
            "action": trigger.action,
            "reason": "The user declined; nothing was changed.",
        }
    return None


class TaskConsentMiddleware(Middleware):
    """Refuse, at submission, a consequential task call the task could never ask about.

    A background task runs in a worker without the client's session, so the
    gate there asks through the task's input loop. A client that declared no
    elicitation (or ``MCP_CONSENT=preview_only``) could never answer, and the
    task would wait until it expired. For task-capable tools such a call gets
    the same ``confirmation_required`` and prepare step here, before any task
    exists. Every other call is gated by the tool guard alone.
    """

    async def on_call_tool(self, context: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
        name = context.message.name
        policy = CONSENT_TABLE.get(name)
        if policy is not None and policy.may_be_consequential and await self._task_capable(context, name):
            await self._check(context, name, policy)
        return await call_next(context)

    @staticmethod
    async def _task_capable(context: Any, name: str) -> bool:
        from .fastmcp_compat import resolve_called_tool

        server = getattr(context.fastmcp_context, "fastmcp", None) if context.fastmcp_context else None
        if server is None:
            return False
        try:
            tool = await resolve_called_tool(server, name)
        except Exception:  # noqa: BLE001 - unknown tools are the dispatcher's error
            return False
        return tool is not None and bool(tool.task_config.supports_tasks())

    @staticmethod
    async def _check(context: Any, name: str, policy: ToolPolicy) -> None:
        from .confirmation import _client_can_elicit

        arguments = dict(context.message.arguments or {})
        if policy.dry_run and arguments.get("dry_run") is True:
            return
        trigger = policy.trigger_for(arguments)
        mode = consent_mode()
        if trigger is None or mode == "off":
            return
        prepare, grant = _PREPARE.get(), _COMMIT.get()
        if (prepare is not None and prepare.tool == name) or (grant is not None and grant.tool == name):
            return  # the tool guard handles prepare and commit
        ctx = context.fastmcp_context
        if mode == "required" and ctx is not None and _client_can_elicit(ctx):
            return
        preview = await render_preview(name, arguments, trigger.action)
        call = CallBinding(name, arguments, lambda: "", arguments)
        raise _consent_required(call, trigger.action, preview, preview_only=mode == "preview_only")


__all__ = [
    "BULK_THRESHOLD",
    "CONSENT_ENV",
    "CONSENT_MODES",
    "CONSENT_TABLE",
    "CallBinding",
    "Classification",
    "ConsentMode",
    "READ",
    "RiskClass",
    "TaskConsentMiddleware",
    "ToolPolicy",
    "Trigger",
    "classify",
    "committing",
    "consent_capable_tools",
    "consent_mode",
    "consent_scope",
    "enforce_consent",
    "log_consent_mode",
    "memoized",
    "preparing",
    "render_preview",
]
