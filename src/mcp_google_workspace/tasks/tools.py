"""FastMCP Tasks tools."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context, FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import run_blocking
from ..common.confirmation import confirm_destructive_action
from ..common.errors import provider_tool_error
from ..common.timezone import resolve_user_timezone, user_now
from .client import tasks_service
from .schemas import (
    CompleteTaskRequest,
    CreateTaskRequest,
    CreateTasklistRequest,
    DeleteTaskRequest,
    GetTaskRequest,
    GetTasklistRequest,
    ListTasksRequest,
    ListTasklistsRequest,
    MoveTaskRequest,
    TaskStatus,
    UpdateTaskRequest,
)
from .presentation import task_envelope, tasklist_envelope, tasklists_page, tasks_digest, tasks_page

# How the Tasks API addresses the account's default task list.
DEFAULT_TASKLIST = "@default"
_LIST_TASKLIST_ID = "Task list ID from tasks_list_tasklists; omit for the account's default list."


def _completed_now(timezone_name: str) -> str:
    return user_now(timezone_name).replace(microsecond=0).isoformat()


def list_tasklists_payload(request: ListTasklistsRequest) -> dict[str, Any]:
    service = tasks_service()
    return service.tasklists().list(maxResults=request.max_results, pageToken=request.page_token).execute()


def get_tasklist_payload(request: GetTasklistRequest) -> dict[str, Any]:
    service = tasks_service()
    return service.tasklists().get(tasklist=request.tasklist_id).execute()


def create_tasklist_payload(request: CreateTasklistRequest) -> dict[str, Any]:
    service = tasks_service()
    return service.tasklists().insert(body={"title": request.title}).execute()


def list_tasks_payload(request: ListTasksRequest) -> dict[str, Any]:
    service = tasks_service()
    return service.tasks().list(
        tasklist=request.tasklist_id,
        completedMax=request.completed_max,
        completedMin=request.completed_min,
        dueMax=request.due_max,
        dueMin=request.due_min,
        maxResults=request.max_results,
        pageToken=request.page_token,
        showAssigned=request.show_assigned,
        showCompleted=request.show_completed,
        showDeleted=request.show_deleted,
        showHidden=request.show_hidden,
        updatedMin=request.updated_min,
    ).execute()


def get_task_payload(request: GetTaskRequest) -> dict[str, Any]:
    service = tasks_service()
    return service.tasks().get(tasklist=request.tasklist_id, task=request.task_id).execute()


def create_task_payload(request: CreateTaskRequest) -> dict[str, Any]:
    service = tasks_service()
    body: dict[str, Any] = {"title": request.title, "status": request.status}
    if request.notes is not None:
        body["notes"] = request.notes
    if request.due is not None:
        body["due"] = request.due
    return service.tasks().insert(
        tasklist=request.tasklist_id,
        parent=request.parent,
        previous=request.previous,
        body=body,
    ).execute()


def update_task_payload(request: UpdateTaskRequest) -> dict[str, Any]:
    service = tasks_service()
    body = request.model_dump(exclude_none=True, exclude={"tasklist_id", "task_id"}, mode="json")
    return service.tasks().patch(tasklist=request.tasklist_id, task=request.task_id, body=body).execute()


def complete_task_payload(
    request: CompleteTaskRequest, *, default_completed_at: str | None = None
) -> dict[str, Any]:
    service = tasks_service()
    completed_at = request.completed_at or default_completed_at
    if not completed_at:
        raise ValueError("completed_at must be supplied when no account timezone is available")
    return service.tasks().patch(
        tasklist=request.tasklist_id,
        task=request.task_id,
        body={"status": "completed", "completed": completed_at},
    ).execute()


def move_task_payload(request: MoveTaskRequest) -> dict[str, Any]:
    service = tasks_service()
    return service.tasks().move(
        tasklist=request.tasklist_id,
        task=request.task_id,
        parent=request.parent,
        previous=request.previous,
        destinationTasklist=request.destination_tasklist_id,
    ).execute()


def delete_task_payload(request: DeleteTaskRequest) -> dict[str, Any]:
    service = tasks_service()
    service.tasks().delete(tasklist=request.tasklist_id, task=request.task_id).execute()
    return {"status": "deleted", "task_id": request.task_id, "tasklist_id": request.tasklist_id}


def register_tools(server: FastMCP) -> None:
    @server.tool(name="list_tasklists")
    async def list_tasklists(
        max_results: Annotated[int, "Maximum task lists per page, 1 to 100."] = 100,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """List the user's Google Tasks task lists. Call it to get the tasklist_id the other tasks tools take ('@default' also selects the default list). Returns tasklists (id, title, updated_at in local time), count, next_page_token when more lists remain, and account_timezone."""
        timezone_name = await resolve_user_timezone()
        try:
            result = await run_blocking(
                list_tasklists_payload, ListTasklistsRequest(max_results=max_results, page_token=page_token)
            )
        except HttpError as exc:
            raise provider_tool_error(exc) from exc
        return tasklists_page(result, account_timezone=timezone_name)

    @server.tool(name="get_tasklist")
    async def get_tasklist(tasklist_id: str) -> dict[str, Any]:
        """Get one task list's title and last update time by ID. To find list IDs use tasks_list_tasklists, and for the tasks in a list use tasks_list_tasks. Returns id, title and updated_at (local time in the account time zone)."""
        timezone_name = await resolve_user_timezone()
        try:
            return tasklist_envelope(
                await run_blocking(get_tasklist_payload, GetTasklistRequest(tasklist_id=tasklist_id)),
                account_timezone=timezone_name,
            )
        except HttpError as exc:
            raise provider_tool_error(exc, tasklist_id=tasklist_id) from exc

    @server.tool(name="create_tasklist")
    async def create_tasklist(title: Annotated[str, "Name of the new task list."]) -> dict[str, Any]:
        """Create a new, empty Google Tasks task list. To add tasks to an existing list use tasks_create_task with its ID from tasks_list_tasklists. Returns status 'ok' and tasklist: id (the tasklist_id for the other tasks tools), title and updated_at (local time)."""
        timezone_name = await resolve_user_timezone()
        try:
            created = await run_blocking(create_tasklist_payload, CreateTasklistRequest(title=title))
            return {"status": "ok", "tasklist": tasklist_envelope(created, account_timezone=timezone_name)}
        except HttpError as exc:
            raise provider_tool_error(exc, title=title) from exc

    @server.tool(name="list_tasks")
    async def list_tasks(
        tasklist_id: Annotated[str | None, _LIST_TASKLIST_ID] = None,
        completed_max: Annotated[
            str | None,
            "Upper bound for the completion time: RFC3339, or YYYY-MM-DD for midnight UTC; a time without offset is read as UTC.",
        ] = None,
        completed_min: Annotated[
            str | None,
            "Lower bound for the completion time: RFC3339, or YYYY-MM-DD for midnight UTC; a time without offset is read as UTC.",
        ] = None,
        due_max: Annotated[
            str | None,
            "Upper bound for the due date: YYYY-MM-DD (midnight UTC) or RFC3339; a time without offset is read as UTC.",
        ] = None,
        due_min: Annotated[
            str | None,
            "Lower bound for the due date: YYYY-MM-DD (midnight UTC) or RFC3339; a time without offset is read as UTC.",
        ] = None,
        max_results: Annotated[int, "Maximum tasks per page, 1 to 100."] = 100,
        page_token: str | None = None,
        show_assigned: Annotated[
            bool, "Also return tasks assigned to the user, for example from Google Docs or Chat spaces."
        ] = False,
        show_completed: Annotated[
            bool,
            (
                "Return completed tasks too (default); false lists only open tasks. Tasks completed in "
                "the Google Tasks apps also need show_hidden true."
            ),
        ] = True,
        show_deleted: Annotated[bool, "Also return deleted tasks, flagged deleted true."] = False,
        show_hidden: Annotated[
            bool, "Also return hidden tasks: completed tasks cleared from the list, including those completed in the Google Tasks apps."
        ] = False,
        updated_min: Annotated[
            str | None,
            "Lower bound for the last modification time, RFC3339; a time without offset is read as UTC.",
        ] = None,
    ) -> dict[str, Any]:
        """List the tasks in one task list (the default list when tasklist_id is omitted), filtered by due date, completion or update time. For a triage view of open work grouped by overdue, upcoming and unscheduled use tasks_tasks_digest. Returns tasklist_id (the list's real ID when Google reports it), tasks (id, title, status, due_date, is_overdue, completed_at and updated_at in local time, notes_snippet, notes_truncated, parent_id, deleted, hidden, url), count, next_page_token when more tasks remain, and account_timezone."""
        timezone_name = await resolve_user_timezone()
        list_id = tasklist_id or DEFAULT_TASKLIST
        try:
            result = await run_blocking(
                list_tasks_payload,
                ListTasksRequest(
                    tasklist_id=list_id,
                    completed_max=completed_max,
                    completed_min=completed_min,
                    due_max=due_max,
                    due_min=due_min,
                    max_results=max_results,
                    page_token=page_token,
                    show_assigned=show_assigned,
                    show_completed=show_completed,
                    show_deleted=show_deleted,
                    show_hidden=show_hidden,
                    updated_min=updated_min,
                )
            )
        except HttpError as exc:
            raise provider_tool_error(exc, tasklist_id=list_id) from exc
        return tasks_page(
            result, tasklist_id=list_id, now=user_now(timezone_name), account_timezone=timezone_name
        )

    @server.tool(name="get_task")
    async def get_task(tasklist_id: str, task_id: str) -> dict[str, Any]:
        """Get one task by its task list and task ID. To list a task list's tasks use tasks_list_tasks. Returns id, title, status, due_date, is_overdue, completed_at and updated_at (local time in the account time zone), parent_id, deleted, hidden, url and the notes as notes_snippet (first 300 characters, with notes_truncated)."""
        timezone_name = await resolve_user_timezone()
        try:
            return task_envelope(
                await run_blocking(
                    get_task_payload,
                    GetTaskRequest(tasklist_id=tasklist_id, task_id=task_id),
                ),
                now=user_now(timezone_name),
                account_timezone=timezone_name,
            )
        except HttpError as exc:
            raise provider_tool_error(exc, tasklist_id=tasklist_id, task_id=task_id) from exc

    @server.tool(name="tasks_digest")
    async def tasks_digest_tool(
        tasklist_id: Annotated[str | None, _LIST_TASKLIST_ID] = None,
        days: Annotated[
            int,
            "Days ahead of today whose due tasks go in 'upcoming'; tasks due later appear in no group.",
        ] = 7,
        max_results: Annotated[
            int, "Maximum open tasks to read from the list, 1 to 100; there is no paging, so tasks past this are left out."
        ] = 100,
    ) -> dict[str, Any]:
        """Triage one task list's open tasks into overdue, upcoming and unscheduled work; it reads the default list unless tasklist_id names another one from tasks_list_tasklists. For completed tasks or custom filters use tasks_list_tasks. Returns overdue (due before today in the account time zone), upcoming (due from today through the window, soonest first) and unscheduled (no due date), each a list of tasks shaped as in tasks_list_tasks, plus count (tasks in the three groups), tasklist_id (the list's real ID when Google reports it), window_days and account_timezone."""
        timezone_name = await resolve_user_timezone()
        list_id = tasklist_id or DEFAULT_TASKLIST
        try:
            result = await run_blocking(
                list_tasks_payload,
                ListTasksRequest(tasklist_id=list_id, max_results=max_results, show_completed=False)
            )
        except HttpError as exc:
            raise provider_tool_error(exc, tasklist_id=list_id) from exc
        return tasks_digest(
            result,
            tasklist_id=list_id,
            now=user_now(timezone_name),
            account_timezone=timezone_name,
            days=days,
        )

    @server.tool(name="create_task")
    async def create_task(
        tasklist_id: str,
        title: Annotated[str, "Text of the task as shown in the list."],
        notes: Annotated[str | None, "Longer free-text details shown under the task."] = None,
        due: Annotated[
            str | None,
            (
                "Due date as YYYY-MM-DD (sent as midnight UTC) or RFC3339. Google Tasks keeps only the "
                "date, so a time of day is dropped."
            ),
        ] = None,
        parent: Annotated[
            str | None, "Parent task ID to nest this as a subtask under; omit for a top-level task."
        ] = None,
        previous: Annotated[
            str | None, "ID of the sibling task this task should be ordered immediately after; omit to place it first."
        ] = None,
        status: Annotated[TaskStatus, "'needsAction' creates an open task; 'completed' creates it already done."] = "needsAction",
    ) -> dict[str, Any]:
        """Add a task to a task list, optionally as a subtask (parent) or right after a sibling (previous). To change an existing task use tasks_update_task, and to reposition it use tasks_move_task. Returns status 'ok' and task: id (the task_id for the other tasks tools), title, status ('needsAction' or 'completed'), due_date, is_overdue, completed_at, updated_at, notes_snippet, notes_truncated, parent_id, deleted, hidden and url."""
        timezone_name = await resolve_user_timezone()
        try:
            created = await run_blocking(
                create_task_payload,
                CreateTaskRequest(
                    tasklist_id=tasklist_id,
                    title=title,
                    notes=notes,
                    due=due,
                    parent=parent,
                    previous=previous,
                    status=status,
                ),
            )
            return {
                "status": "ok",
                "task": task_envelope(created, now=user_now(timezone_name), account_timezone=timezone_name),
            }
        except HttpError as exc:
            raise provider_tool_error(exc, tasklist_id=tasklist_id) from exc

    @server.tool(name="update_task")
    async def update_task(
        tasklist_id: str,
        task_id: str,
        title: Annotated[str | None, "New text of the task."] = None,
        notes: Annotated[str | None, "New details text, replacing the old notes."] = None,
        due: Annotated[
            str | None,
            "New due date as YYYY-MM-DD (sent as midnight UTC) or RFC3339; Google Tasks keeps only the date.",
        ] = None,
        status: Annotated[
            TaskStatus | None, "'completed' marks the task done; 'needsAction' reopens it."
        ] = None,
        completed: Annotated[
            str | None,
            "Completion time to record, RFC3339 (YYYY-MM-DD means midnight UTC); usually sent with status 'completed'.",
        ] = None,
        deleted: Annotated[
            bool | None, "True flags the task as deleted (it then lists only with show_deleted); false clears the flag."
        ] = None,
        hidden: Annotated[
            bool | None, "Sets the hidden flag (true for completed tasks cleared from the list); Google documents it as read-only, so it may be ignored."
        ] = None,
    ) -> dict[str, Any]:
        """Change fields of an existing task; only the fields you pass change, and at least one is required. To just mark a task done use tasks_complete_task, and to reorder or move it use tasks_move_task. Returns status 'ok' and task, the task as stored now, shaped as in tasks_get_task: id, title, status, due_date, is_overdue, completed_at, updated_at, notes_snippet, notes_truncated, parent_id, deleted, hidden and url."""
        timezone_name = await resolve_user_timezone()
        try:
            updated = await run_blocking(
                update_task_payload,
                UpdateTaskRequest(
                    tasklist_id=tasklist_id,
                    task_id=task_id,
                    title=title,
                    notes=notes,
                    due=due,
                    status=status,
                    completed=completed,
                    deleted=deleted,
                    hidden=hidden,
                ),
            )
            return {
                "status": "ok",
                "task": task_envelope(updated, now=user_now(timezone_name), account_timezone=timezone_name),
            }
        except HttpError as exc:
            raise provider_tool_error(exc, tasklist_id=tasklist_id, task_id=task_id) from exc

    @server.tool(name="complete_task")
    async def complete_task(
        tasklist_id: str,
        task_id: str,
        completed_at: Annotated[
            str | None,
            "Completion time to record, RFC3339 (YYYY-MM-DD means midnight UTC); defaults to now in the account's time zone.",
        ] = None,
    ) -> dict[str, Any]:
        """Mark one task as done, recording when it was completed. To reopen it use tasks_update_task with status 'needsAction'. Returns status 'ok' and task, shaped as in tasks_get_task: id, title, status ('completed'), completed_at (the recorded time, in local time), due_date, is_overdue, updated_at, notes_snippet, notes_truncated, parent_id, deleted, hidden and url."""
        timezone_name = await resolve_user_timezone()
        try:
            completed = await run_blocking(
                complete_task_payload,
                CompleteTaskRequest(tasklist_id=tasklist_id, task_id=task_id, completed_at=completed_at),
                default_completed_at=_completed_now(timezone_name),
            )
            return {
                "status": "ok",
                "task": task_envelope(completed, now=user_now(timezone_name), account_timezone=timezone_name),
            }
        except HttpError as exc:
            raise provider_tool_error(exc, tasklist_id=tasklist_id, task_id=task_id) from exc

    @server.tool(name="move_task")
    async def move_task(
        tasklist_id: str,
        task_id: str,
        parent: Annotated[
            str | None,
            "Task ID in the same list to nest this task under; omit to make it a top-level task. Not allowed with destination_tasklist_id.",
        ] = None,
        previous: Annotated[
            str | None,
            "ID of the sibling task to place this task right after; omit to place it first. Not allowed with destination_tasklist_id.",
        ] = None,
        destination_tasklist_id: Annotated[
            str | None,
            "Task list ID to move the task into; omit to reposition it within its current list.",
        ] = None,
    ) -> dict[str, Any]:
        """Reposition a task within its list (nest it under a parent or place it after a sibling), or move it to another list. A cross-list move cannot also set parent or previous: move first, then call again on the destination list to place it. Returns status 'ok' and task, the task after the move shaped as in tasks_get_task: id, title, status, due_date, is_overdue, completed_at, updated_at, notes_snippet, notes_truncated, parent_id (null at the top level), deleted, hidden and url."""
        timezone_name = await resolve_user_timezone()
        try:
            moved = await run_blocking(
                move_task_payload,
                MoveTaskRequest(
                    tasklist_id=tasklist_id,
                    task_id=task_id,
                    parent=parent,
                    previous=previous,
                    destination_tasklist_id=destination_tasklist_id,
                ),
            )
            return {
                "status": "ok",
                "task": task_envelope(moved, now=user_now(timezone_name), account_timezone=timezone_name),
            }
        except HttpError as exc:
            raise provider_tool_error(exc, tasklist_id=tasklist_id, task_id=task_id) from exc

    @server.tool(name="delete_task")
    async def delete_task(
        tasklist_id: str,
        task_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Delete a task from its task list after the user confirms. To only mark it done use tasks_complete_task. Returns status 'deleted' with task_id and tasklist_id; declining returns status 'cancelled', and a client that cannot ask gets a confirmation_required error, both with nothing deleted."""
        request = DeleteTaskRequest(tasklist_id=tasklist_id, task_id=task_id)
        if not await confirm_destructive_action(
            ctx, "delete_task", f"Permanently delete task {request.task_id}?"
        ):
            return {
                "status": "cancelled",
                "task_id": request.task_id,
                "tasklist_id": request.tasklist_id,
            }
        try:
            return await run_blocking(delete_task_payload, request)
        except HttpError as exc:
            raise provider_tool_error(exc, tasklist_id=tasklist_id, task_id=task_id) from exc
