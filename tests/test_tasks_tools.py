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
    CreateTasklistRequest,
    DeleteTaskRequest,
    GetTaskRequest,
    GetTasklistRequest,
    ListTasksRequest,
    ListTasklistsRequest,
    create_task_payload,
    create_tasklist_payload,
    delete_task_payload,
    get_task_payload,
    get_tasklist_payload,
    list_tasks_payload,
    list_tasklists_payload,
    move_task_payload,
    patch_task_payload,
    update_task_body,
)
from mcp_google_workspace.tasks.presentation import task_envelope, tasks_digest, tasks_page
from mcp_google_workspace.server import workspace_mcp

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "google_payloads"
TIMEZONE = "America/Sao_Paulo"
NOW = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
# NOW in the account time zone, as recorded for a completion without an explicit time.
NOW_LOCAL = "2026-11-03T12:00:00-03:00"

TASKS_TOOLS = {
    "list_tasklists",
    "create_tasklist",
    "list_tasks",
    "tasks_digest",
    "create_task",
    "update_task",
    "delete_task",
}
RETIRED_TASKS_TOOLS = {"get_tasklist", "get_task", "complete_task", "move_task"}


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
        return _Exec({"kind": "tasklists.get", "kwargs": kwargs, "id": kwargs["tasklist"], "title": "Work"})

    def insert(self, **kwargs):
        self.calls.append(("insert", kwargs))
        return _Exec({"kind": "tasklists.insert", "kwargs": kwargs})


class _TasksApi:
    def __init__(self, moved_id=None):
        self.calls = []
        self.moved_id = moved_id

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return _Exec({"kind": "tasks.list", "kwargs": kwargs})

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return _Exec({"kind": "tasks.get", "kwargs": kwargs, "id": kwargs["task"], "title": "One"})

    def insert(self, **kwargs):
        self.calls.append(("insert", kwargs))
        return _Exec({"kind": "tasks.insert", "kwargs": kwargs})

    def patch(self, **kwargs):
        self.calls.append(("patch", kwargs))
        return _Exec({"kind": "tasks.patch", "kwargs": kwargs, "id": kwargs["task"], **kwargs["body"]})

    def move(self, **kwargs):
        self.calls.append(("move", kwargs))
        payload = {"kind": "tasks.move", "kwargs": kwargs, "parent": kwargs.get("parent")}
        if self.moved_id is not None:
            payload["id"] = self.moved_id
        return _Exec(payload)

    def delete(self, **kwargs):
        self.calls.append(("delete", kwargs))
        return _Exec({"kind": "tasks.delete", "kwargs": kwargs})


class _TasksService:
    def __init__(self, moved_id=None):
        self.tasklists_api = _TasklistsApi()
        self.tasks_api = _TasksApi(moved_id=moved_id)

    def tasklists(self):
        return self.tasklists_api

    def tasks(self):
        return self.tasks_api


async def _fixed_timezone():
    return TIMEZONE


def _fixed_now(timezone_name):
    return NOW.astimezone(pytz.timezone(timezone_name))


@pytest.fixture
def service(monkeypatch):
    fake = _TasksService()
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.tasks_service", lambda: fake)
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.resolve_user_timezone", _fixed_timezone)
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.user_now", _fixed_now)
    return fake


def _call(tool, arguments, *, raise_on_error=True):
    async def scenario():
        async with Client(tasks_mcp) as client:
            return await client.call_tool(tool, arguments, raise_on_error=raise_on_error)

    return anyio.run(scenario)


def _invalid(tool, arguments):
    result = _call(tool, arguments, raise_on_error=False)
    assert result.is_error is True
    assert result.structured_content["code"] == "invalid_input", result.structured_content
    return result.structured_content


def test_tasks_server_registers_expected_tools():
    tool_names = set(anyio.run(_list_tool_names, tasks_mcp))
    assert tool_names == TASKS_TOOLS
    assert not tool_names & RETIRED_TASKS_TOOLS


