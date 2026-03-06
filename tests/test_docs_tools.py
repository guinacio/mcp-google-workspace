import anyio

from mcp_google_workspace.docs.server import docs_mcp
from mcp_google_workspace.docs.tools import (
    AppendDocumentTextRequest,
    BatchUpdateDocumentRequest,
    CreateDocumentRequest,
    GetDocumentRequest,
    ReplaceDocumentTextRequest,
    append_document_text_payload,
    batch_update_document_payload,
    build_append_text_requests,
    build_replace_text_requests,
    create_document_payload,
    get_document_payload,
    replace_document_text_payload,
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


class _DocumentsApi:
    def __init__(self):
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return _Exec({"kind": "documents.get", "kwargs": kwargs})

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return _Exec({"kind": "documents.create", "kwargs": kwargs})

    def batchUpdate(self, **kwargs):
        self.calls.append(("batchUpdate", kwargs))
        return _Exec({"kind": "documents.batchUpdate", "kwargs": kwargs})


class _DocsService:
    def __init__(self):
        self.documents_api = _DocumentsApi()

    def documents(self):
        return self.documents_api


def test_docs_server_registers_expected_tools():
    tool_names = anyio.run(_list_tool_names, docs_mcp)
    assert "get_document" in tool_names
    assert "create_document" in tool_names
    assert "append_document_text" in tool_names
    assert "replace_document_text" in tool_names
    assert "batch_update_document" in tool_names


def test_docs_request_builders():
    assert build_append_text_requests("Hello") == [{"insertText": {"endOfSegmentLocation": {}, "text": "Hello"}}]
    assert build_replace_text_requests("old", "new", match_case=True) == [
        {
            "replaceAllText": {
                "containsText": {"text": "old", "matchCase": True},
                "replaceText": "new",
            }
        }
    ]


def test_docs_payload_helpers(monkeypatch):
    service = _DocsService()
    monkeypatch.setattr("mcp_google_workspace.docs.tools.docs_service", lambda: service)

    fetched = get_document_payload(GetDocumentRequest(document_id="doc-1", include_tabs_content=True))
    created = create_document_payload(CreateDocumentRequest(title="Sprint Notes"))
    appended = append_document_text_payload(AppendDocumentTextRequest(document_id="doc-1", text="\nNext item"))
    replaced = replace_document_text_payload(
        ReplaceDocumentTextRequest(document_id="doc-1", contains_text="TODO", replace_text="DONE")
    )
    updated = batch_update_document_payload(
        BatchUpdateDocumentRequest(document_id="doc-1", requests=[{"updateDocumentStyle": {}}])
    )

    assert fetched["kwargs"]["documentId"] == "doc-1"
    assert created["kwargs"]["body"]["title"] == "Sprint Notes"
    assert appended["kwargs"]["body"]["requests"][0]["insertText"]["text"] == "\nNext item"
    assert replaced["kwargs"]["body"]["requests"][0]["replaceAllText"]["replaceText"] == "DONE"
    assert updated["kwargs"]["body"]["requests"] == [{"updateDocumentStyle": {}}]


def test_docs_tool_annotations():
    get_tool = anyio.run(_get_tool, docs_mcp, "get_document")
    replace_tool = anyio.run(_get_tool, docs_mcp, "replace_document_text")

    assert get_tool.annotations.readOnlyHint is True
    assert replace_tool.annotations.idempotentHint is True
