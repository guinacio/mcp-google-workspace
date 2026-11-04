"""Turn Gmail label names or IDs into label IDs, for one tool call.

Tools that take labels accept what a person would type ("Clients/Acme",
"starred") as well as Gmail's IDs ("Label_12", "STARRED"). System labels are
recognised by ID, case-insensitively, without a request. Anything else is
looked up in the mailbox's label list, which is fetched at most once per
resolver, so one tool call costs at most one ``users.labels.list``. A value
that matches no label, or a name that matches several, raises ``not_found``
with the candidates: a label is never guessed and never created.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from ..common.errors import RecoverableToolError

#: Gmail's system label IDs. Their IDs are also their names.
SYSTEM_LABEL_IDS = frozenset(
    {
        "CATEGORY_FORUMS",
        "CATEGORY_PERSONAL",
        "CATEGORY_PROMOTIONS",
        "CATEGORY_SOCIAL",
        "CATEGORY_UPDATES",
        "CHAT",
        "DRAFT",
        "IMPORTANT",
        "INBOX",
        "SENT",
        "SPAM",
        "STARRED",
        "TRASH",
        "UNREAD",
    }
)

_MAX_CANDIDATES = 25

Execute = Callable[[Any], Awaitable[dict[str, Any]]]


class LabelResolver:
    """Resolves label names or IDs with at most one ``labels.list`` request."""

    def __init__(self, service: Any, execute: Execute) -> None:
        self._service = service
        self._execute = execute
        self._labels: list[dict[str, Any]] | None = None

    async def _mailbox_labels(self) -> list[dict[str, Any]]:
        if self._labels is None:
            listed = await self._execute(self._service.users().labels().list(userId="me"))
            self._labels = [label for label in listed.get("labels", []) if isinstance(label, dict)]
        return self._labels

    async def resolve(self, value: str) -> str:
        """The label ID for one name or ID."""
        wanted = value.strip()
        if not wanted:
            raise ValueError("A label name or ID must not be blank.")
        if wanted.upper() in SYSTEM_LABEL_IDS:
            return wanted.upper()
        labels = await self._mailbox_labels()
        for label in labels:
            if label.get("id") == wanted:
                return wanted
        exact = [label for label in labels if label.get("name") == wanted]
        if len(exact) == 1:
            return str(exact[0]["id"])
        folded = wanted.casefold()
        matches = exact or [label for label in labels if str(label.get("name", "")).casefold() == folded]
        if len(matches) == 1:
            return str(matches[0]["id"])
        if matches:
            raise _label_not_found(
                wanted,
                f"The label name {wanted!r} matches {len(matches)} labels; pass one of their IDs instead.",
                matches,
            )
        similar = [label for label in labels if folded in str(label.get("name", "")).casefold()]
        candidates = similar or [label for label in labels if label.get("type") == "user"]
        raise _label_not_found(
            wanted,
            f"No Gmail label is named or has the ID {wanted!r}; nothing was changed.",
            candidates,
        )

    async def resolve_all(self, values: Iterable[str] | None) -> list[str]:
        """Label IDs for several names or IDs, in order, without duplicates."""
        resolved: list[str] = []
        for value in values or []:
            label_id = await self.resolve(value)
            if label_id not in resolved:
                resolved.append(label_id)
        return resolved


def _label_not_found(value: str, message: str, candidates: list[dict[str, Any]]) -> RecoverableToolError:
    listed = [
        {"id": label.get("id"), "name": label.get("name")}
        for label in sorted(candidates, key=lambda label: str(label.get("name", "")).casefold())
    ]
    return RecoverableToolError(
        "not_found",
        message,
        required_action={"tool": "gmail_list_labels", "arguments": {}},
        details={
            "label": value,
            "candidates": listed[:_MAX_CANDIDATES],
            "candidates_truncated": len(listed) > _MAX_CANDIDATES,
        },
    )