def test_tasks_request_normalization_and_validation():
    request = CreateTaskRequest(tasklist_id="list-1", title="Follow up", due="2026-03-12")
    assert request.due == "2026-03-12T00:00:00.000Z"

    with pytest.raises(ValueError, match="At least one change"):
        UpdateTaskRequest(tasklist_id="list-1", task_id="task-1")
    # A blank move field is no move.
    with pytest.raises(ValueError, match="At least one change"):
        UpdateTaskRequest(tasklist_id="list-1", task_id="task-1", parent="", previous="")

    completed = UpdateTaskRequest(tasklist_id="list-1", task_id="task-1", completed="2026-03-12")
    assert completed.patch_body() == {"completed": "2026-03-12T00:00:00.000Z"}
    assert completed.moves is False
    assert UpdateTaskRequest(tasklist_id="list-1", task_id="task-1", move_to_top=True).moves is True


@pytest.mark.parametrize(
    "arguments",
    [
        {"parent": "task-0", "destination_tasklist_id": "list-2"},
        {"previous": "task-0", "destination_tasklist_id": "list-2"},
        {"move_to_top": True, "parent": "task-0"},
        {"move_to_top": True, "previous": "task-0"},
        {"move_to_top": True, "destination_tasklist_id": "list-2"},
    ],
)
def test_update_task_rejects_invalid_move_combinations(arguments, service):
    with pytest.raises(ValueError):
        UpdateTaskRequest(tasklist_id="list-1", task_id="task-1", **arguments)

    error = _invalid("update_task", {"tasklist_id": "list-1", "task_id": "task-1", "title": "x", **arguments})

    assert error["code"] == "invalid_input"
    assert service.tasks_api.calls == []


def test_update_task_requires_a_change(service):
    _invalid("update_task", {"task_id": "task-1"})
    assert service.tasks_api.calls == []


def test_tasks_payload_helpers(service):
    tasklists = list_tasklists_payload(ListTasklistsRequest())
    tasklist = get_tasklist_payload(GetTasklistRequest(tasklist_id="list-1"))
    created_tasklist = create_tasklist_payload(CreateTasklistRequest(title="Inbox"))
    tasks = list_tasks_payload(ListTasksRequest(tasklist_id="list-1", max_results=10))
    task = get_task_payload(GetTaskRequest(tasklist_id="list-1", task_id="task-1"))
    created_task = create_task_payload(CreateTaskRequest(tasklist_id="list-1", title="Ship it", due="2026-03-12"))
    patched = patch_task_payload("list-1", "task-1", {"notes": "Done"})
    moved = move_task_payload(UpdateTaskRequest(tasklist_id="list-1", task_id="task-1", previous="task-0"))
    deleted_task = delete_task_payload(DeleteTaskRequest(tasklist_id="list-1", task_id="task-1"))

    assert tasklists["kwargs"]["maxResults"] == 100
    assert tasklist["kwargs"]["tasklist"] == "list-1"
    assert created_tasklist["kwargs"]["body"]["title"] == "Inbox"
    assert tasks["kwargs"]["tasklist"] == "list-1"
    assert task["kwargs"]["task"] == "task-1"
    assert created_task["kwargs"]["body"]["due"] == "2026-03-12T00:00:00.000Z"
    assert patched["kwargs"]["body"] == {"notes": "Done"}
    assert moved["kwargs"]["previous"] == "task-0"
    assert moved["kwargs"]["destinationTasklist"] is None
    assert deleted_task == {"status": "deleted", "task_id": "task-1", "tasklist_id": "list-1"}


def test_update_task_body_records_completion():
    done = UpdateTaskRequest(tasklist_id="l", task_id="t", status="completed")
    explicit = UpdateTaskRequest(tasklist_id="l", task_id="t", status="completed", completed="2026-03-12T10:00:00Z")
    reopened = UpdateTaskRequest(tasklist_id="l", task_id="t", status="needsAction")

    assert update_task_body(done, default_completed=NOW_LOCAL) == {"status": "completed", "completed": NOW_LOCAL}
    assert update_task_body(explicit, default_completed=NOW_LOCAL) == {
        "status": "completed",
        "completed": "2026-03-12T10:00:00Z",
    }
    assert update_task_body(reopened, default_completed=NOW_LOCAL) == {"status": "needsAction"}
    with pytest.raises(ValueError):
        update_task_body(done, default_completed=None)


