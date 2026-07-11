"""Stable cross-tool Google Workspace resource handles."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote, unquote, urlparse

from pydantic import BaseModel, ConfigDict, Field
import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult


class ResourceHandle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "drive_file", "gmail_message", "gmail_thread", "calendar_event", "contact", "task"
    ]
    id: str = Field(min_length=1)
    name: str | None = None
    mime_type: str | None = None
    uri: str
    etag: str | None = None
    modified_at: str | None = None
    web_url: str | None = None


_SCHEMES = {
    "drive_file": "gdrive",
    "gmail_message": "gmail-message",
    "gmail_thread": "gmail-thread",
    "calendar_event": "gcal-event",
    "contact": "gcontact",
    "task": "gtask",
}


def resource_handle(
    kind: str,
    resource_id: str,
    *,
    name: str | None = None,
    mime_type: str | None = None,
    etag: str | None = None,
    modified_at: str | None = None,
    web_url: str | None = None,
) -> dict[str, Any]:
    if kind not in _SCHEMES:
        raise ValueError(f"Unsupported resource kind: {kind}")
    handle = ResourceHandle(
        kind=kind,  # type: ignore[arg-type]
        id=resource_id,
        name=name,
        mime_type=mime_type,
        uri=f"{_SCHEMES[kind]}:///{quote(resource_id, safe='')}",
        etag=etag,
        modified_at=modified_at,
        web_url=web_url,
    )
    return handle.model_dump()


def parse_resource_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    reverse = {scheme: kind for kind, scheme in _SCHEMES.items()}
    kind = reverse.get(parsed.scheme)
    resource_id = unquote(parsed.path.lstrip("/") or parsed.netloc)
    if kind is None or not resource_id:
        raise ValueError("Unsupported Workspace resource URI.")
    return kind, resource_id


_CONTAINER_KINDS = {
    "files": "drive_file",
    "messages": "gmail_message",
    "threads": "gmail_thread",
    "events": "calendar_event",
    "connections": "contact",
    "tasks": "task",
}


def _handle_for(kind: str, item: dict[str, Any]) -> dict[str, Any] | None:
    resource_id = item.get("resourceName") if kind == "contact" else item.get("id")
    if not isinstance(resource_id, str) or not resource_id:
        return None
    return resource_handle(
        kind,
        resource_id,
        name=item.get("name") or item.get("summary") or item.get("title") or item.get("threadId"),
        mime_type=item.get("mimeType") or item.get("mime_type"),
        etag=item.get("etag"),
        modified_at=item.get("modifiedTime") or item.get("updated"),
        web_url=item.get("webViewLink") or item.get("htmlLink"),
    )


def add_resource_handles(payload: dict[str, Any], namespace: str) -> None:
    root_kinds = {
        "drive": "drive_file",
        "gmail": "gmail_message",
        "calendar": "calendar_event",
        "people": "contact",
        "tasks": "task",
    }
    if "resource" not in payload and namespace in root_kinds:
        handle = _handle_for(root_kinds[namespace], payload)
        if handle is not None:
            payload["resource"] = handle
    for key, kind in _CONTAINER_KINDS.items():
        items = payload.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and "resource" not in item:
                handle = _handle_for(kind, item)
                if handle is not None:
                    item["resource"] = handle


class ResourceHandleMiddleware(Middleware):
    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        result = await call_next(context)
        payload = result.structured_content
        if isinstance(payload, dict):
            namespace = context.message.name.split("_", 1)[0]
            add_resource_handles(payload, namespace)
        return result
