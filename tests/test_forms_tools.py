import anyio

from mcp_google_workspace.forms.server import forms_mcp
from mcp_google_workspace.forms.tools import (
    BatchUpdateFormRequest,
    CreateFormRequest,
    GetFormRequest,
    GetFormResponseRequest,
    ListFormResponsesRequest,
    SetFormPublishSettingsRequest,
    batch_update_form_payload,
    build_choice_question_item_request,
    build_text_question_item_request,
    create_form_payload,
    get_form_payload,
    get_form_response_payload,
    list_form_responses_payload,
    set_form_publish_settings_payload,
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


class _ResponsesApi:
    def __init__(self, parent):
        self.parent = parent

    def list(self, **kwargs):
        self.parent.calls.append(("responses.list", kwargs))
        return _Exec({"kind": "forms.responses.list", "kwargs": kwargs})

    def get(self, **kwargs):
        self.parent.calls.append(("responses.get", kwargs))
        return _Exec({"kind": "forms.responses.get", "kwargs": kwargs})


class _FormsApi:
    def __init__(self):
        self.calls = []
        self.responses_api = _ResponsesApi(self)

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return _Exec({"kind": "forms.get", "kwargs": kwargs})

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return _Exec({"kind": "forms.create", "kwargs": kwargs})

    def batchUpdate(self, **kwargs):
        self.calls.append(("batchUpdate", kwargs))
        return _Exec({"kind": "forms.batchUpdate", "kwargs": kwargs})

    def setPublishSettings(self, **kwargs):
        self.calls.append(("setPublishSettings", kwargs))
        return _Exec({"kind": "forms.setPublishSettings", "kwargs": kwargs})

    def responses(self):
        return self.responses_api


class _FormsService:
    def __init__(self):
        self.forms_api = _FormsApi()

    def forms(self):
        return self.forms_api


def test_forms_server_registers_expected_tools():
    tool_names = anyio.run(_list_tool_names, forms_mcp)
    assert "get_form" in tool_names
    assert "create_form" in tool_names
    assert "batch_update_form" in tool_names
    assert "set_form_publish_settings" in tool_names
    assert "list_form_responses" in tool_names
    assert "get_form_response" in tool_names


def test_forms_request_builders():
    text_request = build_text_question_item_request("What happened?", index=1, required=True)
    choice_request = build_choice_question_item_request("Priority", ["High", "Low"], question_type="DROP_DOWN")

    assert text_request["createItem"]["item"]["questionItem"]["question"]["required"] is True
    assert choice_request["createItem"]["item"]["questionItem"]["question"]["choiceQuestion"]["type"] == "DROP_DOWN"


def test_forms_payload_helpers(monkeypatch):
    service = _FormsService()
    monkeypatch.setattr("mcp_google_workspace.forms.tools.forms_service", lambda: service)

    form = get_form_payload(GetFormRequest(form_id="form-1"))
    created = create_form_payload(CreateFormRequest(title="Retro", document_title="Retro Doc", unpublished=True))
    updated = batch_update_form_payload(
        BatchUpdateFormRequest(form_id="form-1", requests=[build_text_question_item_request("Summary")])
    )
    publish = set_form_publish_settings_payload(
        SetFormPublishSettingsRequest(form_id="form-1", publish_settings={"isPublished": True})
    )
    responses = list_form_responses_payload(ListFormResponsesRequest(form_id="form-1", page_size=10))
    response = get_form_response_payload(GetFormResponseRequest(form_id="form-1", response_id="resp-1"))

    assert form["kwargs"]["formId"] == "form-1"
    assert created["kwargs"]["unpublished"] is True
    assert updated["kwargs"]["body"]["requests"][0]["createItem"]["item"]["title"] == "Summary"
    assert publish["kwargs"]["body"]["publishSettings"]["isPublished"] is True
    assert responses["kwargs"]["pageSize"] == 10
    assert response["kwargs"]["responseId"] == "resp-1"


def test_forms_tool_annotations():
    get_tool = anyio.run(_get_tool, forms_mcp, "get_form")
    publish_tool = anyio.run(_get_tool, forms_mcp, "set_form_publish_settings")

    assert get_tool.annotations.readOnlyHint is True
    assert publish_tool.annotations.idempotentHint is True
