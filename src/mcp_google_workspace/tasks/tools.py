"""FastMCP Tasks tools."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context, FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import confirm_destructive_action, run_blocking
from ..common.errors import tool_error_payload
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
from .presentation import task_envelope, tasklist_envelope, tasks_digest


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
    def list_tasklists(max_results: int = 100, page_token: str | None = None) -> dict[str, Any]:
        """List the account's tasklists (each tasklist's 'id' feeds the other tasks tools).

        Returns {"tasklists": [...], "next_page_token", "count"}, or
        {"error": ...} on API failure.
        """
        try:
            result = list_tasklists_payload(ListTasklistsRequest(max_results=max_results, page_token=page_token))
        except HttpError as exc:
            return tool_error_payload(exc)
        items = result.get("items", [])
        return {"tasklists": [tasklist_envelope(item) for item in items], "next_page_token": result.get("nextPageToken"), "count": len(items)}

    @server.tool(name="get_tasklist")
    def get_tasklist(tasklist_id: str) -> dict[str, Any]:
        """Fetch one tasklist's metadata (title, updated time) by its ID.

        Returns the tasklist envelope, or {"error": ...} on API failure.
        """
        try:
            return tasklist_envelope(get_tasklist_payload(GetTasklistRequest(tasklist_id=tasklist_id)))
        except HttpError as exc:
            return tool_error_payload(exc, tasklist_id=tasklist_id)

    @server.tool(name="create_tasklist")
    def create_tasklist(title: str) -> dict[str, Any]:
        """Create a new tasklist with the given title.

        Returns the created tasklist resource (including its 'id'), or
        {"error": ...} on API failure.
        """
        try:
            return create_tasklist_payload(CreateTasklistRequest(title=title))
        except HttpError as exc:
            return tool_error_payload(exc, title=title)

    @server.tool(name="list_tasks")
    async def list_tasks(
        tasklist_id: str,
        completed_max: Annotated[
            str | None, "Only return tasks completed on or before this RFC3339 timestamp or YYYY-MM-DD date."
        ] = None,
        completed_min: Annotated[
            str | None, "Only return tasks completed on or after this RFC3339 timestamp or YYYY-MM-DD date."
        ] = None,
        due_max: Annotated[
            str | None, "Only return tasks due on or before this RFC3339 timestamp or YYYY-MM-DD date."
        ] = None,
        due_min: Annotated[
            str | None, "Only return tasks due on or after this RFC3339 timestamp or YYYY-MM-DD date."
        ] = None,
        max_results: int = 100,
        page_token: str | None = None,
        show_assigned: bool = False,
        show_completed: bool = True,
        show_deleted: bool = False,
        show_hidden: bool = False,
        updated_min: Annotated[
            str | None, "Only return tasks last updated on or after this RFC3339 timestamp."
        ] = None,
    ) -> dict[str, Any]:
        """List tasks in a tasklist, with optional due/completed/updated time filters.

        Timestamp filters accept RFC3339 or bare YYYY-MM-DD dates. Returns
        {"tasks": [...], "next_page_token", "count", "account_timezone"} with
        each task annotated with overdue/due-state info, or {"error": ...} on
        API failure.
        """
        timezone_name = await resolve_user_timezone()
        now = user_now(timezone_name)
        try:
            result = await run_blocking(
                list_tasks_payload,
                ListTasksRequest(
                    tasklist_id=tasklist_id,
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
            return tool_error_payload(exc, tasklist_id=tasklist_id)
        items = result.get("items", [])
        return {
            "tasklist_id": tasklist_id,
            "tasks": [task_envelope(item, now=now) for item in items],
            "account_timezone": timezone_name,
            "next_page_token": result.get("nextPageToken"),
            "count": len(items),
        }

    @server.tool(name="get_task")
    async def get_task(tasklist_id: str, task_id: str) -> dict[str, Any]:
        """Fetch one task by tasklist ID and task ID.

        Returns the task envelope (title, notes, due, status, overdue state),
        or {"error": ...} on API failure.
        """
        timezone_name = await resolve_user_timezone()
        try:
            return task_envelope(
                await run_blocking(
                    get_task_payload,
                    GetTaskRequest(tasklist_id=tasklist_id, task_id=task_id),
                ),
                now=user_now(timezone_name),
            )
        except HttpError as exc:
            return tool_error_payload(exc, tasklist_id=tasklist_id, task_id=task_id)

    @server.tool(name="tasks_digest")
    async def tasks_digest_tool(
        tasklist_id: str,
        days: Annotated[
            int, "Number of days ahead of now that a due task is grouped into 'upcoming' rather than 'unscheduled'."
        ] = 7,
        max_results: int = 100,
    ) -> dict[str, Any]:
        """Group incomplete tasks into overdue, upcoming, and unscheduled work.

        Returns the grouped digest plus tasklist/window metadata, or
        {"error": ...} on API failure.
        """
        timezone_name = await resolve_user_timezone()
        try:
            result = await run_blocking(
                list_tasks_payload,
                ListTasksRequest(tasklist_id=tasklist_id, max_results=max_results, show_completed=False)
            )
        except HttpError as exc:
            return tool_error_payload(exc, tasklist_id=tasklist_id)
        digest = tasks_digest(
            result.get("items", []), now=user_now(timezone_name), days=days
        )
        digest.update(
            {
                "tasklist_id": tasklist_id,
                "window_days": days,
                "account_timezone": timezone_name,
            }
        )
        return digest

    @server.tool(name="create_task")
    def create_task(
        tasklist_id: str,
        title: str,
        notes: Annotated[str | None, "Task notes/description text."] = None,
        due: Annotated[
            str | None,
            "Task due date/time as YYYY-MM-DD or RFC3339; normalized to midnight UTC when only a date is given.",
        ] = None,
        parent: Annotated[
            str | None, "Parent task ID to nest this as a subtask under; omit for a top-level task."
        ] = None,
        previous: Annotated[
            str | None, "ID of the sibling task this task should be ordered immediately after; omit to place it first."
        ] = None,
        status: Annotated[TaskStatus, "Task status: needsAction or completed."] = "needsAction",
    ) -> dict[str, Any]:
        """Create a task in a tasklist, optionally nested (parent) or positioned (previous).

        parent nests the new task as a subtask; previous places it after a
        sibling task; both refer to task IDs in the same tasklist. Returns the
        created task resource (including its 'id'), or {"error": ...} on API
        failure.
        """
        try:
            return create_task_payload(
                CreateTaskRequest(
                    tasklist_id=tasklist_id,
                    title=title,
                    notes=notes,
                    due=due,
                    parent=parent,
                    previous=previous,
                    status=status,
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, tasklist_id=tasklist_id)

    @server.tool(name="update_task")
    def update_task(
        tasklist_id: str,
        task_id: str,
        title: str | None = None,
        notes: Annotated[str | None, "Updated task notes/description text."] = None,
        due: Annotated[
            str | None,
            "Updated due date/time as YYYY-MM-DD or RFC3339; normalized to midnight UTC when only a date is given.",
        ] = None,
        status: Annotated[TaskStatus | None, "Updated task status: needsAction or completed."] = None,
        completed: Annotated[
            str | None, "Completion RFC3339 timestamp to set; typically paired with status='completed'."
        ] = None,
        deleted: Annotated[bool | None, "Whether to mark the task as deleted."] = None,
        hidden: Annotated[bool | None, "Whether to mark the task as hidden from the default task list view."] = None,
    ) -> dict[str, Any]:
        """Patch fields on an existing task; only the supplied fields change.

        At least one mutable field must be provided. Returns the updated task
        resource, or {"error": ...} on API failure.
        """
        try:
            return update_task_payload(
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
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, tasklist_id=tasklist_id, task_id=task_id)

    @server.tool(name="complete_task")
    async def complete_task(
        tasklist_id: str,
        task_id: str,
        completed_at: Annotated[
            str | None, "Completion RFC3339 timestamp to record; defaults to the account's current local time."
        ] = None,
    ) -> dict[str, Any]:
        """Mark a task as completed, recording the completion timestamp.

        Returns the updated task resource, or {"error": ...} on API failure.
        """
        timezone_name = await resolve_user_timezone()
        try:
            return await run_blocking(
                complete_task_payload,
                CompleteTaskRequest(tasklist_id=tasklist_id, task_id=task_id, completed_at=completed_at),
                default_completed_at=_completed_now(timezone_name),
            )
        except HttpError as exc:
            return tool_error_payload(exc, tasklist_id=tasklist_id, task_id=task_id)

    @server.tool(name="move_task")
    def move_task(
        tasklist_id: str,
        task_id: str,
        parent: Annotated[
            str | None, "New parent task ID to nest this task under; omit to leave it at the top level."
        ] = None,
        previous: Annotated[
            str | None, "ID of the sibling task this task should be ordered immediately after; omit to place it first."
        ] = None,
        destination_tasklist_id: Annotated[
            str | None, "Target tasklist ID to move the task into; omit to reorder within the same tasklist."
        ] = None,
    ) -> dict[str, Any]:
        """Reorder a task within its tasklist, or move it to another tasklist.

        Use parent/previous (task IDs in the same tasklist) to reposition, OR
        destination_tasklist_id to move across tasklists — the Tasks API
        rejects combining the two, so cross-list moves land at the destination's
        default position (reorder with a second call). Returns the moved task
        resource, or {"error": ...} on API failure.
        """
        try:
            return move_task_payload(
                MoveTaskRequest(
                    tasklist_id=tasklist_id,
                    task_id=task_id,
                    parent=parent,
                    previous=previous,
                    destination_tasklist_id=destination_tasklist_id,
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, tasklist_id=tasklist_id, task_id=task_id)

    @server.tool(name="delete_task")
    async def delete_task(
        tasklist_id: str,
        task_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Permanently delete a task after interactive host confirmation.

        Returns {"status": "deleted"} on success, {"status": "cancelled"} if
        the user declines, or {"error": ...} on API failure.
        """
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
            return tool_error_payload(exc, tasklist_id=tasklist_id, task_id=task_id)
