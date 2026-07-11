"""Composed Google Workspace FastMCP server."""

from __future__ import annotations

import anyio
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .apps import apps_mcp
from .common.component_annotations import apply_default_tool_annotations
from .common.errors import StructuredToolErrorMiddleware
from .common.production import (
    CapabilityCatalogMiddleware,
    ConsequentialActionMiddleware,
    RUNTIME_STATE,
    ProductionControlMiddleware,
    build_version_payload,
    production_lifespan,
    readiness_report,
)
from .auth import (
    is_apps_dashboard_enabled,
    is_chat_enabled,
    is_gemini_enabled,
    is_keep_enabled,
    is_meet_enabled,
)
from .auth.google_oauth import register_connection_tools
from .auth.google_auth import (
    CAPABILITY_SCOPES,
    build_drive_service,
    build_gmail_service,
    build_people_service,
)
from .common.async_ops import execute_google_request
from .common.resources import ResourceHandleMiddleware, parse_resource_uri, resource_handle
from .common.approvals import APPROVAL_STORE, COMMIT_ACTIVE
from .calendar import calendar_mcp
from .chat import chat_mcp
from .docs import docs_mcp
from .drive import drive_mcp
from .forms import forms_mcp
from .file_uploads import workspace_file_upload
from .gemini import gemini_mcp
from .gmail import gmail_mcp
from .keep import keep_mcp
from .meet import meet_mcp
from .people import people_mcp
from .sheets import sheets_mcp
from .slides import slides_mcp
from .tasks import tasks_mcp

workspace_mcp = FastMCP(
    name="google-workspace-mcp",
    instructions=(
        "Unified Google Workspace MCP server with Gmail, Calendar, Drive, Docs, Sheets, "
        "Tasks, People, Forms, Slides, and optional Meet/Keep/Chat/Gemini integrations."
    ),
    lifespan=production_lifespan,
)
workspace_mcp.add_middleware(StructuredToolErrorMiddleware())
workspace_mcp.add_middleware(ProductionControlMiddleware())
workspace_mcp.add_middleware(CapabilityCatalogMiddleware())
workspace_mcp.add_middleware(ConsequentialActionMiddleware())
workspace_mcp.add_middleware(ResourceHandleMiddleware())


@workspace_mcp.custom_route("/health/live", methods=["GET"], include_in_schema=False)
async def health_live(_: Request) -> Response:
    return JSONResponse({"status": "ok", "uptime_seconds": int(__import__("time").time() - RUNTIME_STATE.started_at)})


@workspace_mcp.custom_route("/health/ready", methods=["GET"], include_in_schema=False)
async def health_ready(_: Request) -> Response:
    ready, payload = await anyio.to_thread.run_sync(readiness_report)
    return JSONResponse(
        {**payload, "active_requests": RUNTIME_STATE.active_requests},
        status_code=200 if ready else 503,
    )


@workspace_mcp.custom_route("/version", methods=["GET"], include_in_schema=False)
async def version_info(_: Request) -> Response:
    return JSONResponse(build_version_payload())


@workspace_mcp.custom_route("/metrics", methods=["GET"], include_in_schema=False)
async def metrics(_: Request) -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

workspace_mcp.add_provider(workspace_file_upload, namespace="files")

workspace_mcp.mount(gmail_mcp, namespace="gmail")
workspace_mcp.mount(calendar_mcp, namespace="calendar")
workspace_mcp.mount(drive_mcp, namespace="drive")
workspace_mcp.mount(sheets_mcp, namespace="sheets")
workspace_mcp.mount(docs_mcp, namespace="docs")
workspace_mcp.mount(tasks_mcp, namespace="tasks")
workspace_mcp.mount(people_mcp, namespace="people")
workspace_mcp.mount(forms_mcp, namespace="forms")
workspace_mcp.mount(slides_mcp, namespace="slides")
if is_apps_dashboard_enabled():
    workspace_mcp.mount(apps_mcp, namespace="apps")
if is_chat_enabled():
    workspace_mcp.mount(chat_mcp, namespace="chat")
if is_gemini_enabled():
    workspace_mcp.mount(gemini_mcp, namespace="gemini")
if is_keep_enabled():
    workspace_mcp.mount(keep_mcp, namespace="keep")
if is_meet_enabled():
    workspace_mcp.mount(meet_mcp, namespace="meet")

register_connection_tools(workspace_mcp)


@workspace_mcp.tool(name="get_workspace_capabilities")
def get_workspace_capabilities() -> dict[str, object]:
    """Describe available namespaces, consent capabilities, and file-transfer choices."""
    enabled = [
        "gmail",
        "calendar",
        "drive",
        "sheets",
        "docs",
        "tasks",
        "people",
        "forms",
        "slides",
    ]
    optional = {
        "apps": is_apps_dashboard_enabled(),
        "chat": is_chat_enabled(),
        "gemini": is_gemini_enabled(),
        "keep": is_keep_enabled(),
        "meet": is_meet_enabled(),
    }
    enabled.extend(name for name, active in optional.items() if active)
    return {
        "status": "ok",
        "enabled_namespaces": sorted(enabled),
        "optional_namespaces": optional,
        "oauth_capabilities": sorted(CAPABILITY_SCOPES),
        "file_inputs": {
            "remote": "Call files_file_manager, then pass uploaded_file.",
            "local": "Trusted stdio clients may pass local filesystem paths.",
            "drive": "Gemini media tools also accept drive_file_id.",
        },
        "discovery": "Remote HTTP uses capability-aware progressive tool discovery.",
    }


