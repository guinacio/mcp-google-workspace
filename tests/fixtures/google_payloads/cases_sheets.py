"""Golden cases for the sheets_ response normalizers."""

from __future__ import annotations

from mcp_google_workspace.sheets.presentation import (
    append_values_envelope,
    batch_sheet_values_envelope,
    batch_update_spreadsheet_envelope,
    created_spreadsheet_envelope,
    raw_spreadsheet_view,
    sheet_values_envelope,
    spreadsheet_envelope,
    update_values_envelope,
)

from . import Case

SPREADSHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdefghijk"

CASES: list[Case] = [
    Case("sheets_get_spreadsheet", "sheets_get_spreadsheet.json", lambda payload: spreadsheet_envelope(payload), items_key="sheets"),
    Case(
        "sheets_get_spreadsheet_grid_data",
        "sheets_get_spreadsheet_grid_data.json",
        lambda payload: spreadsheet_envelope(payload, include_grid_data=True),
        items_key="sheets",
    ),
    Case("sheets_get_spreadsheet_minimal", "sheets_get_spreadsheet_minimal.json", lambda payload: spreadsheet_envelope(payload)),
    # format=raw: Google's resource unchanged. sheets_get_spreadsheet_formatting.json is
    # hand-built from the Sheets API reference (charts, merges, filters, theme, cell formats).
    Case("sheets_get_spreadsheet_raw", "sheets_get_spreadsheet.json", raw_spreadsheet_view, opaque_keys=("spreadsheet",)),
    Case(
        "sheets_get_spreadsheet_raw_formatting",
        "sheets_get_spreadsheet_formatting.json",
        raw_spreadsheet_view,
        opaque_keys=("spreadsheet",),
    ),
    Case("sheets_create_spreadsheet", "sheets_create_spreadsheet.json", created_spreadsheet_envelope, items_key="sheets"),
    Case(
        "sheets_get_sheet_values",
        "sheets_get_sheet_values.json",
        lambda payload: sheet_values_envelope(payload, spreadsheet_id=SPREADSHEET_ID),
        items_key="values",
    ),
    Case(
        "sheets_get_sheet_values_empty",
        "sheets_get_sheet_values_empty.json",
        lambda payload: sheet_values_envelope(payload, spreadsheet_id=SPREADSHEET_ID),
    ),
    Case(
        "sheets_batch_get_sheet_values",
        "sheets_batch_get_sheet_values.json",
        lambda payload: batch_sheet_values_envelope(payload, spreadsheet_id=SPREADSHEET_ID),
        items_key="value_ranges",
    ),
    Case(
        "sheets_append_sheet_values",
        "sheets_append_sheet_values.json",
        lambda payload: append_values_envelope(payload, spreadsheet_id=SPREADSHEET_ID),
    ),
    Case(
        "sheets_update_sheet_values",
        "sheets_update_sheet_values.json",
        lambda payload: update_values_envelope(payload, spreadsheet_id=SPREADSHEET_ID),
    ),
    Case(
        "sheets_batch_update_spreadsheet",
        "sheets_batch_update_spreadsheet.json",
        lambda payload: batch_update_spreadsheet_envelope(payload, spreadsheet_id=SPREADSHEET_ID),
        opaque_keys=("replies",),
    ),
    Case(
        "sheets_batch_update_spreadsheet_minimal",
        "sheets_batch_update_spreadsheet_minimal.json",
        lambda payload: batch_update_spreadsheet_envelope(payload, spreadsheet_id="fleet-sheet"),
        opaque_keys=("replies",),
    ),
]
