"""Compact task representations for prioritization-oriented MCP responses."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def task_envelope(task: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Return the fields needed to triage a Google Task without its API noise."""
    now = now or datetime.now(UTC)
    due = task.get("due")
    due_at = _parse_timestamp(due)
    status = task.get("status", "needsAction")
    notes = str(task.get("notes") or "").strip()
    return {
        "id": task.get("id"),
        "title": task.get("title") or "(untitled task)",
        "status": status,
        "due": due,
        "is_overdue": bool(status != "completed" and due_at and due_at < now),
        "completed_at": task.get("completed"),
        "notes_snippet": notes[:300],
        "notes_truncated": len(notes) > 300,
        "parent_id": task.get("parent"),
        "updated_at": task.get("updated"),
        "deleted": bool(task.get("deleted", False)),
        "hidden": bool(task.get("hidden", False)),
    }


def tasklist_envelope(tasklist: dict[str, Any]) -> dict[str, Any]:
    return {"id": tasklist.get("id"), "title": tasklist.get("title"), "updated_at": tasklist.get("updated")}


def tasks_digest(
    tasks: list[dict[str, Any]], *, now: datetime | None = None, days: int = 7
) -> dict[str, list[dict[str, Any]]]:
    now = now or datetime.now(UTC)
    envelopes = [task_envelope(task, now=now) for task in tasks]
    week_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    week_end = week_end.fromtimestamp(week_end.timestamp() + days * 86_400, tz=UTC)
    upcoming = [
        task for task in envelopes
        if task["status"] != "completed"
        and (due_at := _parse_timestamp(task["due"])) is not None
        and now <= due_at <= week_end
    ]
    return {
        "overdue": [task for task in envelopes if task["is_overdue"]],
        "upcoming": sorted(upcoming, key=lambda task: task["due"] or ""),
        "unscheduled": [task for task in envelopes if task["status"] != "completed" and not task["due"]],
    }
