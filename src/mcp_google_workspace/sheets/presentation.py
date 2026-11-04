"""Readable Google Sheets representations.

Pure functions that turn Sheets API payloads into the shapes the sheets_
tools return: snake_case keys, A1 notation instead of grid indexes, and none
of the formatting, theme, developer metadata or data source objects.
"""

from __future__ import annotations

import re
from typing import Any

from ..common.resources import clean_web_url

_PLAIN_SHEET_TITLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CELL_REFERENCE = re.compile(r"^[A-Za-z]{1,3}[0-9]+$")


def column_letter(index: int) -> str:
    """The A1 column letters of a zero-based column index (0 -> A, 26 -> AA)."""
    letters = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def quote_sheet_title(title: str) -> str:
    """A sheet title as an A1 range prefix, quoted when Sheets requires it."""
    if _PLAIN_SHEET_TITLE.match(title) and not _CELL_REFERENCE.match(title):
        return title
    return "'" + title.replace("'", "''") + "'"


def grid_range_a1(grid_range: dict[str, Any], sheet_titles: dict[int, str]) -> str | None:
    """A GridRange in A1 notation (Summary!B2:B13), or None when it has no A1 form.

    Google omits zero-valued fields, so a missing sheetId is sheet 0 and a
    missing start index is the first row or column; a missing end index is
    unbounded. None when the sheet is not in the payload or the range is open
    to the right (or, for whole rows, to the bottom).
    """
    title = sheet_titles.get(int(grid_range.get("sheetId", 0)))
    if title is None:
        return None
    prefix = quote_sheet_title(title)
    start_row = grid_range.get("startRowIndex")
    end_row = grid_range.get("endRowIndex")
    start_column = grid_range.get("startColumnIndex")
    end_column = grid_range.get("endColumnIndex")
    rows_bounded = start_row is not None or end_row is not None
    columns_bounded = start_column is not None or end_column is not None
    if not rows_bounded and not columns_bounded:
        return prefix
    first_row = str(int(start_row or 0) + 1)
    last_row = str(int(end_row)) if end_row is not None else ""
    first_column = column_letter(int(start_column or 0))
    last_column = column_letter(int(end_column) - 1) if end_column is not None else ""
    if not columns_bounded:
        # Whole rows (2:13); rows open to the bottom have no A1 form.
        return f"{prefix}!{first_row}:{last_row}" if last_row else None
    if not last_column:
        # Columns open to the right have no A1 form.
        return None
    if not rows_bounded:
        return f"{prefix}!{first_column}:{last_column}"
    # Rows open to the bottom: 'Sheet1'!B2:C, as the values API accepts.
    return f"{prefix}!{first_column}{first_row}:{last_column}{last_row}"


def _snake_grid_range(grid_range: dict[str, Any]) -> dict[str, Any]:
    return {
        "sheet_id": grid_range.get("sheetId", 0),
        "start_row_index": grid_range.get("startRowIndex"),
        "end_row_index": grid_range.get("endRowIndex"),
        "start_column_index": grid_range.get("startColumnIndex"),
        "end_column_index": grid_range.get("endColumnIndex"),
    }


def _trim_rows(rows: list[list[str]]) -> list[list[str]]:
    """Drop trailing empty cells of each row and trailing empty rows, as the values API does."""
    trimmed: list[list[str]] = []
    for row in rows:
        end = len(row)
        while end and row[end - 1] == "":
            end -= 1
        trimmed.append(row[:end])
    while trimmed and not trimmed[-1]:
        trimmed.pop()
    return trimmed


def grid_values(data: list[dict[str, Any]]) -> tuple[list[list[str]], str | None]:
    """The formatted cell values of a sheet's GridData blocks, and the cell they start at.

    Blocks requested through several ranges are placed on one grid anchored
    at the top-left cell they cover; cells without a value are empty strings.
    """
    blocks = [block for block in data if block.get("rowData")]
    if not blocks:
        return [], None
    top = min(int(block.get("startRow", 0)) for block in blocks)
    left = min(int(block.get("startColumn", 0)) for block in blocks)
    cells: dict[tuple[int, int], str] = {}
    for block in blocks:
        row_offset = int(block.get("startRow", 0)) - top
        column_offset = int(block.get("startColumn", 0)) - left
        for row_index, row in enumerate(block.get("rowData", [])):
            for column_index, cell in enumerate(row.get("values", [])):
                value = cell.get("formattedValue")
                if value is not None and value != "":
                    cells[(row_offset + row_index, column_offset + column_index)] = str(value)
    if not cells:
        return [], None
    height = max(row for row, _ in cells) + 1
    width = max(column for _, column in cells) + 1
    rows = [[cells.get((row, column), "") for column in range(width)] for row in range(height)]
    return _trim_rows(rows), f"{column_letter(left)}{top + 1}"


def sheet_envelope(sheet: dict[str, Any], *, include_values: bool) -> dict[str, Any]:
    properties = sheet.get("properties", {})
    grid = properties.get("gridProperties")
    values, values_start = grid_values(sheet.get("data", [])) if include_values else (None, None)
    return {
        "sheet_id": properties.get("sheetId"),
        "title": properties.get("title"),
        "index": properties.get("index"),
        "type": properties.get("sheetType"),
        "row_count": grid.get("rowCount") if grid is not None else None,
        "column_count": grid.get("columnCount") if grid is not None else None,
        "frozen_row_count": int(grid.get("frozenRowCount", 0)) if grid is not None else None,
        "frozen_column_count": int(grid.get("frozenColumnCount", 0)) if grid is not None else None,
        "hidden": bool(properties.get("hidden", False)),
        "values": values,
        "values_start": values_start,
    }


