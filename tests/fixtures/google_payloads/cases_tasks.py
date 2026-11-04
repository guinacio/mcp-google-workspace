"""Tasks normalizers over recorded-shape Google Tasks API payloads."""

from __future__ import annotations

from mcp_google_workspace.tasks.presentation import (
    task_envelope,
    tasklist_envelope,
    tasklists_page,
    tasks_digest,
    tasks_page,
)

from . import NOW, TIMEZONE, Case


def _mutated(payload):
    return {"status": "ok", "task": task_envelope(payload, now=NOW, account_timezone=TIMEZONE)}


CASES: list[Case] = [
    Case(
        "tasks_list_tasks",
        "tasks_list_tasks.json",
        # The list was omitted, so '@default' was sent; the real ID comes from the tasks' selfLink.
        lambda payload: tasks_page(payload, tasklist_id="@default", now=NOW, account_timezone=TIMEZONE),
        items_key="tasks",
    ),
    Case(
        "tasks_tasks_digest",
        "tasks_list_tasks.json",
        lambda payload: tasks_digest(payload, tasklist_id="@default", now=NOW, account_timezone=TIMEZONE, days=7),
    ),
    Case(
        "tasks_get_task",
        "tasks_get_task.json",
        lambda payload: task_envelope(payload, now=NOW, account_timezone=TIMEZONE),
    ),
    Case("tasks_create_task", "tasks_insert_task.json", _mutated),
    Case("tasks_update_task", "tasks_patch_task.json", _mutated),
    Case("tasks_complete_task", "tasks_complete_task.json", _mutated),
    Case("tasks_move_task", "tasks_move_task.json", _mutated),
    Case(
        "tasks_list_tasklists",
        "tasks_list_tasklists.json",
        lambda payload: tasklists_page(payload, account_timezone=TIMEZONE),
        items_key="tasklists",
    ),
    Case(
        "tasks_get_tasklist",
        "tasks_insert_tasklist.json",
        lambda payload: tasklist_envelope(payload, account_timezone=TIMEZONE),
    ),
    Case(
        "tasks_create_tasklist",
        "tasks_insert_tasklist.json",
        lambda payload: {"status": "ok", "tasklist": tasklist_envelope(payload, account_timezone=TIMEZONE)},
    ),
]