def test_tasks_tool_annotations():
    list_tool = anyio.run(_get_tool, tasks_mcp, "list_tasks")
    update_tool = anyio.run(_get_tool, tasks_mcp, "update_task")
    delete_tool = anyio.run(_get_tool, tasks_mcp, "delete_task")

    assert list_tool.annotations.read_only_hint is True
    assert update_tool.annotations.idempotent_hint is True
    assert delete_tool.annotations.destructive_hint is True


# --- list_tasklists / list_tasks with an ID (formerly get_tasklist / get_task) ---


def test_list_tasklists_with_id_returns_that_one_list(service):
    result = _call("list_tasklists", {"tasklist_id": "list-1", "max_results": 5, "page_token": "ignored"})

    assert service.tasklists_api.calls == [("get", {"tasklist": "list-1"})]
    structured = result.structured_content
    assert structured["count"] == 1
    assert structured["next_page_token"] is None
    assert structured["has_more"] is False
    assert [item["id"] for item in structured["tasklists"]] == ["list-1"]
    assert structured["tasklists"][0]["title"] == "Work"
    assert structured["account_timezone"] == TIMEZONE


def test_list_tasklists_without_id_pages(service):
    _call("list_tasklists", {"max_results": 5, "page_token": "p2"})
    assert service.tasklists_api.calls == [("list", {"maxResults": 5, "pageToken": "p2"})]


@pytest.mark.parametrize(("arguments", "list_id"), [({}, "@default"), ({"tasklist_id": "list-1"}, "list-1")])
def test_list_tasks_with_task_id_returns_that_one_task(arguments, list_id, service):
    result = _call("list_tasks", {**arguments, "task_id": "task-9", "show_completed": False, "max_results": 3})

    assert service.tasks_api.calls == [("get", {"tasklist": list_id, "task": "task-9"})]
    structured = result.structured_content
    assert structured["tasklist_id"] == list_id
    assert structured["count"] == 1
    assert structured["next_page_token"] is None
    assert [task["id"] for task in structured["tasks"]] == ["task-9"]


class _FailingExec:
    def execute(self):
        raise HttpError(Response({"status": "404"}), b'{"error":{"message":"Not found"}}')


class _FailingTasksService:
    def tasklists(self):
        return self

    def tasks(self):
        return self

    def get(self, **kwargs):
        return _FailingExec()


def test_list_tasklists_with_id_returns_structured_error(monkeypatch):
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.tasks_service", _FailingTasksService)
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.resolve_user_timezone", _fixed_timezone)

    result = _call("list_tasklists", {"tasklist_id": "list-1"}, raise_on_error=False)

    assert result.is_error is True
    error = result.structured_content
    assert "404" in error["message"]
    assert error["code"] == "not_found"
    assert error["provider_status"] == 404
    assert error["details"]["context"]["tasklist_id"] == "list-1"


def test_list_tasks_with_task_id_returns_structured_error(monkeypatch):
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.tasks_service", _FailingTasksService)
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.resolve_user_timezone", _fixed_timezone)

    result = _call("list_tasks", {"task_id": "task-1"}, raise_on_error=False)

    assert result.is_error is True
    assert result.structured_content["code"] == "not_found"
    assert result.structured_content["details"]["context"] == {"tasklist_id": "@default", "task_id": "task-1"}


# --- update_task: completion (formerly complete_task) ---


def test_update_task_completes_now_in_the_account_time_zone(service):
    result = _call("update_task", {"tasklist_id": "list-1", "task_id": "task-1", "status": "completed"})

    assert service.tasks_api.calls == [
        ("patch", {"tasklist": "list-1", "task": "task-1", "body": {"status": "completed", "completed": NOW_LOCAL}})
    ]
    assert result.structured_content["status"] == "ok"
    assert result.structured_content["task"]["status"] == "completed"
    assert result.structured_content["task"]["completed_at"] == NOW_LOCAL


