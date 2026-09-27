import anyio
from fastmcp import Client
from googleapiclient.errors import HttpError
from httplib2 import Response

from mcp_google_workspace.keep.client import normalize_note_name
from mcp_google_workspace.keep.schemas import ChecklistItem, CreateNoteRequest, UpdateNoteRequest
from mcp_google_workspace.keep.server import keep_mcp
from mcp_google_workspace.keep.tools import _build_note_body
from mcp_google_workspace.keep.presentation import note_envelope


async def _list_tools(server):
    return await server.list_tools(run_middleware=False)


def test_normalize_note_name():
    assert normalize_note_name("notes/abc") == "notes/abc"
    assert normalize_note_name("abc") == "notes/abc"


def test_build_note_body_text():
    request = CreateNoteRequest(title="A", text_body="Hello")
    body = _build_note_body(request)
    assert body["title"] == "A"
    assert body["body"]["text"]["text"] == "Hello"


def test_build_note_body_checklist():
    request = UpdateNoteRequest(
        note_name="notes/x",
        checklist_items=[ChecklistItem(text="Task 1", checked=False)],
    )
    body = _build_note_body(request)
    assert body["body"]["list"]["listItems"][0]["text"]["text"] == "Task 1"


def test_note_envelope_surfaces_preview_and_checklist_progress():
    result = note_envelope(
        {"name": "notes/a", "title": "Plan", "body": {"list": {"listItems": [{"text": {"text": "Ship"}, "checked": True}, {"text": {"text": "Test"}, "checked": False}]}}},
        account_timezone="America/Sao_Paulo",
    )
    assert result["checklist"] == {"total": 2, "completed": 1}
    assert result["text"] == "Ship\nTest"


def test_keep_tools_all_have_descriptions():
    tools = anyio.run(_list_tools, keep_mcp)

    # W2: 18 -> 17 after the owner-approved removal of summarize_note.
    assert len(tools) == 17
    assert all(tool.description for tool in tools)


class _FailingExec:
    def execute(self):
        raise HttpError(Response({"status": "404"}), b'{"error":{"message":"Not found"}}')


class _FailingKeepService:
    def notes(self):
        return self

    def get(self, **_kwargs):
        return _FailingExec()


def test_keep_tool_returns_structured_provider_error(monkeypatch):
    async def fake_timezone():
        return "UTC"

    monkeypatch.setattr(
        "mcp_google_workspace.keep.tools.keep_service",
        _FailingKeepService,
    )
    monkeypatch.setattr(
        "mcp_google_workspace.keep.tools.resolve_user_timezone",
        fake_timezone,
    )

    async def scenario():
        async with Client(keep_mcp) as client:
            result = await client.call_tool(
                "get_note",
                {"request": {"note_name": "missing"}},
                raise_on_error=False,
            )
            assert result.is_error is True
            return result.structured_content

    # W5: a Google API failure is an isError tool result with the error envelope
    # (was a successful result carrying an "error" dict).
    result = anyio.run(scenario)
    assert result["code"] == "not_found"
    assert result["provider_status"] == 404
    assert result["details"]["context"]["note_name"] == "notes/missing"
