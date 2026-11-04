import anyio

from mcp_google_workspace.forms.server import forms_mcp
from mcp_google_workspace.forms.presentation import question_titles, response_envelope
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

    assert get_tool.annotations.read_only_hint is True
    assert publish_tool.annotations.idempotent_hint is True


def test_response_envelope_resolves_question_ids_to_titles():
    titles = question_titles({"items": [{"title": "Priority", "questionItem": {"question": {"questionId": "q1"}}}]})
    result = response_envelope(
        {"responseId": "r1", "answers": {"q1": {"textAnswers": {"answers": [{"value": "High"}]}}}},
        titles,
        account_timezone="America/Sao_Paulo",
    )
    assert result["answers"] == [{"question_id": "q1", "question": "Priority", "values": ["High"]}]


# --- Normalized responses (2.0.0) -------------------------------------------------

import inspect  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

from fastmcp import Client  # noqa: E402

import mcp_google_workspace.auth.google_auth as google_auth  # noqa: E402
from mcp_google_workspace.common.output_schemas import infer_tool_output_schema  # noqa: E402
from mcp_google_workspace.forms.presentation import (  # noqa: E402
    OTHER_OPTION,
    batch_update_form_envelope,
    form_envelope,
    form_item_envelope,
    publish_settings_envelope,
)
from mcp_google_workspace.server import workspace_mcp  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "google_payloads"
_TIMEZONE = "America/Sao_Paulo"


def _fixture(name):
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


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


def test_response_envelope_carries_no_per_item_timezone():
    result = response_envelope(
        {"responseId": "r1", "createTime": "2026-11-03T12:00:00Z", "lastSubmittedTime": "2026-11-03T12:00:00Z"},
        {},
        account_timezone=_TIMEZONE,
    )
    assert "timezone" not in result
    assert result["created_at"] == "2026-11-03T09:00:00-03:00"


def test_form_item_types_and_options():
    def question(**kind):
        return form_item_envelope({"itemId": "i", "questionItem": {"question": {"questionId": "q", **kind}}})

    assert question(textQuestion={})["question_type"] == "short_text"
    assert question(textQuestion={"paragraph": True})["question_type"] == "paragraph"
    checkbox = question(choiceQuestion={"type": "CHECKBOX", "options": [{"value": "A"}, {"isOther": True}]})
    assert checkbox["question_type"] == "checkboxes"
    assert checkbox["options"] == ["A", OTHER_OPTION]
    assert checkbox["required"] is False
    assert question(timeQuestion={"duration": True})["question_type"] == "duration"
    assert question(dateQuestion={"includeTime": True})["question_type"] == "date_time"
    scale = question(scaleQuestion={"low": 0, "high": 10})
    assert scale["options"] == {"low": 0, "high": 10, "low_label": None, "high_label": None}
    assert form_item_envelope({"itemId": "p", "pageBreakItem": {}})["kind"] == "page_break"
    assert form_item_envelope({"itemId": "x"})["kind"] is None


def test_form_publish_state_is_null_for_legacy_forms():
    legacy = form_envelope({"formId": "f1", "info": {"title": "T"}})
    assert legacy["published"] is None and legacy["accepting_responses"] is None
    assert legacy["items"] == [] and legacy["count"] == 0
    closed = publish_settings_envelope({"formId": "f1", "publishSettings": {"publishState": {}}}, form_id="f1")
    assert closed == {"status": "ok", "form_id": "f1", "published": False, "accepting_responses": False}


def test_batch_update_form_envelope_takes_the_form_id_from_the_request():
    result = batch_update_form_envelope({"replies": [{}], "writeControl": {"requiredRevisionId": "00000005"}}, form_id="f1")
    assert result == {"status": "ok", "form_id": "f1", "revision_id": "00000005", "replies": [{}], "count": 1, "form": None}


def test_get_form_end_to_end_returns_the_normalized_shape(monkeypatch):
    payload = _fixture("forms_get_form.json")
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _PayloadService(payload))

    async def scenario():
        async with Client(workspace_mcp) as client:
            return await client.call_tool("forms_get_form", {"form_id": payload["formId"]})

    result = anyio.run(scenario)
    assert result.is_error is False
    structured = result.structured_content
    assert structured["form_id"] == payload["formId"]
    assert structured["count"] == len(payload["items"])
    assert structured["items"][0]["question_id"] == "7b2d9e10"
    assert "usp=" not in structured["responder_url"]
    for raw_field in ("formId", "info", "settings", "publishSettings", "responderUri"):
        assert raw_field not in structured


def test_list_form_responses_end_to_end_has_one_top_level_time_zone(monkeypatch):
    payload = _fixture("forms_list_form_responses.json")
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _PayloadService(payload))

    async def fake_timezone():
        return _TIMEZONE

    monkeypatch.setattr("mcp_google_workspace.forms.tools.resolve_user_timezone", fake_timezone)

    async def scenario():
        async with Client(workspace_mcp) as client:
            return await client.call_tool(
                "forms_list_form_responses", {"form_id": "form-1", "enrich_questions": False}
            )

    result = anyio.run(scenario)
    assert result.is_error is False
    structured = result.structured_content
    assert structured["account_timezone"] == _TIMEZONE
    assert structured["count"] == 2
    assert all("timezone" not in response for response in structured["responses"])


def test_forms_tools_return_inferable_literal_shapes():
    async def tools():
        return {tool.name: tool for tool in await forms_mcp.list_tools(run_middleware=False)}

    listed = anyio.run(tools)
    expected = {
        "get_form": {"form_id", "title", "responder_url", "revision_id", "published", "accepting_responses", "items", "count"},
        "create_form": {"status", "form_id", "title", "document_title", "responder_url", "revision_id"},
        "batch_update_form": {"status", "form_id", "revision_id", "replies", "count", "form"},
        "set_form_publish_settings": {"status", "form_id", "published", "accepting_responses"},
        "list_form_responses": {"form_id", "responses", "next_page_token", "count", "account_timezone"},
    }
    for name, keys in expected.items():
        schema = infer_tool_output_schema(inspect.unwrap(listed[name].fn))
        assert schema is not None, name
        assert keys <= set(schema["properties"]), (name, sorted(schema["properties"]))
