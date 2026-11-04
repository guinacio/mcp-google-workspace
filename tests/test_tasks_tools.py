import json
from datetime import UTC, datetime
from pathlib import Path

import anyio
import pytest
import pytz
from fastmcp import Client
from googleapiclient.errors import HttpError
from httplib2 import Response

import mcp_google_workspace.auth.google_auth as google_auth

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
from mcp_google_workspace.tasks.presentation import task_envelope, tasks_digest, tasks_page
from mcp_google_workspace.server import workspace_mcp

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "google_payloads"
TIMEZONE = "America/Sao_Paulo"
NOW = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)


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

    assert list_tool.annotations.read_only_hint is True
    assert complete_tool.annotations.idempotent_hint is True
    assert delete_tool.annotations.destructive_hint is True


def test_task_envelope_and_digest_surface_actionable_state():
    task = {
        "id": "task-1",
        "title": "Reply",
        "due": "2026-07-01T00:00:00.000Z",
        "notes": "Send a concise update.",
        "updated": "2026-07-08T12:00:00.000Z",
        "selfLink": "https://www.googleapis.com/tasks/v1/lists/list-real/tasks/task-1",
    }
    now = datetime(2026, 7, 9, tzinfo=UTC)

    envelope = task_envelope(task, now=now, account_timezone=TIMEZONE)
    digest = tasks_digest(
        {"items": [task, {"id": "task-2", "title": "Someday"}]},
        tasklist_id="@default",
        now=now,
        account_timezone=TIMEZONE,
    )

    assert envelope["is_overdue"] is True
    assert envelope["due_date"] == "2026-07-01"
    assert envelope["updated_at"] == "2026-07-08T09:00:00-03:00"
    assert envelope["notes_snippet"] == "Send a concise update."
    assert "source_due" not in envelope and "due_is_date_only" not in envelope
    assert digest["overdue"][0]["id"] == "task-1"
    assert digest["unscheduled"][0]["id"] == "task-2"
    assert digest["count"] == 2
    # The real list ID is read from the tasks' selfLink when '@default' was asked for.
    assert digest["tasklist_id"] == "list-real"


def test_task_overdue_uses_the_account_date_not_utc():
    # 01:00 UTC on Nov 4 is still Nov 3 in Sao Paulo: a task due Nov 3 is not overdue yet.
    late_utc = datetime(2026, 11, 4, 1, 0, tzinfo=UTC)
    task = {"id": "t", "due": "2026-11-03T00:00:00.000Z", "completed": "2026-11-04T00:30:00.000Z"}

    envelope = task_envelope(task, now=late_utc, account_timezone=TIMEZONE)

    assert envelope["is_overdue"] is False
    assert envelope["completed_at"] == "2026-11-03T21:30:00-03:00"


def test_tasks_page_echoes_the_requested_list_when_it_is_empty():
    page = tasks_page({}, tasklist_id="@default", now=NOW, account_timezone=TIMEZONE)
    assert page == {
        "tasklist_id": "@default",
        "tasks": [],
        "count": 0,
        "next_page_token": None,
        "account_timezone": TIMEZONE,
    }


async def _fixed_timezone():
    return TIMEZONE


def _fixed_now(timezone_name):
    return NOW.astimezone(pytz.timezone(timezone_name))


@pytest.mark.parametrize("tool", ["list_tasks", "tasks_digest"])
def test_omitted_tasklist_id_reads_the_default_list(tool, monkeypatch):
    service = _TasksService()
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.tasks_service", lambda: service)
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.resolve_user_timezone", _fixed_timezone)

    async def scenario():
        async with Client(tasks_mcp) as client:
            listed = {entry.name: entry for entry in await client.list_tools()}
            result = await client.call_tool(tool, {})
            return listed[tool], result

    listed, result = anyio.run(scenario)

    assert "tasklist_id" not in listed.input_schema.get("required", [])
    assert "default list" in listed.input_schema["properties"]["tasklist_id"]["description"]
    assert service.tasks_api.calls[-1][1]["tasklist"] == "@default"
    assert result.structured_content["tasklist_id"] == "@default"


