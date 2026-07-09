from mcp_google_workspace.keep.client import normalize_note_name
from mcp_google_workspace.keep.schemas import ChecklistItem, CreateNoteRequest, UpdateNoteRequest
from mcp_google_workspace.keep.tools import _build_note_body
from mcp_google_workspace.keep.presentation import note_envelope


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
    result = note_envelope({"name": "notes/a", "title": "Plan", "body": {"list": {"listItems": [{"text": {"text": "Ship"}, "checked": True}, {"text": {"text": "Test"}, "checked": False}]}}})
    assert result["checklist"] == {"total": 2, "completed": 1}
    assert result["text"] == "Ship\nTest"
