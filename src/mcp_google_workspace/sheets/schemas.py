"""Pydantic models for Sheets tools."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator

from ..common.request_model import ToolRequestModel

_A1_RANGE_RE = re.compile(r"^(?:[^!]+!)?[A-Za-z]+[0-9]+(?::[A-Za-z]+[0-9]+)?$")


def _validate_a1_range(value: str) -> str:
    if not _A1_RANGE_RE.match(value):
        raise ValueError("range_a1 must use basic A1 notation, for example Sheet1!A1:B10")
    return value


class GetSpreadsheetRequest(ToolRequestModel):
    spreadsheet_id: str = Field(description="Spreadsheet file ID.")
    include_grid_data: bool = Field(default=False, description="Include grid cell data when true.")
    ranges: list[str] = Field(default_factory=list, description="Optional A1 ranges to limit the response.")

    @field_validator("ranges")
    @classmethod
    def _validate_ranges(cls, value: list[str]) -> list[str]:
        return [_validate_a1_range(item) for item in value]


class CreateSpreadsheetRequest(ToolRequestModel):
    title: str = Field(description="Spreadsheet title.")
    sheet_titles: list[str] = Field(default_factory=list, description="Optional initial sheet titles.")


class GetSheetValuesRequest(ToolRequestModel):
    spreadsheet_id: str = Field(description="Spreadsheet file ID.")
    range_a1: str = Field(description="A1 notation range, for example Sheet1!A1:C10.")
    major_dimension: Literal["ROWS", "COLUMNS"] = Field(default="ROWS")
    value_render_option: str | None = Field(default=None)
    date_time_render_option: str | None = Field(default=None)

    @field_validator("range_a1")
    @classmethod
    def _validate_range(cls, value: str) -> str:
        return _validate_a1_range(value)


class BatchGetSheetValuesRequest(ToolRequestModel):
    spreadsheet_id: str = Field(description="Spreadsheet file ID.")
    ranges: list[str] = Field(description="A1 notation ranges to fetch.")
    major_dimension: Literal["ROWS", "COLUMNS"] = Field(default="ROWS")
    value_render_option: str | None = Field(default=None)
    date_time_render_option: str | None = Field(default=None)

    @field_validator("ranges")
    @classmethod
    def _validate_ranges(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("ranges cannot be empty")
        return [_validate_a1_range(item) for item in value]


class AppendSheetValuesRequest(ToolRequestModel):
    spreadsheet_id: str = Field(description="Spreadsheet file ID.")
    range_a1: str = Field(description="Target A1 range anchor.")
    values: list[list[Any]] = Field(description="Tabular values to append.")
    value_input_option: Literal["RAW", "USER_ENTERED"] = Field(default="RAW")
    insert_data_option: Literal["OVERWRITE", "INSERT_ROWS"] = Field(default="INSERT_ROWS")
    include_values_in_response: bool = Field(default=False)

    @field_validator("range_a1")
    @classmethod
    def _validate_range(cls, value: str) -> str:
        return _validate_a1_range(value)


class UpdateSheetValuesRequest(ToolRequestModel):
    spreadsheet_id: str = Field(description="Spreadsheet file ID.")
    range_a1: str = Field(description="Target A1 range.")
    values: list[list[Any]] = Field(description="Replacement tabular values.")
    value_input_option: Literal["RAW", "USER_ENTERED"] = Field(default="RAW")
    include_values_in_response: bool = Field(default=False)

    @field_validator("range_a1")
    @classmethod
    def _validate_range(cls, value: str) -> str:
        return _validate_a1_range(value)


class BatchUpdateSpreadsheetRequest(ToolRequestModel):
    spreadsheet_id: str = Field(description="Spreadsheet file ID.")
    requests: list[dict[str, Any]] = Field(description="Raw Sheets batchUpdate requests.")
    include_spreadsheet_in_response: bool = Field(default=False)
