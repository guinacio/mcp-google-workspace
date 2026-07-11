"""Shared request model behavior for MCP tools."""

from __future__ import annotations

from pydantic import BaseModel


class ToolRequestModel(BaseModel):
    """Base input model for MCP tools expecting object payloads."""

    model_config = {
        "extra": "forbid",
        "populate_by_name": True,
        "json_schema_extra": {
            "description": (
                "Pass this as a JSON object payload to the tool. "
                "Do not pass a raw string for the full request."
            )
        },
    }
