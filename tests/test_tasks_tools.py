import anyio
import pytest

from mcp_google_workspace.tasks.schemas import CreateTaskRequest, UpdateTaskRequest
from mcp_google_workspace.tasks.server import tasks_mcp
from mcp_google_workspace.tasks.tools import (
    CompleteTaskRequest,
    CreateTasklistRequest,
    DeleteTaskRequest,
    GetTaskRequest,
    GetTasklistRequest,
    ListTasksRequest,
    ListTasklistsRequest,
    MoveTaskRequest,
    complete_task_payload,
    create_task_payload,
    create_tasklist_payload,
    delete_task_payload,
    get_task_payload,
    get_tasklist_payload,
    list_tasks_payload,
    list_tasklists_payload,
    move_task_payload,
    update_task_payload,
)


async def _list_tool_names(server):
    tools = await server.list_tools(run_middleware=False)
    return [tool.name for tool in tools]


async def _get_tool(server, name):
    tools = await server.list_tools(run_middleware=False)
    return next(tool for tool in tools if tool.name == name)


class _Exec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _TasklistsApi:
    def __init__(self):
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return _Exec({"kind": "tasklists.list", "kwargs": kwargs})

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return _Exec({"kind": "tasklists.get", "kwargs": kwargs})

    def insert(self, **kwargs):
        self.calls.append(("insert", kwargs))
        return _Exec({"kind": "tasklists.insert", "kwargs": kwargs})


class _TasksApi:
    def __init__(self):
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return _Exec({"kind": "tasks.list", "kwargs": kwargs})

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return _Exec({"kind": "tasks.get", "kwargs": kwargs})

    def insert(self, **kwargs):
        self.calls.append(("insert", kwargs))
        return _Exec({"kind": "tasks.insert", "kwargs": kwargs})

    def patch(self, **kwargs):
        self.calls.append(("patch", kwargs))
        return _Exec({"kind": "tasks.patch", "kwargs": kwargs})

    def move(self, **kwargs):
        self.calls.append(("move", kwargs))
        return _Exec({"kind": "tasks.move", "kwargs": kwargs})

    def delete(self, **kwargs):
        self.calls.append(("delete", kwargs))
        return _Exec({"kind": "tasks.delete", "kwargs": kwargs})


class _TasksService:
    def __init__(self):
        self.tasklists_api = _TasklistsApi()
        self.tasks_api = _TasksApi()

    def tasklists(self):
        return self.tasklists_api

    def tasks(self):
        return self.tasks_api


def test_tasks_server_registers_expected_tools():
    tool_names = anyio.run(_list_tool_names, tasks_mcp)
    assert "list_tasklists" in tool_names
    assert "get_tasklist" in tool_names
    assert "create_tasklist" in tool_names
    assert "list_tasks" in tool_names
    assert "get_task" in tool_names
    assert "create_task" in tool_names
    assert "update_task" in tool_names
    assert "complete_task" in tool_names
    assert "move_task" in tool_names
    assert "delete_task" in tool_names


def test_tasks_request_normalization_and_validation():
    request = CreateTaskRequest(tasklist_id="list-1", title="Follow up", due="2026-03-12")
    assert request.due == "2026-03-12T00:00:00.000Z"

    with pytest.raises(ValueError):
        UpdateTaskRequest(tasklist_id="list-1", task_id="task-1")


def test_tasks_payload_helpers(monkeypatch):
    service = _TasksService()
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.tasks_service", lambda: service)

    tasklists = list_tasklists_payload(ListTasklistsRequest())
    tasklist = get_tasklist_payload(GetTasklistRequest(tasklist_id="list-1"))
    created_tasklist = create_tasklist_payload(CreateTasklistRequest(title="Inbox"))
    tasks = list_tasks_payload(ListTasksRequest(tasklist_id="list-1", max_results=10))
    task = get_task_payload(GetTaskRequest(tasklist_id="list-1", task_id="task-1"))
    created_task = create_task_payload(CreateTaskRequest(tasklist_id="list-1", title="Ship it", due="2026-03-12"))
    updated_task = update_task_payload(UpdateTaskRequest(tasklist_id="list-1", task_id="task-1", notes="Done"))
    completed_task = complete_task_payload(
        CompleteTaskRequest(tasklist_id="list-1", task_id="task-1", completed_at="2026-03-12T10:00:00Z")
    )
    moved_task = move_task_payload(MoveTaskRequest(tasklist_id="list-1", task_id="task-1", previous="task-0"))
    deleted_task = delete_task_payload(DeleteTaskRequest(tasklist_id="list-1", task_id="task-1"))

    assert tasklists["kwargs"]["maxResults"] == 100
    assert tasklist["kwargs"]["tasklist"] == "list-1"
    assert created_tasklist["kwargs"]["body"]["title"] == "Inbox"
    assert tasks["kwargs"]["tasklist"] == "list-1"
    assert task["kwargs"]["task"] == "task-1"
    assert created_task["kwargs"]["body"]["due"] == "2026-03-12T00:00:00.000Z"
    assert updated_task["kwargs"]["body"]["notes"] == "Done"
    assert completed_task["kwargs"]["body"]["status"] == "completed"
    assert moved_task["kwargs"]["previous"] == "task-0"
    assert deleted_task == {"status": "deleted", "task_id": "task-1", "tasklist_id": "list-1"}


def test_tasks_tool_annotations():
    list_tool = anyio.run(_get_tool, tasks_mcp, "list_tasks")
    complete_tool = anyio.run(_get_tool, tasks_mcp, "complete_task")
    delete_tool = anyio.run(_get_tool, tasks_mcp, "delete_task")

    assert list_tool.annotations.readOnlyHint is True
    assert complete_tool.annotations.idempotentHint is True
    assert delete_tool.annotations.destructiveHint is True
