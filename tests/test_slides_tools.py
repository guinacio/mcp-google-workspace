import anyio

from mcp_google_workspace.slides.server import slides_mcp
from mcp_google_workspace.slides.tools import (
    BatchUpdatePresentationRequest,
    CreatePresentationRequest,
    GetSlideThumbnailRequest,
    ReadPresentationRequest,
    ReplaceTextInPresentationRequest,
    batch_update_presentation_payload,
    build_replace_all_text_request,
    create_presentation_payload,
    get_slide_thumbnail_payload,
    read_presentation_payload,
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
    assert "read_presentation" in tool_names
    assert "create_presentation" in tool_names
    assert "replace_text_in_presentation" in tool_names
    assert "get_presentation" not in tool_names
    assert "get_slide_page" not in tool_names
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

    presentation = read_presentation_payload(ReadPresentationRequest(presentation_id="deck-1", slide_ids=["g1"]))
    created = create_presentation_payload(CreatePresentationRequest(title="Quarterly Review"))
    replaced = replace_text_in_presentation_payload(
        ReplaceTextInPresentationRequest(
            presentation_id="deck-1",
            contains_text="TODO",
            replace_text="DONE",
        )
    )
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

    # One presentations.get serves the outline and the slide detail alike.
    assert presentation["kwargs"] == {"presentationId": "deck-1"}
    assert created["kwargs"]["body"]["title"] == "Quarterly Review"
    assert replaced["kwargs"]["body"]["requests"][0]["replaceAllText"]["replaceText"] == "DONE"
    assert [call[0] for call in service.presentations_api.calls] == [
        "get", "create", "batchUpdate", "pages.getThumbnail", "batchUpdate",
    ]
    assert thumbnail["kwargs"]["thumbnailProperties.mimeType"] == "JPEG"
    assert updated["kwargs"]["body"]["requests"] == [{"createSlide": {}}]


def test_slides_tool_annotations():
    get_tool = anyio.run(_get_tool, slides_mcp, "read_presentation")
    replace_tool = anyio.run(_get_tool, slides_mcp, "replace_text_in_presentation")

    assert get_tool.annotations.read_only_hint is True
    assert replace_tool.annotations.idempotent_hint is True


# --- Normalized responses (2.0.0) -------------------------------------------------

import inspect  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

from fastmcp import Client  # noqa: E402

import mcp_google_workspace.auth.google_auth as google_auth  # noqa: E402
from mcp_google_workspace.common.output_schemas import infer_tool_output_schema  # noqa: E402
from mcp_google_workspace.server import workspace_mcp  # noqa: E402
from mcp_google_workspace.slides.presentation import (  # noqa: E402
    batch_update_presentation_envelope,
    page_envelope,
    presentation_envelope,
    replace_text_envelope,
    text_content,
    to_points,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "google_payloads"


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


def test_units_convert_emu_and_pt_to_points():
    assert to_points(9144000, "EMU") == 720.0
    assert to_points(12700, "EMU") == 1.0
    assert to_points(18, "PT") == 18.0
    assert to_points(None, "EMU") == 0.0


def test_text_content_joins_runs_and_soft_breaks():
    text = {"textElements": [
        {"paragraphMarker": {}},
        {"textRun": {"content": "Line one\u000bstill one\n"}},
        {"autoText": {"type": "SLIDE_NUMBER", "content": "3"}},
        {"textRun": {"content": "\n"}},
    ]}
    assert text_content(text) == "Line one\nstill one\n3"
    assert text_content({"textElements": [{"textRun": {"content": "\n"}}]}) is None
    assert text_content(None) is None


def test_grouped_element_geometry_composes_the_group_transform():
    page = {
        "objectId": "s1",
        "pageType": "SLIDE",
        "pageElements": [{
            "objectId": "grp",
            "transform": {"scaleX": 2, "scaleY": 2, "translateX": 1270000, "translateY": 0, "unit": "EMU"},
            "elementGroup": {"children": [{
                "objectId": "child",
                "size": {"width": {"magnitude": 10, "unit": "PT"}, "height": {"magnitude": 5, "unit": "PT"}},
                "transform": {"scaleX": 1, "scaleY": 1, "translateX": 10, "translateY": 20, "unit": "PT"},
                "shape": {"shapeType": "RECTANGLE"},
            }]},
        }],
    }
    elements = page_envelope(page)["elements"]
    assert [element["object_id"] for element in elements] == ["grp", "child"]
    assert elements[0]["size"] is None
    assert elements[1]["position"] == {"x_pt": 120.0, "y_pt": 40.0}
    assert elements[1]["size"] == {"width_pt": 20.0, "height_pt": 10.0}


def test_presentation_envelope_handles_minimal_payloads():
    result = presentation_envelope({"presentationId": "deck-1"})
    assert result["slides"] == [] and result["count"] == 0
    assert result["page_size"] is None
    assert result["url"] == "https://docs.google.com/presentation/d/deck-1/edit"
    slide = presentation_envelope({"presentationId": "deck-1", "slides": [{"objectId": "s1"}]})["slides"][0]
    assert slide == {"object_id": "s1", "index": 0, "layout": None, "title": None, "text": None, "notes": None, "elements": []}


def test_replace_and_batch_update_envelopes_default_missing_fields():
    assert replace_text_envelope({}, presentation_id="deck-1") == {
        "status": "ok", "presentation_id": "deck-1", "occurrences_changed": 0,
    }
    assert batch_update_presentation_envelope({"replies": [{}]}, presentation_id="deck-1") == {
        "status": "ok", "presentation_id": "deck-1", "revision_id": None, "replies": [{}], "count": 1,
    }


def test_read_presentation_end_to_end_returns_the_outline(monkeypatch):
    payload = _fixture("slides_get_presentation.json")
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _PayloadService(payload))

    async def scenario():
        async with Client(workspace_mcp) as client:
            return await client.call_tool("slides_read_presentation", {"presentation_id": payload["presentationId"]})

    result = anyio.run(scenario)
    assert result.is_error is False
    structured = result.structured_content
    assert structured["presentation_id"] == payload["presentationId"]
    assert structured["count"] == len(payload["slides"])
    assert structured["slides"][0]["title"] == "Quarterly review"
    assert structured["slides"][0]["notes"] == "Open with the win."
    assert structured["slides"][0]["layout"] == "Title slide"
    for raw_field in ("presentationId", "masters", "layouts", "notesMaster", "pageSize"):
        assert raw_field not in structured
    assert structured == json.loads(json.dumps(presentation_envelope(payload)))
    # The outline carries no geometry; slide_ids adds it.
    assert all(set(element) == {"object_id", "type", "placeholder", "text"}
               for slide in structured["slides"] for element in slide["elements"])


def test_slides_tools_return_inferable_literal_shapes():
    async def tools():
        return {tool.name: tool for tool in await slides_mcp.list_tools(run_middleware=False)}

    listed = anyio.run(tools)
    expected = {
        "read_presentation": {"presentation_id", "title", "revision_id", "url", "page_size", "slides", "count"},
        "get_slide_thumbnail": {"content_url", "width", "height", "mime_type"},
        "create_presentation": {"status", "presentation_id", "title", "url", "slides"},
        "replace_text_in_presentation": {"status", "presentation_id", "occurrences_changed"},
        "batch_update_presentation": {"status", "presentation_id", "revision_id", "replies", "count"},
    }
    for name, keys in expected.items():
        schema = infer_tool_output_schema(inspect.unwrap(listed[name].fn))
        assert schema is not None, name
        assert keys <= set(schema["properties"]), (name, sorted(schema["properties"]))


# --- slides_read_presentation with slide_ids: the former slide page detail ------------

_SLIDE_ID = "g2a0b1c2d3e_0_8"


def _read_presentation(monkeypatch, arguments):
    payload = _fixture("slides_get_presentation.json")
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _PayloadService(payload))

    async def scenario():
        async with Client(workspace_mcp) as client:
            return await client.call_tool(
                "slides_read_presentation",
                {"presentation_id": payload["presentationId"], **arguments},
                raise_on_error=False,
            )

    return anyio.run(scenario), payload