def named_range_envelope(named_range: dict[str, Any], sheet_titles: dict[int, str]) -> dict[str, Any]:
    grid_range = named_range.get("range") or {}
    a1 = grid_range_a1(grid_range, sheet_titles)
    return {
        "named_range_id": named_range.get("namedRangeId"),
        "name": named_range.get("name"),
        "range": a1 if a1 is not None else _snake_grid_range(grid_range),
    }


def spreadsheet_envelope(payload: dict[str, Any], *, include_grid_data: bool = False) -> dict[str, Any]:
    """A spreadsheet's identity, locale and tabs, with cell values when grid data was fetched."""
    properties = payload.get("properties", {})
    sheets = payload.get("sheets", [])
    titles = {
        int(sheet["properties"].get("sheetId", 0)): sheet["properties"]["title"]
        for sheet in sheets
        if isinstance(sheet.get("properties"), dict) and sheet["properties"].get("title") is not None
    }
    return {
        "spreadsheet_id": payload.get("spreadsheetId"),
        "title": properties.get("title"),
        "locale": properties.get("locale"),
        "spreadsheet_time_zone": properties.get("timeZone"),
        "url": clean_web_url(payload.get("spreadsheetUrl")),
        "sheets": [sheet_envelope(sheet, include_values=include_grid_data) for sheet in sheets],
        "named_ranges": [named_range_envelope(item, titles) for item in payload.get("namedRanges", [])],
    }


def created_spreadsheet_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ok",
        "spreadsheet_id": payload.get("spreadsheetId"),
        "title": payload.get("properties", {}).get("title"),
        "url": clean_web_url(payload.get("spreadsheetUrl")),
        "sheets": [
            {
                "sheet_id": sheet.get("properties", {}).get("sheetId"),
                "title": sheet.get("properties", {}).get("title"),
                "index": sheet.get("properties", {}).get("index"),
            }
            for sheet in payload.get("sheets", [])
        ],
    }


def value_range_envelope(value_range: dict[str, Any]) -> dict[str, Any]:
    """One ValueRange; Google omits ``values`` when the range holds no data."""
    values = value_range.get("values", [])
    return {
        "range": value_range.get("range"),
        "major_dimension": value_range.get("majorDimension"),
        "values": values,
        "row_count": len(values),
    }


def sheet_values_envelope(payload: dict[str, Any], *, spreadsheet_id: str) -> dict[str, Any]:
    values = payload.get("values", [])
    return {
        "spreadsheet_id": spreadsheet_id,
        "range": payload.get("range"),
        "major_dimension": payload.get("majorDimension"),
        "values": values,
        "row_count": len(values),
    }


def batch_sheet_values_envelope(payload: dict[str, Any], *, spreadsheet_id: str) -> dict[str, Any]:
    value_ranges = payload.get("valueRanges", [])
    return {
        "spreadsheet_id": payload.get("spreadsheetId") or spreadsheet_id,
        "value_ranges": [value_range_envelope(item) for item in value_ranges],
        "count": len(value_ranges),
    }


def _updated_values(updates: dict[str, Any]) -> list[list[Any]] | None:
    """The written cells as stored, present only when include_values_in_response was set."""
    data = updates.get("updatedData")
    return data.get("values", []) if isinstance(data, dict) else None


def append_values_envelope(payload: dict[str, Any], *, spreadsheet_id: str) -> dict[str, Any]:
    updates = payload.get("updates", {})
    return {
        "status": "ok",
        "spreadsheet_id": payload.get("spreadsheetId") or spreadsheet_id,
        "table_range": payload.get("tableRange"),
        "updated_range": updates.get("updatedRange"),
        "updated_rows": int(updates.get("updatedRows", 0)),
        "updated_columns": int(updates.get("updatedColumns", 0)),
        "updated_cells": int(updates.get("updatedCells", 0)),
        "updated_values": _updated_values(updates),
    }


def update_values_envelope(payload: dict[str, Any], *, spreadsheet_id: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "spreadsheet_id": payload.get("spreadsheetId") or spreadsheet_id,
        "updated_range": payload.get("updatedRange"),
        "updated_rows": int(payload.get("updatedRows", 0)),
        "updated_columns": int(payload.get("updatedColumns", 0)),
        "updated_cells": int(payload.get("updatedCells", 0)),
        "updated_values": _updated_values(payload),
    }


def batch_update_spreadsheet_envelope(payload: dict[str, Any], *, spreadsheet_id: str) -> dict[str, Any]:
    """A batchUpdate result; ``spreadsheet`` is set only when the caller asked for it."""
    replies = payload.get("replies", [])
    updated = payload.get("updatedSpreadsheet")
    return {
        "status": "ok",
        "spreadsheet_id": payload.get("spreadsheetId") or spreadsheet_id,
        "replies": replies,
        "count": len(replies),
        "spreadsheet": spreadsheet_envelope(updated) if isinstance(updated, dict) else None,
    }
