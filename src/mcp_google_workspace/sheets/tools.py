"""FastMCP Sheets tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import run_blocking
from ..common.errors import provider_tool_error
from .client import sheets_service
from .presentation import (
    append_values_envelope,
    batch_sheet_values_envelope,
    batch_update_spreadsheet_envelope,
    created_spreadsheet_envelope,
    spreadsheet_envelope,
    update_values_envelope,
)
from .schemas import (
    AppendSheetValuesRequest,
    BatchUpdateSpreadsheetRequest,
    CreateSpreadsheetRequest,
    DateTimeRenderOption,
    GetSpreadsheetRequest,
    ReadSheetValuesRequest,
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


def read_sheet_values_payload(request: ReadSheetValuesRequest) -> dict[str, Any]:
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


_RANGE_RULES = (
    "Basic A1 notation with an optional sheet prefix: 'Sheet1!A1:C10', 'A1', 'A:C' (whole columns) or "
    "'1:5' (whole rows); quote sheet names that contain spaces ('My Sheet'!A1:B2). A bare sheet name or "
    "an open-ended range such as A2:C is rejected, and without a prefix the first sheet is used."
)


def register_tools(server: FastMCP) -> None:
    @server.tool(name="get_spreadsheet")
    def get_spreadsheet(
        spreadsheet_id: str,
        include_grid_data: Annotated[
            bool,
            (
                "Also return each sheet's cell values, as displayed, in sheets[].values; this can be "
                "very large, so limit it with ranges. For values alone use sheets_read_sheet_values."
            ),
        ] = False,
        ranges: Annotated[
            list[str] | None,
            (
                "A1 ranges that limit the response to the sheets (and, with include_grid_data, the "
                "cells) they cover, e.g. ['Sheet1!A1:C10']; basic A1 notation as in sheets_read_sheet_values, so a "
                "bare sheet name is rejected. Omitted, every sheet is returned."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Read a Google Sheets spreadsheet's structure: its title, locale and every sheet (tab) with its ID and grid size. The spreadsheet_id comes from drive_list_files, search_workspace or sheets_create_spreadsheet; for cell values use sheets_read_sheet_values. Returns spreadsheet_id, title, locale, spreadsheet_time_zone (the file's own setting), url, named_ranges (name, named_range_id, A1 range) and sheets, each with sheet_id, title, index, type, row_count, column_count, frozen_row_count, frozen_column_count and hidden. With include_grid_data each sheet also has values (rows of displayed cell text) and values_start (the cell of values[0][0]), else both are null."""
        try:
            return spreadsheet_envelope(
                get_spreadsheet_payload(
                    GetSpreadsheetRequest(
                        spreadsheet_id=spreadsheet_id,
                        include_grid_data=include_grid_data,
                        ranges=ranges or [],
                    )
                ),
                include_grid_data=include_grid_data,
            )
        except HttpError as exc:
            raise provider_tool_error(exc, spreadsheet_id=spreadsheet_id) from exc

    @server.tool(name="create_spreadsheet")
    def create_spreadsheet(
        title: Annotated[str, "Title of the new spreadsheet, also used as its Drive file name."],
        sheet_titles: Annotated[
            list[str] | None,
            (
                "Titles of the sheets (tabs) to create, in order. Omitted, the spreadsheet starts with "
                "one sheet that Google names (Sheet1 in English)."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Create a new Google Sheets spreadsheet in the root of the user's My Drive, optionally with named sheets (tabs). Fill it afterwards with sheets_update_sheet_values or sheets_append_sheet_values. Returns status ok, spreadsheet_id (the ID every other sheets_ tool takes), title, url and sheets, each with sheet_id, title and index."""
        try:
            return created_spreadsheet_envelope(
                create_spreadsheet_payload(
                    CreateSpreadsheetRequest(title=title, sheet_titles=sheet_titles or [])
                )
            )
        except HttpError as exc:
            raise provider_tool_error(exc, title=title) from exc

    @server.tool(name="read_sheet_values")
    def read_sheet_values(
        spreadsheet_id: str,
        ranges: Annotated[
            list[str],
            (
                "One or more ranges to read, e.g. ['Sheet1!A1:C10'] or ['Sheet1!A1:C10', 'Sheet2!A:B']. "
                + _RANGE_RULES
            ),
        ],
        value_render_option: ValueRenderOption | None = None,
        date_time_render_option: DateTimeRenderOption | None = None,
        major_dimension: Literal["ROWS", "COLUMNS"] = "ROWS",
    ) -> dict[str, Any]:
        """Read the cell values of one or more A1 ranges of a Google Sheets spreadsheet in a single call. The spreadsheet_id comes from drive_list_files, search_workspace or sheets_create_spreadsheet; for the sheet (tab) names and sizes use sheets_get_spreadsheet, and to write cells sheets_update_sheet_values. Returns spreadsheet_id, count and value_ranges, one per requested range in request order (a list even for one range), each with range (the A1 range actually read), major_dimension, values (rows, or columns with major_dimension COLUMNS, without trailing empty rows and cells; [] when the range is empty) and row_count."""
        try:
            return batch_sheet_values_envelope(
                read_sheet_values_payload(
                    ReadSheetValuesRequest(
                        spreadsheet_id=spreadsheet_id,
                        ranges=ranges,
                        value_render_option=value_render_option,
                        date_time_render_option=date_time_render_option,
                        major_dimension=major_dimension,
                    )
                ),
                spreadsheet_id=spreadsheet_id,
            )
        except HttpError as exc:
            raise provider_tool_error(exc, spreadsheet_id=spreadsheet_id, ranges=ranges) from exc

    @server.tool(name="append_sheet_values")
    def append_sheet_values(
        spreadsheet_id: str,
        range_a1: Annotated[
            str,
            (
                "Range that locates the table to append to, e.g. 'Sheet1!A1' or 'Sheet1!A:D'; rows are "
                "written after the last row of that table, starting in its first column. " + _RANGE_RULES
            ),
        ],
        values: Annotated[
            list[list[Any]],
            "Rows to append, each a list of cell values (strings, numbers, booleans), e.g. [['2026-11-03', 'Lunch', 12.5]].",
        ],
        value_input_option: Literal["RAW", "USER_ENTERED"] = "RAW",
        insert_data_option: Annotated[
            Literal["OVERWRITE", "INSERT_ROWS"],
            (
                "INSERT_ROWS inserts new rows for the data, shifting any cells below it down; OVERWRITE "
                "writes into the cells after the table, replacing whatever is there."
            ),
        ] = "INSERT_ROWS",
        include_values_in_response: Annotated[
            bool, "Also return the written cells, as stored after parsing, in updated_values."
        ] = False,
    ) -> dict[str, Any]:
        """Append rows below the existing data of a table in a Google Sheets spreadsheet, leaving the current rows in place. To overwrite a known range use sheets_update_sheet_values; the spreadsheet_id comes from drive_list_files, search_workspace or sheets_create_spreadsheet. Returns status ok, spreadsheet_id, table_range (the table that was found, null on an empty sheet), updated_range, updated_rows, updated_columns, updated_cells and updated_values (the written cells with include_values_in_response, else null)."""
        try:
            return append_values_envelope(
                append_sheet_values_payload(
                    AppendSheetValuesRequest(
                        spreadsheet_id=spreadsheet_id,
                        range_a1=range_a1,
                        values=values,
                        value_input_option=value_input_option,
                        insert_data_option=insert_data_option,
                        include_values_in_response=include_values_in_response,
                    )
                ),
                spreadsheet_id=spreadsheet_id,
            )
        except HttpError as exc:
            raise provider_tool_error(exc, spreadsheet_id=spreadsheet_id, range_a1=range_a1) from exc

    @server.tool(name="update_sheet_values")
    def update_sheet_values(
        spreadsheet_id: str,
        range_a1: Annotated[str, "Range to write. " + _RANGE_RULES],
        values: Annotated[
            list[list[Any]],
            (
                "Rows written from the range's top-left cell, each a list of cell values, e.g. "
                "[['Name', 'Total'], ['Ana', 42]]; they must fit inside range_a1, a null skips a cell and "
                "an empty string clears it."
            ),
        ],
        value_input_option: Literal["RAW", "USER_ENTERED"] = "RAW",
        include_values_in_response: Annotated[
            bool, "Also return the written cells, as stored after parsing, in updated_values."
        ] = False,
    ) -> dict[str, Any]:
        """Write values into one A1 range of a Google Sheets spreadsheet, overwriting the cells it covers. To add rows below existing data use sheets_append_sheet_values, and for formatting or tab changes use sheets_batch_update_spreadsheet (extended tool tier); the spreadsheet_id comes from drive_list_files, search_workspace or sheets_create_spreadsheet. Returns status ok, spreadsheet_id, updated_range, updated_rows, updated_columns, updated_cells and updated_values (the written cells with include_values_in_response, else null)."""
        try:
            return update_values_envelope(
                update_sheet_values_payload(
                    UpdateSheetValuesRequest(
                        spreadsheet_id=spreadsheet_id,
                        range_a1=range_a1,
                        values=values,
                        value_input_option=value_input_option,
                        include_values_in_response=include_values_in_response,
                    )
                ),
                spreadsheet_id=spreadsheet_id,
            )
        except HttpError as exc:
            raise provider_tool_error(exc, spreadsheet_id=spreadsheet_id, range_a1=range_a1) from exc

    @server.tool(name="batch_update_spreadsheet", task=True)
    async def batch_update_spreadsheet(
        spreadsheet_id: str,
        requests: Annotated[
            list[dict[str, Any]],
            (
                "Sheets API batchUpdate request objects, applied in order, e.g. "
                '[{"addSheet": {"properties": {"title": "New tab"}}}] or '
                "updateSheetProperties/deleteSheet/repeatCell/updateCells requests."
            ),
        ],
        include_spreadsheet_in_response: Annotated[
            bool, "Also return the spreadsheet after the update, without cell values, in spreadsheet."
        ] = False,
        dry_run: Annotated[
            bool,
            (
                "True returns the impact preview (status 'dry_run', preview with the request count by "
                "kind, consent_required, action) without sending anything to Google."
            ),
        ] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Apply raw Google Sheets API batchUpdate requests for structure and formatting: adding, renaming or deleting sheets, formatting, merges, charts. To write cell values use sheets_update_sheet_values or sheets_append_sheet_values instead. Ten or more requests, or any delete* request, ask the user to confirm first; dry_run previews without changing anything. Requests apply atomically: if one is invalid, none is applied. Returns status 'ok', spreadsheet_id, replies (Google's, one per request in order, e.g. addSheet.properties.sheetId), count and spreadsheet (the sheets_get_spreadsheet shape when requested, else null)."""
        if dry_run:
            raise RuntimeError("dry_run is answered by the consent gate before the tool body runs.")
        try:
            return batch_update_spreadsheet_envelope(
                await run_blocking(
                    batch_update_spreadsheet_payload,
                    BatchUpdateSpreadsheetRequest(
                        spreadsheet_id=spreadsheet_id,
                        requests=requests,
                        include_spreadsheet_in_response=include_spreadsheet_in_response,
                    ),
                ),
                spreadsheet_id=spreadsheet_id,
            )
        except HttpError as exc:
            raise provider_tool_error(exc, spreadsheet_id=spreadsheet_id) from exc
