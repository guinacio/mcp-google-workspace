"""FastMCP Sheets tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import run_blocking
from ..common.errors import tool_error_payload
from .client import sheets_service
from .schemas import (
    AppendSheetValuesRequest,
    BatchGetSheetValuesRequest,
    BatchUpdateSpreadsheetRequest,
    CreateSpreadsheetRequest,
    DateTimeRenderOption,
    GetSheetValuesRequest,
    GetSpreadsheetRequest,
    UpdateSheetValuesRequest,
    ValueRenderOption,
)


def get_spreadsheet_payload(request: GetSpreadsheetRequest) -> dict[str, Any]:
    service = sheets_service()
    return service.spreadsheets().get(
        spreadsheetId=request.spreadsheet_id,
        includeGridData=request.include_grid_data,
        ranges=request.ranges or None,
    ).execute()


def create_spreadsheet_payload(request: CreateSpreadsheetRequest) -> dict[str, Any]:
    service = sheets_service()
    body: dict[str, Any] = {"properties": {"title": request.title}}
    if request.sheet_titles:
        body["sheets"] = [{"properties": {"title": title}} for title in request.sheet_titles]
    return service.spreadsheets().create(body=body).execute()


def get_sheet_values_payload(request: GetSheetValuesRequest) -> dict[str, Any]:
    service = sheets_service()
    return service.spreadsheets().values().get(
        spreadsheetId=request.spreadsheet_id,
        range=request.range_a1,
        majorDimension=request.major_dimension,
        valueRenderOption=request.value_render_option,
        dateTimeRenderOption=request.date_time_render_option,
    ).execute()


def batch_get_sheet_values_payload(request: BatchGetSheetValuesRequest) -> dict[str, Any]:
    service = sheets_service()
    return service.spreadsheets().values().batchGet(
        spreadsheetId=request.spreadsheet_id,
        ranges=request.ranges,
        majorDimension=request.major_dimension,
        valueRenderOption=request.value_render_option,
        dateTimeRenderOption=request.date_time_render_option,
    ).execute()


def append_sheet_values_payload(request: AppendSheetValuesRequest) -> dict[str, Any]:
    service = sheets_service()
    return service.spreadsheets().values().append(
        spreadsheetId=request.spreadsheet_id,
        range=request.range_a1,
        valueInputOption=request.value_input_option,
        insertDataOption=request.insert_data_option,
        includeValuesInResponse=request.include_values_in_response,
        body={"values": request.values},
    ).execute()


def update_sheet_values_payload(request: UpdateSheetValuesRequest) -> dict[str, Any]:
    service = sheets_service()
    return service.spreadsheets().values().update(
        spreadsheetId=request.spreadsheet_id,
        range=request.range_a1,
        valueInputOption=request.value_input_option,
        includeValuesInResponse=request.include_values_in_response,
        body={"values": request.values},
    ).execute()


def batch_update_spreadsheet_payload(request: BatchUpdateSpreadsheetRequest) -> dict[str, Any]:
    service = sheets_service()
    return service.spreadsheets().batchUpdate(
        spreadsheetId=request.spreadsheet_id,
        body={
            "requests": request.requests,
            "includeSpreadsheetInResponse": request.include_spreadsheet_in_response,
        },
    ).execute()


def register_tools(server: FastMCP) -> None:
    @server.tool(name="get_spreadsheet")
    def get_spreadsheet(
        spreadsheet_id: str,
        include_grid_data: bool = False,
        ranges: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch spreadsheet metadata: properties plus each sheet/tab's title and sheetId.

        spreadsheet_id is the file's Drive ID — there is no Sheets-side lookup
        tool, so obtain it via drive_list_files or search_workspace first. Set
        include_grid_data=true (optionally limited by A1 'ranges') to include
        cell data. Returns the Spreadsheet resource, or {"error": ...} on API
        failure.
        """
        try:
            return get_spreadsheet_payload(
                GetSpreadsheetRequest(
                    spreadsheet_id=spreadsheet_id,
                    include_grid_data=include_grid_data,
                    ranges=ranges or [],
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, spreadsheet_id=spreadsheet_id)

    @server.tool(name="create_spreadsheet")
    def create_spreadsheet(
        title: str,
        sheet_titles: Annotated[
            list[str] | None, "Optional titles for initial sheets/tabs to create in the spreadsheet."
        ] = None,
    ) -> dict[str, Any]:
        """Create a new spreadsheet, optionally with named initial sheets/tabs.

        Returns the created Spreadsheet resource (including its
        'spreadsheetId' for the other sheets tools), or {"error": ...} on API
        failure.
        """
        try:
            return create_spreadsheet_payload(
                CreateSpreadsheetRequest(title=title, sheet_titles=sheet_titles or [])
            )
        except HttpError as exc:
            return tool_error_payload(exc, title=title)

    @server.tool(name="get_sheet_values")
    def get_sheet_values(
        spreadsheet_id: str,
        range_a1: Annotated[
            str, "A1 range to read, e.g. Sheet1!A1:C10, A:C (whole columns), or 1:5 (whole rows)."
        ],
        major_dimension: Literal["ROWS", "COLUMNS"] = "ROWS",
        value_render_option: Annotated[
            ValueRenderOption | None,
            "How cell values are rendered: FORMATTED_VALUE (default), UNFORMATTED_VALUE, or FORMULA.",
        ] = None,
        date_time_render_option: Annotated[
            DateTimeRenderOption | None,
            "How dates/times are rendered: SERIAL_NUMBER (default) or FORMATTED_STRING.",
        ] = None,
    ) -> dict[str, Any]:
        """Read cell values from one A1 range of a spreadsheet.

        spreadsheet_id comes from create_spreadsheet or Drive discovery
        (drive_list_files / search_workspace). Returns the ValueRange payload
        ('values' as a list of rows), or {"error": ...} on API failure.
        """
        try:
            return get_sheet_values_payload(
                GetSheetValuesRequest(
                    spreadsheet_id=spreadsheet_id,
                    range_a1=range_a1,
                    major_dimension=major_dimension,
                    value_render_option=value_render_option,
                    date_time_render_option=date_time_render_option,
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, spreadsheet_id=spreadsheet_id, range_a1=range_a1)

    @server.tool(name="batch_get_sheet_values")
    def batch_get_sheet_values(
        spreadsheet_id: str,
        ranges: Annotated[
            list[str], "A1 ranges to read in one call, e.g. [\"Sheet1!A1:C10\", \"Sheet2!A:B\"]."
        ],
        major_dimension: Literal["ROWS", "COLUMNS"] = "ROWS",
        value_render_option: Annotated[
            ValueRenderOption | None,
            "How cell values are rendered: FORMATTED_VALUE (default), UNFORMATTED_VALUE, or FORMULA.",
        ] = None,
        date_time_render_option: Annotated[
            DateTimeRenderOption | None,
            "How dates/times are rendered: SERIAL_NUMBER (default) or FORMATTED_STRING.",
        ] = None,
    ) -> dict[str, Any]:
        """Read cell values from multiple A1 ranges of one spreadsheet in a single call.

        spreadsheet_id comes from create_spreadsheet or Drive discovery
        (drive_list_files / search_workspace). Returns the batchGet payload
        ('valueRanges' list, one per requested range), or {"error": ...} on
        API failure.
        """
        try:
            return batch_get_sheet_values_payload(
                BatchGetSheetValuesRequest(
                    spreadsheet_id=spreadsheet_id,
                    ranges=ranges,
                    major_dimension=major_dimension,
                    value_render_option=value_render_option,
                    date_time_render_option=date_time_render_option,
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, spreadsheet_id=spreadsheet_id)

    @server.tool(name="append_sheet_values")
    def append_sheet_values(
        spreadsheet_id: str,
        range_a1: str,
        values: Annotated[list[list[Any]], "Tabular row-major values to append after the range's last row."],
        value_input_option: Literal["RAW", "USER_ENTERED"] = "RAW",
        insert_data_option: Annotated[
            Literal["OVERWRITE", "INSERT_ROWS"],
            "How appended data interacts with existing rows: OVERWRITE existing cells or INSERT_ROWS to shift them down.",
        ] = "INSERT_ROWS",
        include_values_in_response: bool = False,
    ) -> dict[str, Any]:
        """Append rows after the last data row of the table that range_a1 anchors.

        value_input_option USER_ENTERED parses values like typed input
        (formulas, dates); RAW stores them verbatim. Returns the append reply
        (including the 'updates' summary), or {"error": ...} on API failure.
        """
        try:
            return append_sheet_values_payload(
                AppendSheetValuesRequest(
                    spreadsheet_id=spreadsheet_id,
                    range_a1=range_a1,
                    values=values,
                    value_input_option=value_input_option,
                    insert_data_option=insert_data_option,
                    include_values_in_response=include_values_in_response,
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, spreadsheet_id=spreadsheet_id, range_a1=range_a1)

    @server.tool(name="update_sheet_values")
    def update_sheet_values(
        spreadsheet_id: str,
        range_a1: str,
        values: Annotated[list[list[Any]], "Replacement tabular row-major values for the given range."],
        value_input_option: Literal["RAW", "USER_ENTERED"] = "RAW",
        include_values_in_response: bool = False,
    ) -> dict[str, Any]:
        """Overwrite the cells in range_a1 with the given row-major values.

        value_input_option USER_ENTERED parses values like typed input
        (formulas, dates); RAW stores them verbatim. Returns the update reply
        (updatedRows/updatedCells counts), or {"error": ...} on API failure.
        """
        try:
            return update_sheet_values_payload(
                UpdateSheetValuesRequest(
                    spreadsheet_id=spreadsheet_id,
                    range_a1=range_a1,
                    values=values,
                    value_input_option=value_input_option,
                    include_values_in_response=include_values_in_response,
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, spreadsheet_id=spreadsheet_id, range_a1=range_a1)

    @server.tool(name="batch_update_spreadsheet", task=True)
    async def batch_update_spreadsheet(
        spreadsheet_id: str,
        requests: Annotated[
            list[dict[str, Any]],
            (
                "Raw Sheets API batchUpdate request objects, e.g. "
                '[{"addSheet": {"properties": {"title": "New tab"}}}] or '
                "updateSheetProperties/deleteSheet/repeatCell/updateCells requests."
            ),
        ],
        include_spreadsheet_in_response: bool = False,
    ) -> dict[str, Any]:
        """Apply raw Sheets API batchUpdate requests (structural/formatting changes).

        Use this for anything beyond cell values: adding/deleting sheets,
        formatting, merges, charts. spreadsheet_id comes from
        create_spreadsheet or Drive discovery (drive_list_files /
        search_workspace). Returns the batchUpdate replies, or {"error": ...}
        on API failure.
        """
        try:
            return await run_blocking(
                batch_update_spreadsheet_payload,
                BatchUpdateSpreadsheetRequest(
                    spreadsheet_id=spreadsheet_id,
                    requests=requests,
                    include_spreadsheet_in_response=include_spreadsheet_in_response,
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, spreadsheet_id=spreadsheet_id)
