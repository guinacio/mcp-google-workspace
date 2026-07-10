"""Compact task representations for prioritization-oriented MCP responses."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _task_due_date(value: str | None) -> date | None:
    """Google Tasks due values represent a date, even when serialized at 00:00Z."""
    parsed = _parse_timestamp(value)
    return parsed.date() if parsed else None


def task_envelope(task: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Return the fields needed to triage a Google Task without its API noise."""
    now = now or datetime.now(UTC)
    due = task.get("due")
    due_date = _task_due_date(due)
    status = task.get("status", "needsAction")
    notes = str(task.get("notes") or "").strip()
    return {
        "id": task.get("id"),
        "title": task.get("title") or "(untitled task)",
        "status": status,
        "due_date": due_date.isoformat() if due_date else None,
        "due_is_date_only": bool(due_date),
        "source_due": due,
        "is_overdue": bool(status != "completed" and due_date and due_date < now.date()),
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
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    envelopes = [task_envelope(task, now=now) for task in tasks]
    week_end = (now + timedelta(days=days)).replace(
        hour=23, minute=59, second=59, microsecond=999999
    )
    upcoming = [
        task
        for task in envelopes
        if task["status"] != "completed"
        and (due_date := _task_due_date(task["source_due"])) is not None
        and now.date() <= due_date <= week_end.date()
    ]
    return {
        "overdue": [task for task in envelopes if task["is_overdue"]],
        "upcoming": sorted(upcoming, key=lambda task: task["due_date"] or ""),
        "unscheduled": [
            task for task in envelopes if task["status"] != "completed" and not task["due_date"]
        ],
    }
