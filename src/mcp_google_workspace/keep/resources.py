"""MCP resources for Google Keep data."""

from __future__ import annotations

import json

from fastmcp import FastMCP

from ..common.async_ops import execute_google_request
from .client import keep_service, normalize_note_name


def register_resources(server: FastMCP) -> None:
    @server.resource("keep://notes/recent", name="keep_recent_notes")
    async def keep_recent_notes() -> str:
        service = keep_service()
        result = await execute_google_request(service.notes().list(pageSize=20))
        return json.dumps(result, indent=2)

    @server.resource("keep://note/{note_id}", name="keep_note_by_id")
    async def keep_note_by_id(note_id: str) -> str:
        service = keep_service()
        name = normalize_note_name(note_id)
        note = await execute_google_request(service.notes().get(name=name))
        return json.dumps(note, indent=2)