def test_update_task_completes_at_an_explicit_time(service):
    _call(
        "update_task",
        {"tasklist_id": "list-1", "task_id": "task-1", "status": "completed", "completed": "2026-03-12"},
    )

    assert service.tasks_api.calls[0][1]["body"] == {
        "status": "completed",
        "completed": "2026-03-12T00:00:00.000Z",
    }


# --- update_task: moves (formerly move_task) ---


@pytest.mark.parametrize(
    ("arguments", "parent", "previous"),
    [
        ({"previous": "task-0"}, None, "task-0"),
        ({"parent": "task-p"}, "task-p", None),
        ({"parent": "task-p", "previous": "task-s"}, "task-p", "task-s"),
        ({"move_to_top": True}, None, None),
    ],
)
def test_update_task_moves_within_the_list(arguments, parent, previous, service):
    result = _call("update_task", {"tasklist_id": "list-1", "task_id": "task-1", **arguments})

    assert service.tasks_api.calls == [
        (
            "move",
            {
                "tasklist": "list-1",
                "task": "task-1",
                "parent": parent,
                "previous": previous,
                "destinationTasklist": None,
            },
        )
    ]
    assert result.structured_content["status"] == "ok"
    assert result.structured_content["task"]["parent_id"] == parent


def test_update_task_moves_to_another_list(service):
    result = _call("update_task", {"task_id": "task-1", "destination_tasklist_id": "list-2"})

    assert service.tasks_api.calls == [
        (
            "move",
            {"tasklist": "@default", "task": "task-1", "parent": None, "previous": None, "destinationTasklist": "list-2"},
        )
    ]
    assert result.structured_content["status"] == "ok"


def test_update_task_moves_then_patches_on_the_destination_list(monkeypatch):
    fake = _TasksService(moved_id="task-1-moved")
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.tasks_service", lambda: fake)
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.resolve_user_timezone", _fixed_timezone)
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.user_now", _fixed_now)

    result = _call(
        "update_task",
        {
            "tasklist_id": "list-1",
            "task_id": "task-1",
            "destination_tasklist_id": "list-2",
            "title": "Renamed",
            "status": "completed",
        },
    )

    assert [call[0] for call in fake.tasks_api.calls] == ["move", "patch"]
    assert fake.tasks_api.calls[0][1]["destinationTasklist"] == "list-2"
    assert fake.tasks_api.calls[1][1] == {
        "tasklist": "list-2",
        "task": "task-1-moved",
        "body": {"title": "Renamed", "status": "completed", "completed": NOW_LOCAL},
    }
    assert result.structured_content["task"]["title"] == "Renamed"


def test_update_task_moves_then_patches_within_the_list(service):
    _call("update_task", {"tasklist_id": "list-1", "task_id": "task-1", "previous": "task-0", "notes": "n"})

    assert [call[0] for call in service.tasks_api.calls] == ["move", "patch"]
    assert service.tasks_api.calls[1][1] == {"tasklist": "list-1", "task": "task-1", "body": {"notes": "n"}}


class _PatchFailsTasksService(_TasksService):
    def __init__(self):
        super().__init__(moved_id="task-1")
        tasks_api = self.tasks_api

        def failing_patch(**kwargs):
            tasks_api.calls.append(("patch", kwargs))
            return _FailingExec()

        tasks_api.patch = failing_patch


def test_update_task_patch_failure_after_a_move_names_the_new_list(monkeypatch):
    fake = _PatchFailsTasksService()
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.tasks_service", lambda: fake)
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.resolve_user_timezone", _fixed_timezone)

    result = _call(
        "update_task",
        {"tasklist_id": "list-1", "task_id": "task-1", "destination_tasklist_id": "list-2", "title": "x"},
        raise_on_error=False,
    )

    assert result.is_error is True
    assert result.structured_content["details"]["context"] == {
        "tasklist_id": "list-2",
        "task_id": "task-1",
        "moved": True,
    }


# --- default task list on create / update / delete ---