class _PayloadService:
    """A Google client stub whose every ``execute()`` returns one payload."""

    def __init__(self, payload):
        self._payload = payload

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return self

    def __call__(self, *_args, **_kwargs):
        return self

    def execute(self, *_args, **_kwargs):
        return json.loads(json.dumps(self._payload))


@pytest.mark.parametrize(
    ("tool", "arguments", "fixture", "golden"),
    [
        ("tasks_list_tasks", {}, "tasks_list_tasks", "tasks_list_tasks"),
        ("tasks_tasks_digest", {}, "tasks_list_tasks", "tasks_tasks_digest"),
        ("tasks_list_tasklists", {}, "tasks_list_tasklists", "tasks_list_tasklists"),
        (
            "tasks_create_task",
            {"tasklist_id": "list-1", "title": "File the tax return", "due": "2026-11-14"},
            "tasks_insert_task",
            "tasks_create_task",
        ),
        (
            "tasks_complete_task",
            {"tasklist_id": "list-1", "task_id": "Y2xvc2VfY29udHJhY3RzOjE"},
            "tasks_complete_task",
            "tasks_complete_task",
        ),
        (
            "tasks_move_task",
            {"tasklist_id": "list-1", "task_id": "Ym9va19yb29tOjM", "destination_tasklist_id": "list-2"},
            "tasks_move_task",
            "tasks_move_task",
        ),
        ("tasks_create_tasklist", {"title": "Renovation"}, "tasks_insert_tasklist", "tasks_create_tasklist"),
    ],
)
def test_tasks_tools_return_the_normalized_shape_through_the_server(tool, arguments, fixture, golden, monkeypatch):
    payload = json.loads((FIXTURES / f"{fixture}.json").read_text(encoding="utf-8"))
    expected = json.loads((FIXTURES / "golden" / f"{golden}.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _PayloadService(payload))
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.resolve_user_timezone", _fixed_timezone)
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.user_now", _fixed_now)

    async def scenario():
        async with Client(workspace_mcp) as client:
            return await client.call_tool(tool, arguments)

    result = anyio.run(scenario)

    assert result.is_error is False, result.content
    structured = dict(result.structured_content)
    for added in ("has_more", "next_page_token", "fetched_at"):  # list / digest wrapper fields
        if added not in expected:
            structured.pop(added, None)
    assert structured == expected


def test_move_task_rejects_reposition_combined_with_destination():
    with pytest.raises(ValueError):
        MoveTaskRequest(
            tasklist_id="list-1", task_id="task-1", parent="task-0", destination_tasklist_id="list-2"
        )
    with pytest.raises(ValueError):
        MoveTaskRequest(
            tasklist_id="list-1", task_id="task-1", previous="task-0", destination_tasklist_id="list-2"
        )
    moved = MoveTaskRequest(tasklist_id="list-1", task_id="task-1", destination_tasklist_id="list-2")
    assert moved.destination_tasklist_id == "list-2"


class _FailingExec:
    def execute(self):
        raise HttpError(Response({"status": "404"}), b'{"error":{"message":"Not found"}}')


class _FailingTasksService:
    def tasklists(self):
        return self

    def get(self, **kwargs):
        return _FailingExec()


def test_get_tasklist_tool_returns_structured_error(monkeypatch):
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.tasks_service", _FailingTasksService)
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.resolve_user_timezone", _fixed_timezone)

    async def scenario():
        async with Client(tasks_mcp) as client:
            result = await client.call_tool(
                "get_tasklist", {"tasklist_id": "list-1"}, raise_on_error=False
            )
            assert result.is_error is True
            return result.structured_content

    # W5: a Google API failure is an isError tool result with the error envelope
    # (was a successful result carrying an "error" dict).
    result = anyio.run(scenario)
    assert "404" in result["message"]
    assert result["code"] == "not_found"
    assert result["provider_status"] == 404
    assert result["details"]["context"]["tasklist_id"] == "list-1"
