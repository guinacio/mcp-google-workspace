"""Compact task representations for prioritization-oriented MCP responses."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

import pytz

from ..common.resources import clean_web_url
from ..common.timezone import in_account_timezone

_NOTES_SNIPPET = 300
# A task's selfLink names the list it lives in by its real ID, also when the
# request addressed the list as '@default'.
_SELF_LINK_LIST = re.compile(r"/lists/([^/]+)/tasks/")


def _task_due_date(value: str | None) -> date | None:
    """Google Tasks stores only a due date, serialized as midnight UTC (YYYY-MM-DDT00:00:00.000Z)."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _local_today(now: datetime, account_timezone: str) -> date:
    return now.astimezone(pytz.timezone(account_timezone)).date()


def task_envelope(task: dict[str, Any], *, now: datetime, account_timezone: str) -> dict[str, Any]:
    """Return the fields needed to triage a Google Task without its API noise."""
    due_date = _task_due_date(task.get("due"))
    status = task.get("status", "needsAction")
    notes = str(task.get("notes") or "").strip()
    return {
        "id": task.get("id"),
        "title": task.get("title") or "(untitled task)",
        "status": status,
        "due_date": due_date.isoformat() if due_date else None,
        "is_overdue": bool(
            status != "completed" and due_date and due_date < _local_today(now, account_timezone)
        ),
        "completed_at": in_account_timezone(task.get("completed"), account_timezone),
        "notes_snippet": notes[:_NOTES_SNIPPET],
        "notes_truncated": len(notes) > _NOTES_SNIPPET,
        "parent_id": task.get("parent"),
        "updated_at": in_account_timezone(task.get("updated"), account_timezone),
        "deleted": bool(task.get("deleted", False)),
        "hidden": bool(task.get("hidden", False)),
        "url": clean_web_url(task.get("webViewLink")),
    }


def tasklist_envelope(tasklist: dict[str, Any], *, account_timezone: str) -> dict[str, Any]:
    return {
        "id": tasklist.get("id"),
        "title": tasklist.get("title"),
        "updated_at": in_account_timezone(tasklist.get("updated"), account_timezone),
    }


def tasklists_page(payload: dict[str, Any], *, account_timezone: str) -> dict[str, Any]:
    """tasklists.list as one page of task lists."""
    tasklists = [
        tasklist_envelope(item, account_timezone=account_timezone) for item in payload.get("items") or []
    ]
    return {
        "tasklists": tasklists,
        "count": len(tasklists),
        "next_page_token": payload.get("nextPageToken"),
        "account_timezone": account_timezone,
    }


def _list_id(items: list[dict[str, Any]], requested: str) -> str:
    """The real ID of the listed task list, read from a task's selfLink; else the ID requested."""
    for item in items:
        match = _SELF_LINK_LIST.search(str(item.get("selfLink") or ""))
        if match:
            return match.group(1)
    return requested


def tasks_page(
    payload: dict[str, Any], *, tasklist_id: str, now: datetime, account_timezone: str
) -> dict[str, Any]:
    """tasks.list as one page of task envelopes."""
    items = payload.get("items") or []
    return {
        "tasklist_id": _list_id(items, tasklist_id),
        "tasks": [task_envelope(item, now=now, account_timezone=account_timezone) for item in items],
        "count": len(items),
        "next_page_token": payload.get("nextPageToken"),
        "account_timezone": account_timezone,
    }


def tasks_digest(
    payload: dict[str, Any],
    *,
    tasklist_id: str,
    now: datetime,
    account_timezone: str,
    days: int = 7,
) -> dict[str, Any]:
    """Open tasks of one tasks.list page grouped as overdue, upcoming and unscheduled.

    The groups are disjoint (due before today, due today through the window, no
    due date); a task due after the window is in none of them.
    """
    items = payload.get("items") or []
    envelopes = [task_envelope(item, now=now, account_timezone=account_timezone) for item in items]
    today = _local_today(now, account_timezone)
    window_end = today + timedelta(days=days)
    open_tasks = [task for task in envelopes if task["status"] != "completed"]
    overdue = [task for task in open_tasks if task["is_overdue"]]
    upcoming = sorted(
        (
            task
            for task in open_tasks
            if task["due_date"] is not None and today <= date.fromisoformat(task["due_date"]) <= window_end
        ),
        key=lambda task: task["due_date"],
    )
    unscheduled = [task for task in open_tasks if task["due_date"] is None]
    return {
        "tasklist_id": _list_id(items, tasklist_id),
        "overdue": overdue,
        "upcoming": upcoming,
        "unscheduled": unscheduled,
        "count": len(overdue) + len(upcoming) + len(unscheduled),
        "window_days": days,
        "account_timezone": account_timezone,
    }
