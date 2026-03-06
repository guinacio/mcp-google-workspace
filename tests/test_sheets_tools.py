import anyio
import pytest

from mcp_google_workspace.sheets.schemas import GetSheetValuesRequest
from mcp_google_workspace.sheets.server import sheets_mcp
from mcp_google_workspace.sheets.tools import (
    AppendSheetValuesRequest,
    BatchGetSheetValuesRequest,
    BatchUpdateSpreadsheetRequest,
    CreateSpreadsheetRequest,
    GetSpreadsheetRequest,
    UpdateSheetValuesRequest,
    append_sheet_values_payload,
    batch_get_sheet_values_payload,
    batch_update_spreadsheet_payload,
    create_spreadsheet_payload,
    get_sheet_values_payload,
    get_spreadsheet_payload,
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
    assert "get_sheet_values" in tool_names
    assert "batch_get_sheet_values" in tool_names
    assert "append_sheet_values" in tool_names
    assert "update_sheet_values" in tool_names
    assert "batch_update_spreadsheet" in tool_names


def test_sheets_request_validates_a1_ranges():
    request = GetSheetValuesRequest(spreadsheet_id="sheet-1", range_a1="Sheet1!A1:B10")
    assert request.range_a1 == "Sheet1!A1:B10"

    with pytest.raises(ValueError):
        GetSheetValuesRequest(spreadsheet_id="sheet-1", range_a1="not-a-range")


def test_sheets_payload_helpers(monkeypatch):
    service = _SheetsService()
    monkeypatch.setattr("mcp_google_workspace.sheets.tools.sheets_service", lambda: service)

    spreadsheet = get_spreadsheet_payload(
        GetSpreadsheetRequest(spreadsheet_id="sheet-1", include_grid_data=True, ranges=["Sheet1!A1:B2"])
    )
    created = create_spreadsheet_payload(CreateSpreadsheetRequest(title="Roadmap", sheet_titles=["Q1"]))
    values = get_sheet_values_payload(
        GetSheetValuesRequest(spreadsheet_id="sheet-1", range_a1="Sheet1!A1:B2")
    )
    batch = batch_get_sheet_values_payload(
        BatchGetSheetValuesRequest(spreadsheet_id="sheet-1", ranges=["Sheet1!A1:A2", "Sheet1!B1:B2"])
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
    assert values["kwargs"]["range"] == "Sheet1!A1:B2"
    assert batch["kwargs"]["ranges"] == ["Sheet1!A1:A2", "Sheet1!B1:B2"]
    assert appended["kwargs"]["body"]["values"] == [["a", 1]]
    assert updated["kwargs"]["body"]["values"] == [["b", 2]]
    assert patched["kwargs"]["body"]["requests"][0]["addSheet"]["properties"]["title"] == "Q2"


def test_sheets_tool_annotations():
    get_tool = anyio.run(_get_tool, sheets_mcp, "get_spreadsheet")
    update_tool = anyio.run(_get_tool, sheets_mcp, "update_sheet_values")

    assert get_tool.annotations.readOnlyHint is True
    assert get_tool.annotations.idempotentHint is True
    assert update_tool.annotations.readOnlyHint is False
    assert update_tool.annotations.idempotentHint is True