def test_create_update_delete_default_to_the_default_list(service, monkeypatch):
    async def confirm(_ctx, _action, _message, **_kwargs):
        return True

    monkeypatch.setattr("mcp_google_workspace.tasks.tools.confirm_destructive_action", confirm)

    async def scenario():
        async with Client(tasks_mcp) as client:
            listed = {entry.name: entry for entry in await client.list_tools()}
            await client.call_tool("create_task", {"title": "New"})
            await client.call_tool("update_task", {"task_id": "task-1", "title": "Renamed"})
            deleted = await client.call_tool("delete_task", {"task_id": "task-1"})
            return listed, deleted

    listed, deleted = anyio.run(scenario)

    for tool in ("create_task", "update_task", "delete_task", "list_tasks", "tasks_digest"):
        schema = listed[tool].input_schema
        assert "tasklist_id" not in schema.get("required", []), tool
        assert schema["properties"]["tasklist_id"]["description"] == (
            "Task list ID from tasks_list_tasklists; omit for the account's default list."
        )
    assert [(name, kwargs["tasklist"]) for name, kwargs in service.tasks_api.calls] == [
        ("insert", "@default"),
        ("patch", "@default"),
        ("delete", "@default"),
    ]
    assert deleted.structured_content == {"status": "deleted", "task_id": "task-1", "tasklist_id": "@default"}


@pytest.mark.parametrize("tool", ["list_tasks", "tasks_digest"])
def test_omitted_tasklist_id_reads_the_default_list(tool, service):
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


# --- delete_task confirmation (unchanged gating) ---


@pytest.mark.parametrize("accepted", [True, False])
def test_delete_task_asks_for_confirmation(accepted, service, monkeypatch):
    asked = []

    async def confirm(ctx, action_name, message, **kwargs):
        asked.append((action_name, message, kwargs))
        return accepted

    monkeypatch.setattr("mcp_google_workspace.tasks.tools.confirm_destructive_action", confirm)

    result = _call("delete_task", {"tasklist_id": "list-1", "task_id": "task-1"})

    assert asked == [("delete_task", "Permanently delete task task-1?", {})]
    if accepted:
        assert service.tasks_api.calls == [("delete", {"tasklist": "list-1", "task": "task-1"})]
        assert result.structured_content["status"] == "deleted"
    else:
        assert service.tasks_api.calls == []
        assert result.structured_content == {"status": "cancelled", "task_id": "task-1", "tasklist_id": "list-1"}


# --- presentation ---


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
            "tasks_update_task",
            {"tasklist_id": "list-1", "task_id": "Y2xvc2VfY29udHJhY3RzOjE", "status": "completed"},
            "tasks_complete_task",
            "tasks_complete_task",
        ),
        (
            "tasks_update_task",
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


def test_tasks_lookups_by_id_through_the_server(monkeypatch):
    task = json.loads((FIXTURES / "tasks_get_task.json").read_text(encoding="utf-8"))
    tasklist = json.loads((FIXTURES / "tasks_insert_tasklist.json").read_text(encoding="utf-8"))
    task_golden = json.loads((FIXTURES / "golden" / "tasks_get_task.json").read_text(encoding="utf-8"))
    tasklist_golden = json.loads((FIXTURES / "golden" / "tasks_get_tasklist.json").read_text(encoding="utf-8"))
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.resolve_user_timezone", _fixed_timezone)
    monkeypatch.setattr("mcp_google_workspace.tasks.tools.user_now", _fixed_now)

    def call(payload, tool, arguments):
        monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _PayloadService(payload))

        async def scenario():
            async with Client(workspace_mcp) as client:
                return await client.call_tool(tool, arguments)

        return anyio.run(scenario).structured_content

    one_task = call(task, "tasks_list_tasks", {"task_id": task["id"]})
    one_list = call(tasklist, "tasks_list_tasklists", {"tasklist_id": tasklist["id"]})

    # The one item is exactly what the retired get_task / get_tasklist returned.
    assert one_task["tasks"] == [task_golden] and one_task["count"] == 1 and one_task["has_more"] is False
    assert one_list["tasklists"] == [tasklist_golden] and one_list["count"] == 1
