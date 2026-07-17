"""Composed Google Workspace FastMCP server."""

from __future__ import annotations

import anyio
import base64
import os
import secrets
from typing import Annotated
from fastmcp import FastMCP
from fastmcp.server.providers.addressing import hash_tool, hashed_resource_uri
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, Field

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
from .common.approvals import APPROVAL_STORE, COMMIT_ACTIVE, CONSEQUENTIAL_TOOLS
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


class McpAppsCallbacks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    store_files: str = Field(description="Hidden app-callable upload tool address.")
    delete_file: str = Field(description="Hidden app-callable delete tool address.")


class McpAppsSelfTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(description="Self-test execution status.")
    stored_handle: str | None = Field(
        default=None, description="Temporary handle created by a successful self-test."
    )
    delete_result: dict[str, object] | None = Field(
        default=None, description="Result returned by the hidden delete callback."
    )


class McpAppsDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(description="Machine-readable diagnostic status.")
    apps_enabled: bool = Field(description="Whether the file-picker Apps provider is mounted.")
    picker_tool: str = Field(description="Model-visible tool that opens the file picker.")
    resource_uri: str = Field(description="MCP Apps UI resource URI advertised by the picker.")
    resource_mime_type: str = Field(description="MIME type served for the UI resource.")
    renderer_mode: str = Field(description="Prefab renderer delivery mode.")
    hidden_callbacks: McpAppsCallbacks = Field(
        description="App-callable backend tool addresses hidden from the model catalog."
    )
    max_file_bytes: int = Field(
        ge=1, description="Maximum decoded size accepted for one picker upload."
    )
    self_test: McpAppsSelfTest = Field(
        description="Optional store/delete callback self-test result."
    )


@workspace_mcp.tool(name="get_mcp_apps_diagnostics")
async def get_mcp_apps_diagnostics(
    run_self_test: bool = False,
) -> McpAppsDiagnostics:
    """Describe the file-picker MCP Apps contract and optionally verify callbacks."""
    picker_name = "files_file_manager"
    store_address = f"{hash_tool('Workspace Files', 'store_files')}_store_files"
    delete_address = f"{hash_tool('Workspace Files', 'delete_file')}_delete_file"
    resource_uri = str(hashed_resource_uri("Workspace Files", "file_manager"))
    renderer_mode = (
        "bundled"
        if os.getenv("PREFAB_BUNDLED_RENDERER", "").strip()
        else "external" if os.getenv("PREFAB_RENDERER_URL", "").strip() else "cdn"
    )
    result: dict[str, object] = {
        "status": "ok",
        "apps_enabled": True,
        "picker_tool": picker_name,
        "resource_uri": resource_uri,
        "resource_mime_type": "text/html;profile=mcp-app",
        "renderer_mode": renderer_mode,
        "hidden_callbacks": {
            "store_files": store_address,
            "delete_file": delete_address,
        },
        "max_file_bytes": 25 * 1024 * 1024,
        "self_test": {"status": "not_run"},
    }
    if not run_self_test:
        return McpAppsDiagnostics.model_validate(result)

    filename = f"mcp-app-self-test-{secrets.token_hex(8)}.txt"
    stored = await workspace_mcp.call_tool(
        store_address,
        {
            "files": [
                {
                    "name": filename,
                    "size": 0,
                    "type": "text/plain",
                    "data": base64.b64encode(b"").decode("ascii"),
                }
            ]
        },
    )
    payload = stored.structured_content or {}
    entries = payload.get("result", payload) if isinstance(payload, dict) else payload
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("The MCP Apps storage callback returned an invalid result.")
    self_test_entry = next(
        (
            entry
            for entry in entries
            if isinstance(entry, dict)
            and (entry.get("display_name") == filename or entry.get("name") == filename)
        ),
        None,
    )
    if self_test_entry is None:
        raise RuntimeError(
            "The MCP Apps storage callback did not return the temporary self-test file."
        )
    handle = str(
        self_test_entry.get("name") or self_test_entry.get("upload_id") or ""
    )
    if not handle:
        raise RuntimeError("The MCP Apps storage callback returned no file handle.")
    deleted = await workspace_mcp.call_tool(delete_address, {"name": handle})
    result["self_test"] = {
        "status": "passed",
        "stored_handle": handle,
        "delete_result": deleted.structured_content,
    }
    return McpAppsDiagnostics.model_validate(result)


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
        "discovery": (
            "FastMCP BM25 Tool Search is enabled by default for HTTP and stdio. "
            "Set MCP_CLIENT_MODEL=claude to disable it automatically in auto mode, "
            "or use MCP_TOOL_SEARCH=on|off explicitly."
        ),
    }


@workspace_mcp.tool(name="search_workspace")
async def search_workspace(
    query: str,
    services: Annotated[
        list[str] | None,
        (
            "Subset of services to search: any of 'drive', 'people', 'gmail'; "
            "omit to search all three."
        ),
    ] = None,
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
async def resolve_workspace_resource(
    uri: Annotated[
        str,
        "Stable Workspace resource URI previously returned in a tool response's 'resource' field.",
    ],
) -> dict[str, object]:
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
    tool_name: Annotated[
        str,
        (
            "Full name of the consequential tool to prepare; must be one of "
            f"{sorted(CONSEQUENTIAL_TOOLS)}. Other tools do not use the prepare/commit protocol."
        ),
    ],
    arguments: Annotated[
        dict[str, object],
        "Exact keyword arguments to bind and later execute unchanged for tool_name.",
    ],
) -> dict[str, object]:
    """Preview and bind one consequential action to a short-lived one-time commit token."""
    return APPROVAL_STORE.prepare(tool_name, arguments)


@workspace_mcp.tool(name="commit_workspace_action")
async def commit_workspace_action(
    commit_token: Annotated[
        str,
        (
            "One-time, principal-bound token from prepare_workspace_action's response; "
            "expires 5 minutes after issuance and is consumed on first use."
        ),
    ],
) -> dict[str, object]:
    """Atomically consume a prepared action token and execute its exact bound arguments."""
    tool_name, arguments = APPROVAL_STORE.consume(commit_token)
    active_token = COMMIT_ACTIVE.set(True)
    try:
        # Re-enter the complete middleware chain so revocation, admission,
        # deadlines, input limits, handle resolution, telemetry, and structured
        # errors still apply at commit time. COMMIT_ACTIVE bypasses only the
        # consequential-action gate for this exact bound invocation.
        result = await workspace_mcp.call_tool(tool_name, arguments)
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
