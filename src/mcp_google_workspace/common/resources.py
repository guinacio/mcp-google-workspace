"""Stable cross-tool Google Workspace resource handles."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, Field
import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import InputRequiredToolResult, ToolResult


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


# Query parameters Google appends to share and view links to attribute the
# click (``usp=drivesdk``, ``ouid=<user id>``); they carry no meaning for the
# link itself. Every other parameter (a Calendar link's ``eid``) is kept.
_TRACKING_PARAMETERS = frozenset({"usp", "ouid", "rtpof", "sd", "pli", "authuser"})


def clean_web_url(url: str | None) -> str | None:
    """Drop tracking query parameters from a Google web link, keeping the rest."""
    if not url:
        return url
    parsed = urlparse(url)
    if not parsed.query:
        return url
    kept = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in _TRACKING_PARAMETERS and not key.startswith("utm_")
    ]
    return urlunparse(parsed._replace(query=urlencode(kept)))


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
    """A stable handle for one Workspace resource; fields that are unknown are omitted."""
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
        web_url=clean_web_url(web_url),
    )
    return handle.model_dump(exclude_none=True)


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
    "contacts": "contact",
    "tasks": "task",
}

# Lists named for what they group rather than for the resource, per namespace.
_NAMESPACE_CONTAINER_KINDS = {
    "tasks": {"overdue": "task", "upcoming": "task", "unscheduled": "task"},
    "calendar": {"requires_response": "calendar_event"},
}

# Keys that hold one resource of a kind, e.g. ``{"file": {...}}``.
_SINGLE_KINDS = {
    "file": "drive_file",
    "event": "calendar_event",
    "contact": "contact",
    "task": "task",
}

# Handle fields are left out when the item already carries the same fact under
# one of these keys: a handle attached to an item adds only what it lacks.
_ITEM_EQUIVALENTS = {
    "name": ("name", "title", "summary"),
    "mime_type": ("mime_type", "mimeType"),
    "modified_at": ("modified_at", "updated_at", "modifiedTime", "updated"),
    "web_url": ("web_url", "url", "webViewLink", "htmlLink"),
    "etag": ("etag",),
}


def _handle_for(kind: str, item: dict[str, Any]) -> dict[str, Any] | None:
    if kind == "contact":
        resource_id = item.get("resource_name") or item.get("resourceName")
    elif kind == "task" and "status" not in item:
        # A task always has a status; a task list (same namespace, also an
        # ``id``) does not, and must not get a handle to a task that does not exist.
        return None
    else:
        resource_id = item.get("id")
    if not isinstance(resource_id, str) or not resource_id:
        return None

    def missing(field: str) -> bool:
        return not any(key in item for key in _ITEM_EQUIVALENTS[field])

    return resource_handle(
        kind,
        resource_id,
        name=(item.get("name") or item.get("summary") or item.get("title")) if missing("name") else None,
        mime_type=(item.get("mimeType") or item.get("mime_type")) if missing("mime_type") else None,
        etag=item.get("etag") if missing("etag") else None,
        modified_at=(item.get("modifiedTime") or item.get("updated")) if missing("modified_at") else None,
        web_url=(item.get("webViewLink") or item.get("htmlLink")) if missing("web_url") else None,
    )


def _attach(item: Any, kind: str) -> None:
    if isinstance(item, dict) and "resource" not in item:
        handle = _handle_for(kind, item)
        if handle is not None:
            item["resource"] = handle


def add_resource_handles(payload: dict[str, Any], namespace: str) -> None:
    """Attach a ``resource`` handle to every Workspace resource in a result.

    The handle is the cross-tool reference (``uri`` for
    resolve_workspace_resource); attached to an item it carries only the kind,
    id and uri plus whatever the item does not already say itself.
    """
    root_kinds = {
        "drive": "drive_file",
        "gmail": "gmail_message",
        "calendar": "calendar_event",
        "people": "contact",
        "tasks": "task",
    }
    if namespace in root_kinds:
        _attach(payload, root_kinds[namespace])
    containers = {**_CONTAINER_KINDS, **_NAMESPACE_CONTAINER_KINDS.get(namespace, {})}
    for key, kind in containers.items():
        items = payload.get(key)
        if isinstance(items, list):
            for item in items:
                _attach(item, kind)
    for key, kind in _SINGLE_KINDS.items():
        _attach(payload.get(key), kind)


class ResourceHandleMiddleware(Middleware):
    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        result = await call_next(context)
        # Only a completed tool result carries business payload. A tasked call
        # yields the Tasks extension's CreateTaskResult receipt, and a
        # multi-round-trip guard yields an InputRequiredToolResult; neither may
        # be decorated with resource handles.
        if not isinstance(result, ToolResult) or isinstance(result, InputRequiredToolResult):
            return result
        payload = result.structured_content
        if isinstance(payload, dict):
            namespace = context.message.name.split("_", 1)[0]
            add_resource_handles(payload, namespace)
        return result
