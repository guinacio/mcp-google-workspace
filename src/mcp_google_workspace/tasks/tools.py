"""FastMCP Tasks tools."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastmcp import FastMCP

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


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def complete_task_payload(request: CompleteTaskRequest) -> dict[str, Any]:
    service = tasks_service()
    completed_at = request.completed_at or _utc_now()
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
    async def list_tasklists(max_results: int = 100, page_token: str | None = None) -> dict[str, Any]:
        return list_tasklists_payload(ListTasklistsRequest(max_results=max_results, page_token=page_token))

    @server.tool(name="get_tasklist")
    async def get_tasklist(tasklist_id: str) -> dict[str, Any]:
        return get_tasklist_payload(GetTasklistRequest(tasklist_id=tasklist_id))

    @server.tool(name="create_tasklist")
    async def create_tasklist(title: str) -> dict[str, Any]:
        return create_tasklist_payload(CreateTasklistRequest(title=title))

    @server.tool(name="list_tasks")
    async def list_tasks(
        tasklist_id: str,
        completed_max: str | None = None,
        completed_min: str | None = None,
        due_max: str | None = None,
        due_min: str | None = None,
        max_results: int = 100,
        page_token: str | None = None,
        show_assigned: bool = False,
        show_completed: bool = True,
        show_deleted: bool = False,
        show_hidden: bool = False,
        updated_min: str | None = None,
    ) -> dict[str, Any]:
        return list_tasks_payload(
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

    @server.tool(name="get_task")
    async def get_task(tasklist_id: str, task_id: str) -> dict[str, Any]:
        return get_task_payload(GetTaskRequest(tasklist_id=tasklist_id, task_id=task_id))

    @server.tool(name="create_task")
    async def create_task(
        tasklist_id: str,
        title: str,
        notes: str | None = None,
        due: str | None = None,
        parent: str | None = None,
        previous: str | None = None,
        status: TaskStatus = "needsAction",
    ) -> dict[str, Any]:
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

    @server.tool(name="update_task")
    async def update_task(
        tasklist_id: str,
        task_id: str,
        title: str | None = None,
        notes: str | None = None,
        due: str | None = None,
        status: TaskStatus | None = None,
        completed: str | None = None,
        deleted: bool | None = None,
        hidden: bool | None = None,
    ) -> dict[str, Any]:
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

    @server.tool(name="complete_task")
    async def complete_task(tasklist_id: str, task_id: str, completed_at: str | None = None) -> dict[str, Any]:
        return complete_task_payload(
            CompleteTaskRequest(tasklist_id=tasklist_id, task_id=task_id, completed_at=completed_at)
        )

    @server.tool(name="move_task")
    async def move_task(
        tasklist_id: str,
        task_id: str,
        parent: str | None = None,
        previous: str | None = None,
        destination_tasklist_id: str | None = None,
    ) -> dict[str, Any]:
        return move_task_payload(
            MoveTaskRequest(
                tasklist_id=tasklist_id,
                task_id=task_id,
                parent=parent,
                previous=previous,
                destination_tasklist_id=destination_tasklist_id,
            )
        )

    @server.tool(name="delete_task")
    async def delete_task(tasklist_id: str, task_id: str) -> dict[str, Any]:
        return delete_task_payload(DeleteTaskRequest(tasklist_id=tasklist_id, task_id=task_id))
