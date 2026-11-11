import anyio
import pytest
from fastmcp import Client
from googleapiclient.errors import HttpError
from httplib2 import Response

from mcp_google_workspace.sheets.schemas import ReadSheetValuesRequest
from mcp_google_workspace.sheets.server import sheets_mcp
from mcp_google_workspace.sheets.tools import (
    AppendSheetValuesRequest,
    BatchUpdateSpreadsheetRequest,
    CreateSpreadsheetRequest,
    GetSpreadsheetRequest,
    UpdateSheetValuesRequest,
    append_sheet_values_payload,
    batch_update_spreadsheet_payload,
    create_spreadsheet_payload,
    get_spreadsheet_payload,
    read_sheet_values_payload,
    update_sheet_values_payload,
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


class _ValuesApi:
    def __init__(self):
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return _Exec({"kind": "values.get", "kwargs": kwargs})

    def batchGet(self, **kwargs):
        self.calls.append(("batchGet", kwargs))
        return _Exec({"kind": "values.batchGet", "kwargs": kwargs})

    def append(self, **kwargs):
        self.calls.append(("append", kwargs))
        return _Exec({"kind": "values.append", "kwargs": kwargs})

    def update(self, **kwargs):
        self.calls.append(("update", kwargs))
        return _Exec({"kind": "values.update", "kwargs": kwargs})


class _SpreadsheetsApi:
    def __init__(self):
        self.calls = []
        self.values_api = _ValuesApi()

    def values(self):
        return self.values_api

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return _Exec({"kind": "spreadsheets.get", "kwargs": kwargs})

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return _Exec({"kind": "spreadsheets.create", "kwargs": kwargs})

    def batchUpdate(self, **kwargs):
        self.calls.append(("batchUpdate", kwargs))
        return _Exec({"kind": "spreadsheets.batchUpdate", "kwargs": kwargs})


class _SheetsService:
    def __init__(self):
        self.api = _SpreadsheetsApi()

    def spreadsheets(self):
        return self.api


def test_sheets_server_registers_expected_tools():
    tool_names = anyio.run(_list_tool_names, sheets_mcp)
    assert "get_spreadsheet" in tool_names
    assert "create_spreadsheet" in tool_names
    assert "read_sheet_values" in tool_names
    assert "get_sheet_values" not in tool_names
    assert "batch_get_sheet_values" not in tool_names
    assert "append_sheet_values" in tool_names
    assert "update_sheet_values" in tool_names
    assert "batch_update_spreadsheet" in tool_names


def test_sheets_request_validates_a1_ranges():
    request = ReadSheetValuesRequest(spreadsheet_id="sheet-1", ranges=["Sheet1!A1:B10"])
    assert request.ranges == ["Sheet1!A1:B10"]

    with pytest.raises(ValueError, match="ranges must use basic A1 notation"):
        ReadSheetValuesRequest(spreadsheet_id="sheet-1", ranges=["Sheet1!A1", "not-a-range"])
    with pytest.raises(ValueError, match="ranges cannot be empty"):
        ReadSheetValuesRequest(spreadsheet_id="sheet-1", ranges=[])


def test_sheets_payload_helpers(monkeypatch):
    service = _SheetsService()
    monkeypatch.setattr("mcp_google_workspace.sheets.tools.sheets_service", lambda: service)

    spreadsheet = get_spreadsheet_payload(
        GetSpreadsheetRequest(spreadsheet_id="sheet-1", include_grid_data=True, ranges=["Sheet1!A1:B2"])
    )
    created = create_spreadsheet_payload(CreateSpreadsheetRequest(title="Roadmap", sheet_titles=["Q1"]))
    values = read_sheet_values_payload(
        ReadSheetValuesRequest(spreadsheet_id="sheet-1", ranges=["Sheet1!A1:B2"])
    )
    batch = read_sheet_values_payload(
        ReadSheetValuesRequest(spreadsheet_id="sheet-1", ranges=["Sheet1!A1:A2", "Sheet1!B1:B2"])
    )
    appended = append_sheet_values_payload(
        AppendSheetValuesRequest(spreadsheet_id="sheet-1", range_a1="Sheet1!A1", values=[["a", 1]])
    )
    updated = update_sheet_values_payload(
        UpdateSheetValuesRequest(spreadsheet_id="sheet-1", range_a1="Sheet1!A1:B1", values=[["b", 2]])
    )
    patched = batch_update_spreadsheet_payload(
        BatchUpdateSpreadsheetRequest(
            spreadsheet_id="sheet-1",
            requests=[{"addSheet": {"properties": {"title": "Q2"}}}],
        )
    )

    assert spreadsheet["kwargs"]["spreadsheetId"] == "sheet-1"
    assert created["kwargs"]["body"]["sheets"][0]["properties"]["title"] == "Q1"
    assert values["kwargs"] == {
        "spreadsheetId": "sheet-1",
        "ranges": ["Sheet1!A1:B2"],
        "majorDimension": "ROWS",
        "valueRenderOption": None,
        "dateTimeRenderOption": None,
    }
    assert [call[0] for call in service.api.values_api.calls] == ["batchGet", "batchGet", "append", "update"]
    assert batch["kwargs"]["ranges"] == ["Sheet1!A1:A2", "Sheet1!B1:B2"]
    assert appended["kwargs"]["body"]["values"] == [["a", 1]]
    assert updated["kwargs"]["body"]["values"] == [["b", 2]]
    assert patched["kwargs"]["body"]["requests"][0]["addSheet"]["properties"]["title"] == "Q2"


def test_sheets_tool_annotations():
    get_tool = anyio.run(_get_tool, sheets_mcp, "get_spreadsheet")
    update_tool = anyio.run(_get_tool, sheets_mcp, "update_sheet_values")

    assert get_tool.annotations.read_only_hint is True
    assert get_tool.annotations.idempotent_hint is True
    assert update_tool.annotations.read_only_hint is False
    assert update_tool.annotations.idempotent_hint is True


def test_a1_range_accepts_whole_column_and_row_ranges():
    for value in ("A1", "Sheet1!A1:B10", "A:A", "Sheet1!A:C", "1:5", "My Sheet!2:2"):
        request = ReadSheetValuesRequest(spreadsheet_id="sheet-1", ranges=[value])
        assert request.ranges == [value]
    for invalid in ("A:", ":B", "Sheet1!", "A1:B:C"):
        with pytest.raises(ValueError):
            ReadSheetValuesRequest(spreadsheet_id="sheet-1", ranges=[invalid])


class _FailingExec:
    def execute(self):
        raise HttpError(Response({"status": "400"}), b'{"error":{"message":"Bad range"}}')


class _FailingSheetsService:
    def spreadsheets(self):
        return self

    def values(self):
        return self

    def batchGet(self, **kwargs):
        return _FailingExec()


def test_read_sheet_values_tool_returns_structured_error(monkeypatch):
    monkeypatch.setattr("mcp_google_workspace.sheets.tools.sheets_service", _FailingSheetsService)

    async def scenario():
        async with Client(sheets_mcp) as client:
            result = await client.call_tool(
                "read_sheet_values",
                {"spreadsheet_id": "sheet-1", "ranges": ["A:C"]},
                raise_on_error=False,
            )
            assert result.is_error is True
            return result.structured_content

    # W5: a Google API failure is an isError tool result with the error envelope
    # (was a successful result carrying an "error" dict).
    result = anyio.run(scenario)
    assert "400" in result["message"]
    assert result["code"] == "invalid_input"
    assert result["provider_status"] == 400
    assert result["details"]["context"]["spreadsheet_id"] == "sheet-1"
    assert result["details"]["context"]["ranges"] == ["A:C"]


# --- Normalized responses (2.0.0) -------------------------------------------------

import inspect  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

import mcp_google_workspace.auth.google_auth as google_auth  # noqa: E402
from mcp_google_workspace.common.output_schemas import infer_tool_output_schema  # noqa: E402
from mcp_google_workspace.server import workspace_mcp  # noqa: E402
from mcp_google_workspace.sheets.presentation import (  # noqa: E402
    batch_update_spreadsheet_envelope,
    column_letter,
    grid_range_a1,
    grid_values,
    quote_sheet_title,
    sheet_values_envelope,
    spreadsheet_envelope,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "google_payloads"


def _fixture(name):
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


class _PayloadService:
    """A Google client stub whose every ``execute()`` returns one payload."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        self.calls.append(name)
        return self

    def __call__(self, *_args, **_kwargs):
        return self

    def execute(self, *_args, **_kwargs):
        return json.loads(json.dumps(self._payload))


def test_column_letters_and_sheet_title_quoting():
    assert [column_letter(index) for index in (0, 25, 26, 51, 701, 702)] == ["A", "Z", "AA", "AZ", "ZZ", "AAA"]
    assert quote_sheet_title("Summary") == "Summary"
    assert quote_sheet_title("My Sheet") == "'My Sheet'"
    assert quote_sheet_title("Q1") == "'Q1'"  # reads as a cell reference
    assert quote_sheet_title("Bob's") == "'Bob''s'"


def test_grid_range_a1_covers_bounded_and_open_ranges():
    titles = {0: "Summary", 7: "My Sheet"}
    assert grid_range_a1({"sheetId": 0, "startRowIndex": 1, "endRowIndex": 13, "startColumnIndex": 1, "endColumnIndex": 2}, titles) == "Summary!B2:B13"
    # Google omits zero-valued fields: no sheetId is sheet 0, no start index is the first row/column.
    assert grid_range_a1({"endRowIndex": 5, "endColumnIndex": 3}, titles) == "Summary!A1:C5"
    assert grid_range_a1({"sheetId": 7, "startColumnIndex": 2, "endColumnIndex": 4}, titles) == "'My Sheet'!C:D"
    assert grid_range_a1({"sheetId": 7, "startRowIndex": 1, "endRowIndex": 4}, titles) == "'My Sheet'!2:4"
    assert grid_range_a1({"sheetId": 7, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 2}, titles) == "'My Sheet'!A2:B"
    assert grid_range_a1({"sheetId": 7}, titles) == "'My Sheet'"
    assert grid_range_a1({"sheetId": 7, "startColumnIndex": 2}, titles) is None
    assert grid_range_a1({"sheetId": 99, "endRowIndex": 2}, titles) is None


def test_grid_values_places_blocks_and_trims_like_the_values_api():
    data = [
        {"startRow": 1, "startColumn": 1, "rowData": [{"values": [{"formattedValue": "a"}, {}]}, {"values": [{}]}]},
        {"startRow": 3, "startColumn": 2, "rowData": [{"values": [{"formattedValue": "b"}]}]},
    ]
    values, start = grid_values(data)
    assert start == "B2"
    assert values == [["a"], [], ["", "b"]]
    assert grid_values([]) == ([], None)
    assert grid_values([{"rowData": [{"values": [{}]}]}]) == ([], None)


def test_spreadsheet_envelope_handles_minimal_payloads():
    """A stub or older payload with only an ID and title normalizes without errors."""
    result = spreadsheet_envelope({"spreadsheetId": "s1", "properties": {"title": "T"}})
    assert result["spreadsheet_id"] == "s1"
    assert result["sheets"] == [] and result["named_ranges"] == []
    assert result["url"] is None and result["spreadsheet_time_zone"] is None
    assert spreadsheet_envelope({})["title"] is None

    updated = batch_update_spreadsheet_envelope({"spreadsheetId": "s1", "replies": [{}]}, spreadsheet_id="s1")
    assert updated == {"status": "ok", "spreadsheet_id": "s1", "replies": [{}], "count": 1, "spreadsheet": None}
    assert batch_update_spreadsheet_envelope({}, spreadsheet_id="s2")["spreadsheet_id"] == "s2"


def test_sheet_values_envelope_reports_empty_ranges_as_empty_lists():
    result = sheet_values_envelope({"range": "Sheet1!A1:B2", "majorDimension": "ROWS"}, spreadsheet_id="s1")
    assert result == {"spreadsheet_id": "s1", "range": "Sheet1!A1:B2", "major_dimension": "ROWS", "values": [], "row_count": 0}


def test_get_spreadsheet_end_to_end_returns_the_normalized_shape(monkeypatch):
    payload = _fixture("sheets_get_spreadsheet.json")
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _PayloadService(payload))

    async def scenario():
        async with Client(workspace_mcp) as client:
            return await client.call_tool("sheets_get_spreadsheet", {"spreadsheet_id": payload["spreadsheetId"]})

    result = anyio.run(scenario)
    assert result.is_error is False
    structured = result.structured_content
    assert structured["spreadsheet_id"] == payload["spreadsheetId"]
    assert structured["title"] == "Budget 2026"
    assert structured["sheets"][0]["sheet_id"] == 0
    assert structured["named_ranges"][0]["range"] == "Summary!B2:B13"
    for raw_field in ("spreadsheetId", "properties", "developerMetadata", "dataSources"):
        assert raw_field not in structured


def test_batch_update_spreadsheet_end_to_end_with_a_minimal_payload(monkeypatch):
    import importlib

    import mcp_google_workspace.server as server_module

    payload = {"spreadsheetId": "sheet-1", "replies": [{}]}
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: _PayloadService(payload))
    # The raw batchUpdate tools are published from the extended tool tier up.
    monkeypatch.setenv("MCP_TOOL_TIER", "extended")
    extended = importlib.reload(server_module).workspace_mcp
    monkeypatch.delenv("MCP_TOOL_TIER")
    importlib.reload(server_module)

    async def scenario():
        async with Client(extended) as client:
            return await client.call_tool(
                "sheets_batch_update_spreadsheet",
                {"spreadsheet_id": "sheet-1", "requests": [{"addSheet": {"properties": {"title": "Q2"}}}]},
            )

    result = anyio.run(scenario)
    assert result.is_error is False
    assert result.structured_content["spreadsheet_id"] == "sheet-1"
    assert result.structured_content["count"] == 1


def test_sheets_tools_return_inferable_literal_shapes():
    async def tools():
        return {tool.name: tool for tool in await sheets_mcp.list_tools(run_middleware=False)}

    listed = anyio.run(tools)
    expected = {
        "get_spreadsheet": {"spreadsheet_id", "title", "locale", "spreadsheet_time_zone", "url", "sheets", "named_ranges"},
        "create_spreadsheet": {"status", "spreadsheet_id", "title", "url", "sheets"},
        "read_sheet_values": {"spreadsheet_id", "value_ranges", "count"},
        "append_sheet_values": {"status", "spreadsheet_id", "table_range", "updated_range", "updated_values"},
        "update_sheet_values": {"status", "spreadsheet_id", "updated_range", "updated_cells", "updated_values"},
        "batch_update_spreadsheet": {"status", "spreadsheet_id", "replies", "count", "spreadsheet"},
    }
    for name, keys in expected.items():
        schema = infer_tool_output_schema(inspect.unwrap(listed[name].fn))
        assert schema is not None, name
        assert keys <= set(schema["properties"]), (name, sorted(schema["properties"]))


# --- sheets_read_sheet_values: one range and several ---------------------------


class _BatchGetService(_PayloadService):
    """Records the keyword arguments of values().batchGet()."""

    def __init__(self, payload):
        super().__init__(payload)
        self.kwargs = []

    def __call__(self, *_args, **kwargs):
        if kwargs:
            self.kwargs.append(kwargs)
        return self


def _read_values(monkeypatch, payload, arguments):
    service = _BatchGetService(payload)
    monkeypatch.setattr(google_auth, "_build_service_now", lambda *_a, **_k: service)

    async def scenario():
        async with Client(workspace_mcp) as client:
            return await client.call_tool("sheets_read_sheet_values", arguments, raise_on_error=False)

    return anyio.run(scenario), service


def test_read_sheet_values_one_range_is_a_one_item_list(monkeypatch):
    payload = {
        "spreadsheetId": "sheet-1",
        "valueRanges": [{"range": "Sheet1!A1:B2", "majorDimension": "ROWS", "values": [["a", "1"], ["b", "2"]]}],
    }
    result, service = _read_values(monkeypatch, payload, {"spreadsheet_id": "sheet-1", "ranges": ["Sheet1!A1:B2"]})

    assert result.is_error is False, result.content
    assert "batchGet" in service.calls and "get" not in service.calls
    assert service.kwargs[-1] == {
        "spreadsheetId": "sheet-1",
        "ranges": ["Sheet1!A1:B2"],
        "majorDimension": "ROWS",
        "valueRenderOption": None,
        "dateTimeRenderOption": None,
    }
    assert result.structured_content == {
        "spreadsheet_id": "sheet-1",
        "value_ranges": [
            {"range": "Sheet1!A1:B2", "major_dimension": "ROWS", "values": [["a", "1"], ["b", "2"]], "row_count": 2}
        ],
        "count": 1,
    }


def test_read_sheet_values_several_ranges_keep_request_order_and_empty_ranges(monkeypatch):
    payload = _fixture("sheets_batch_get_sheet_values.json")
    ranges = [entry["range"] for entry in payload["valueRanges"]]
    result, service = _read_values(monkeypatch, payload, {"spreadsheet_id": "sheet-1", "ranges": ranges})

    assert result.is_error is False, result.content
    assert service.kwargs[-1]["ranges"] == ranges
    structured = result.structured_content
    assert structured["count"] == len(ranges)
    assert [entry["range"] for entry in structured["value_ranges"]] == ranges
    for entry, raw in zip(structured["value_ranges"], payload["valueRanges"], strict=True):
        assert entry["values"] == raw.get("values", [])
        assert entry["row_count"] == len(raw.get("values", []))


def test_read_sheet_values_empty_range_reports_an_empty_list(monkeypatch):
    payload = {"spreadsheetId": "sheet-1", "valueRanges": [{"range": "Sheet1!A1:B2", "majorDimension": "ROWS"}]}
    result, _ = _read_values(monkeypatch, payload, {"spreadsheet_id": "sheet-1", "ranges": ["Sheet1!A1:B2"]})
    assert result.structured_content["value_ranges"][0]["values"] == []
    assert result.structured_content["value_ranges"][0]["row_count"] == 0


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"value_render_option": "FORMULA"}, {"valueRenderOption": "FORMULA"}),
        ({"value_render_option": "UNFORMATTED_VALUE"}, {"valueRenderOption": "UNFORMATTED_VALUE"}),
        ({"value_render_option": "FORMATTED_VALUE"}, {"valueRenderOption": "FORMATTED_VALUE"}),
        (
            {"value_render_option": "UNFORMATTED_VALUE", "date_time_render_option": "SERIAL_NUMBER"},
            {"valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "SERIAL_NUMBER"},
        ),
        ({"date_time_render_option": "FORMATTED_STRING"}, {"dateTimeRenderOption": "FORMATTED_STRING"}),
        ({"major_dimension": "COLUMNS"}, {"majorDimension": "COLUMNS"}),
    ],
)
@pytest.mark.parametrize("ranges", [["Sheet1!A1:B2"], ["Sheet1!A1:B2", "Sheet2!A:B"]])
def test_read_sheet_values_passes_each_render_option(monkeypatch, arguments, expected, ranges):
    payload = {"spreadsheetId": "sheet-1", "valueRanges": [{"range": item} for item in ranges]}
    result, service = _read_values(monkeypatch, payload, {"spreadsheet_id": "sheet-1", "ranges": ranges, **arguments})

    assert result.is_error is False, result.content
    sent = service.kwargs[-1]
    assert sent["ranges"] == ranges
    for key, value in expected.items():
        assert sent[key] == value
    assert result.structured_content["count"] == len(ranges)


@pytest.mark.parametrize(
    "arguments",
    [
        {"ranges": []},
        {"ranges": ["Sheet1!A2:C"]},
        {"ranges": ["Sheet1!A1"], "value_render_option": "AS_IS"},
        {"ranges": ["Sheet1!A1"], "major_dimension": "DIAGONAL"},
    ],
)
def test_read_sheet_values_rejects_invalid_input(monkeypatch, arguments):
    result, service = _read_values(monkeypatch, {}, {"spreadsheet_id": "sheet-1", **arguments})
    assert result.is_error is True
    assert service.kwargs == []
