"""FastMCP Sheets tools."""

from __future__ import annotations

from typing import Any, Literal

from fastmcp import FastMCP

from .client import sheets_service
from .schemas import (
    AppendSheetValuesRequest,
    BatchGetSheetValuesRequest,
    BatchUpdateSpreadsheetRequest,
    CreateSpreadsheetRequest,
    GetSheetValuesRequest,
    GetSpreadsheetRequest,
    UpdateSheetValuesRequest,
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
    async def get_spreadsheet(
        spreadsheet_id: str,
        include_grid_data: bool = False,
        ranges: list[str] | None = None,
    ) -> dict[str, Any]:
        return get_spreadsheet_payload(
            GetSpreadsheetRequest(
                spreadsheet_id=spreadsheet_id,
                include_grid_data=include_grid_data,
                ranges=ranges or [],
            )
        )

    @server.tool(name="create_spreadsheet")
    async def create_spreadsheet(title: str, sheet_titles: list[str] | None = None) -> dict[str, Any]:
        return create_spreadsheet_payload(
            CreateSpreadsheetRequest(title=title, sheet_titles=sheet_titles or [])
        )

    @server.tool(name="get_sheet_values")
    async def get_sheet_values(
        spreadsheet_id: str,
        range_a1: str,
        major_dimension: Literal["ROWS", "COLUMNS"] = "ROWS",
        value_render_option: str | None = None,
        date_time_render_option: str | None = None,
    ) -> dict[str, Any]:
        return get_sheet_values_payload(
            GetSheetValuesRequest(
                spreadsheet_id=spreadsheet_id,
                range_a1=range_a1,
                major_dimension=major_dimension,
                value_render_option=value_render_option,
                date_time_render_option=date_time_render_option,
            )
        )

    @server.tool(name="batch_get_sheet_values")
    async def batch_get_sheet_values(
        spreadsheet_id: str,
        ranges: list[str],
        major_dimension: Literal["ROWS", "COLUMNS"] = "ROWS",
        value_render_option: str | None = None,
        date_time_render_option: str | None = None,
    ) -> dict[str, Any]:
        return batch_get_sheet_values_payload(
            BatchGetSheetValuesRequest(
                spreadsheet_id=spreadsheet_id,
                ranges=ranges,
                major_dimension=major_dimension,
                value_render_option=value_render_option,
                date_time_render_option=date_time_render_option,
            )
        )

    @server.tool(name="append_sheet_values")
    async def append_sheet_values(
        spreadsheet_id: str,
        range_a1: str,
        values: list[list[Any]],
        value_input_option: Literal["RAW", "USER_ENTERED"] = "RAW",
        insert_data_option: Literal["OVERWRITE", "INSERT_ROWS"] = "INSERT_ROWS",
        include_values_in_response: bool = False,
    ) -> dict[str, Any]:
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

    @server.tool(name="update_sheet_values")
    async def update_sheet_values(
        spreadsheet_id: str,
        range_a1: str,
        values: list[list[Any]],
        value_input_option: Literal["RAW", "USER_ENTERED"] = "RAW",
        include_values_in_response: bool = False,
    ) -> dict[str, Any]:
        return update_sheet_values_payload(
            UpdateSheetValuesRequest(
                spreadsheet_id=spreadsheet_id,
                range_a1=range_a1,
                values=values,
                value_input_option=value_input_option,
                include_values_in_response=include_values_in_response,
            )
        )

    @server.tool(name="batch_update_spreadsheet")
    async def batch_update_spreadsheet(
        spreadsheet_id: str,
        requests: list[dict[str, Any]],
        include_spreadsheet_in_response: bool = False,
    ) -> dict[str, Any]:
        return batch_update_spreadsheet_payload(
            BatchUpdateSpreadsheetRequest(
                spreadsheet_id=spreadsheet_id,
                requests=requests,
                include_spreadsheet_in_response=include_spreadsheet_in_response,
            )
        )