@workspace_mcp.tool(name="search_workspace")
async def search_workspace(
    query: str,
    services: list[str] | None = None,
    max_results_per_service: int = 5,
) -> dict[str, object]:
    """Search Drive files, contacts, and Gmail IDs through one normalized entry point."""
    selected = services or ["drive", "people", "gmail"]
    unknown = sorted(set(selected) - {"drive", "people", "gmail"})
    if unknown:
        raise ValueError(f"Unsupported search services: {', '.join(unknown)}")
    if not query.strip():
        raise ValueError("query must not be empty")
    if not 1 <= max_results_per_service <= 25:
        raise ValueError("max_results_per_service must be between 1 and 25")
    matches: list[dict[str, object]] = []
    errors: dict[str, str] = {}

    async def search_drive() -> None:
        escaped = query.replace("'", "\\'")
        try:
            result = await execute_google_request(
                build_drive_service()
                .files()
                .list(
                    q=f"name contains '{escaped}' and trashed = false",
                    pageSize=max_results_per_service,
                    fields="files(id,name,mimeType,webViewLink,modifiedTime)",
                )
            )
            matches.extend(
                {
                    "service": "drive",
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "kind": item.get("mimeType"),
                    "url": item.get("webViewLink"),
                    "modified_at": item.get("modifiedTime"),
                    "resource": resource_handle(
                        "drive_file",
                        str(item.get("id")),
                        name=item.get("name"),
                        mime_type=item.get("mimeType"),
                        modified_at=item.get("modifiedTime"),
                        web_url=item.get("webViewLink"),
                    ),
                }
                for item in result.get("files", [])
            )
        except Exception as exc:
            errors["drive"] = str(exc)

    async def search_people() -> None:
        try:
            service = build_people_service()
            await execute_google_request(
                service.people().searchContacts(
                    query="", readMask="names,emailAddresses", pageSize=1
                )
            )
            result = await execute_google_request(
                service.people().searchContacts(
                    query=query,
                    readMask="names,emailAddresses,phoneNumbers,organizations",
                    pageSize=max_results_per_service,
                )
            )
            for result_item in result.get("results", []):
                person = result_item.get("person", {})
                names = person.get("names", [])
                emails = person.get("emailAddresses", [])
                matches.append(
                    {
                        "service": "people",
                        "id": person.get("resourceName"),
                        "name": names[0].get("displayName") if names else None,
                        "kind": "contact",
                        "email": emails[0].get("value") if emails else None,
                        "resource": resource_handle(
                            "contact",
                            str(person.get("resourceName")),
                            name=names[0].get("displayName") if names else None,
                        ),
                    }
                )
        except Exception as exc:
            errors["people"] = str(exc)

    async def search_gmail() -> None:
        try:
            result = await execute_google_request(
                build_gmail_service()
                .users()
                .messages()
                .list(userId="me", q=query, maxResults=max_results_per_service)
            )
            matches.extend(
                {
                    "service": "gmail",
                    "id": item.get("id"),
                    "name": item.get("threadId"),
                    "kind": "message",
                    "resource": resource_handle(
                        "gmail_message", str(item.get("id")), name=item.get("threadId")
                    ),
                }
                for item in result.get("messages", [])
            )
        except Exception as exc:
            errors["gmail"] = str(exc)

    searchers = {
        "drive": search_drive,
        "people": search_people,
        "gmail": search_gmail,
    }
    async with anyio.create_task_group() as task_group:
        for service_name in selected:
            task_group.start_soon(searchers[service_name])
    return {
        "status": "ok" if not errors else "partial",
        "query": query,
        "services": selected,
        "matches": matches,
        "count": len(matches),
        "errors": errors,
    }


@workspace_mcp.tool(name="resolve_workspace_resource")
async def resolve_workspace_resource(uri: str) -> dict[str, object]:
    """Resolve a stable Workspace URI to fresh compact metadata."""
    kind, resource_id = parse_resource_uri(uri)
    if kind == "drive_file":
        item = await execute_google_request(
            build_drive_service().files().get(
                fileId=resource_id,
                fields="id,name,mimeType,modifiedTime,webViewLink,etag",
            )
        )
        return {
            "status": "ok",
            "resource": resource_handle(
                kind,
                resource_id,
                name=item.get("name"),
                mime_type=item.get("mimeType"),
                modified_at=item.get("modifiedTime"),
                web_url=item.get("webViewLink"),
                etag=item.get("etag"),
            ),
        }
    if kind == "gmail_message":
        item = await execute_google_request(
            build_gmail_service().users().messages().get(
                userId="me", id=resource_id, format="metadata"
            )
        )
        return {
            "status": "ok",
            "resource": resource_handle(kind, resource_id, name=item.get("threadId")),
        }
    return {
        "status": "resolved",
        "resource": resource_handle(kind, resource_id),
        "next_actions": ["Use the matching namespace tool with resource.id."],
    }


@workspace_mcp.tool(name="prepare_workspace_action")
def prepare_workspace_action(
    tool_name: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    """Preview and bind one consequential action to a short-lived one-time commit token."""
    return APPROVAL_STORE.prepare(tool_name, arguments)


@workspace_mcp.tool(name="commit_workspace_action")
async def commit_workspace_action(commit_token: str) -> dict[str, object]:
    """Atomically consume a prepared action token and execute its exact bound arguments."""
    tool_name, arguments = APPROVAL_STORE.consume(commit_token)
    active_token = COMMIT_ACTIVE.set(True)
    try:
        result = await workspace_mcp.call_tool(tool_name, arguments, run_middleware=False)
    finally:
        COMMIT_ACTIVE.reset(active_token)
    return {
        "status": "committed",
        "tool": tool_name,
        "result": result.structured_content or {
            "content": [item.model_dump() for item in result.content]
        },
    }
apply_default_tool_annotations(workspace_mcp)