def test_read_presentation_slide_ids_returns_the_slide_detail(monkeypatch):
    result, payload = _read_presentation(monkeypatch, {"slide_ids": [_SLIDE_ID]})

    assert result.is_error is False, result.content
    structured = result.structured_content
    assert structured["presentation_id"] == payload["presentationId"]
    assert structured["count"] == 1
    (slide,) = structured["slides"]
    assert (slide["object_id"], slide["index"], slide["layout"]) == (_SLIDE_ID, 1, "Title and body")
    # Everything the single-page read returned for this slide, page type aside.
    page = page_envelope(_fixture("slides_get_slide_page.json"))
    for key in ("object_id", "title", "text", "notes", "elements"):
        assert slide[key] == json.loads(json.dumps(page[key])), key
    title = slide["elements"][0]
    assert title["placeholder"] == "TITLE"
    assert title["size"] == {"width_pt": 670.9, "height_pt": 44.9}
    assert title["position"] == {"x_pt": 24.5, "y_pt": 35.0}


def test_read_presentation_slide_ids_keep_the_requested_order(monkeypatch):
    ids = ["g2a0b1c2d3e_0_20", "p", "g2a0b1c2d3e_0_20"]
    result, _ = _read_presentation(monkeypatch, {"slide_ids": ids})

    assert result.is_error is False, result.content
    slides = result.structured_content["slides"]
    assert [(slide["object_id"], slide["index"]) for slide in slides] == [("g2a0b1c2d3e_0_20", 2), ("p", 0)]
    assert result.structured_content["count"] == 2
    assert all("size" in element and "position" in element for slide in slides for element in slide["elements"])


def test_read_presentation_unknown_slide_id_is_invalid_input(monkeypatch):
    result, _ = _read_presentation(monkeypatch, {"slide_ids": [_SLIDE_ID, "nope"]})

    assert result.is_error is True
    assert result.structured_content["code"] == "invalid_input"
    assert "nope" in result.structured_content["message"]


def test_presentation_envelope_slide_ids_select_and_add_geometry():
    payload = {
        "presentationId": "deck-1",
        "slides": [
            {"objectId": "s1"},
            {"objectId": "s2", "pageElements": [{
                "objectId": "box",
                "size": {"width": {"magnitude": 10, "unit": "PT"}, "height": {"magnitude": 5, "unit": "PT"}},
                "transform": {"scaleX": 1, "scaleY": 1, "translateX": 3, "translateY": 4, "unit": "PT"},
                "shape": {"shapeType": "TEXT_BOX"},
            }]},
        ],
    }
    outline = presentation_envelope(payload)
    assert outline["count"] == 2
    assert outline["slides"][1]["elements"] == [{"object_id": "box", "type": "shape", "placeholder": None, "text": None}]
    detail = presentation_envelope(payload, slide_ids=["s2"])
    assert detail["count"] == 1
    assert detail["slides"][0]["index"] == 1
    assert detail["slides"][0]["elements"] == [{
        "object_id": "box", "type": "shape", "placeholder": None, "text": None,
        "size": {"width_pt": 10.0, "height_pt": 5.0}, "position": {"x_pt": 3.0, "y_pt": 4.0},
    }]
    assert presentation_envelope(payload, slide_ids=[]) == outline
