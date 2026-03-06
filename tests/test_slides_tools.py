import anyio

from mcp_google_workspace.slides.server import slides_mcp
from mcp_google_workspace.slides.tools import (
    BatchUpdatePresentationRequest,
    CreatePresentationRequest,
    GetPresentationRequest,
    GetSlidePageRequest,
    GetSlideThumbnailRequest,
    ReplaceTextInPresentationRequest,
    batch_update_presentation_payload,
    build_replace_all_text_request,
    create_presentation_payload,
    get_presentation_payload,
    get_slide_page_payload,
    get_slide_thumbnail_payload,
    replace_text_in_presentation_payload,
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


class _PagesApi:
    def __init__(self, parent):
        self.parent = parent

    def get(self, **kwargs):
        self.parent.calls.append(("pages.get", kwargs))
        return _Exec({"kind": "presentations.pages.get", "kwargs": kwargs})

    def getThumbnail(self, **kwargs):
        self.parent.calls.append(("pages.getThumbnail", kwargs))
        return _Exec({"kind": "presentations.pages.getThumbnail", "kwargs": kwargs})


class _PresentationsApi:
    def __init__(self):
        self.calls = []
        self.pages_api = _PagesApi(self)

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return _Exec({"kind": "presentations.get", "kwargs": kwargs})

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return _Exec({"kind": "presentations.create", "kwargs": kwargs})

    def batchUpdate(self, **kwargs):
        self.calls.append(("batchUpdate", kwargs))
        return _Exec({"kind": "presentations.batchUpdate", "kwargs": kwargs})

    def pages(self):
        return self.pages_api


class _SlidesService:
    def __init__(self):
        self.presentations_api = _PresentationsApi()

    def presentations(self):
        return self.presentations_api


def test_slides_server_registers_expected_tools():
    tool_names = anyio.run(_list_tool_names, slides_mcp)
    assert "get_presentation" in tool_names
    assert "create_presentation" in tool_names
    assert "replace_text_in_presentation" in tool_names
    assert "get_slide_page" in tool_names
    assert "get_slide_thumbnail" in tool_names
    assert "batch_update_presentation" in tool_names


def test_slides_request_builder():
    request = build_replace_all_text_request("Hello", "Hi", match_case=True)
    assert request == {
        "replaceAllText": {
            "containsText": {"text": "Hello", "matchCase": True},
            "replaceText": "Hi",
        }
    }


def test_slides_payload_helpers(monkeypatch):
    service = _SlidesService()
    monkeypatch.setattr("mcp_google_workspace.slides.tools.slides_service", lambda: service)

    presentation = get_presentation_payload(GetPresentationRequest(presentation_id="deck-1"))
    created = create_presentation_payload(CreatePresentationRequest(title="Quarterly Review"))
    replaced = replace_text_in_presentation_payload(
        ReplaceTextInPresentationRequest(
            presentation_id="deck-1",
            contains_text="TODO",
            replace_text="DONE",
        )
    )
    page = get_slide_page_payload(GetSlidePageRequest(presentation_id="deck-1", page_object_id="g1"))
    thumbnail = get_slide_thumbnail_payload(
        GetSlideThumbnailRequest(
            presentation_id="deck-1",
            page_object_id="g1",
            mime_type="JPEG",
            thumbnail_size="SMALL",
        )
    )
    updated = batch_update_presentation_payload(
        BatchUpdatePresentationRequest(presentation_id="deck-1", requests=[{"createSlide": {}}])
    )

    assert presentation["kwargs"]["presentationId"] == "deck-1"
    assert created["kwargs"]["body"]["title"] == "Quarterly Review"
    assert replaced["kwargs"]["body"]["requests"][0]["replaceAllText"]["replaceText"] == "DONE"
    assert page["kwargs"]["pageObjectId"] == "g1"
    assert thumbnail["kwargs"]["thumbnailProperties.mimeType"] == "JPEG"
    assert updated["kwargs"]["body"]["requests"] == [{"createSlide": {}}]


def test_slides_tool_annotations():
    get_tool = anyio.run(_get_tool, slides_mcp, "get_presentation")
    replace_tool = anyio.run(_get_tool, slides_mcp, "replace_text_in_presentation")

    assert get_tool.annotations.readOnlyHint is True
    assert replace_tool.annotations.idempotentHint is True
