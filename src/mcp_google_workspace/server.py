"""Composed Google Workspace FastMCP server."""

from __future__ import annotations

import anyio
import base64
import os
import secrets
from typing import Annotated, Any, Literal, get_args
from fastmcp import FastMCP
from googleapiclient.errors import HttpError
from fastmcp.tools import InputRequiredToolResult, ToolResult
import mcp_types
from fastmcp.server.providers.addressing import hash_tool, hashed_resource_uri
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .apps import apps_mcp, mount_apps_dashboard
from .common.component_annotations import apply_default_tool_annotations
from .common.confirmation import REQUEST_STATE_AUDIENCE, build_request_state_security
from .common.errors import RecoverableToolError, StructuredToolErrorMiddleware
from .common.task_backend import install_tasks_extension
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
from .auth.identity import current_principal
from .auth.google_auth import (
    CAPABILITY_SCOPES,
    build_drive_service,
    build_gmail_service,
    build_people_service,
)
from .common.async_ops import execute_google_request
from .common.resources import (
    ResourceHandleMiddleware,
    clean_web_url,
    parse_resource_uri,
    resource_handle,
)
from .common.timezone import in_account_timezone, resolve_user_timezone
from .drive.presentation import file_kind
from .gmail.helpers import gather_in_order
from .gmail.presentation import envelope as gmail_envelope
from .common.approvals import (
    COMMIT_ACTIVE,
    CONSEQUENTIAL_TOOLS,
    INVALID_COMMIT,
    prepare_action,
)
from .common.operations import (
    OperationOutcomeMiddleware,
    get_operation_store,
    operation_key,
    operation_ref,
    resolve_claim,
)
from .common.repeat_safety import current_tracker
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
    # serverInfo.version on the wire is this package's release, not FastMCP's.
    version=__version__,
    instructions=(
        "Unified Google Workspace MCP server with Gmail, Calendar, Drive, Docs, Sheets, "
        "Tasks, People, Forms, Slides, and optional Meet/Keep/Chat/Gemini integrations."
    ),
    lifespan=production_lifespan,
    # Seals multi-round-trip continuation state (confirmations). Shared across
    # replicas via MCP_REQUEST_STATE_KEYS; ephemeral per process otherwise.
    request_state_security=build_request_state_security(audience=REQUEST_STATE_AUDIENCE),
)
# One root-managed Tasks extension (and therefore one queue/worker) for every
# runnable entrypoint: HTTP, stdio bundle, and out-of-process task workers all
# serve this object. Mounted namespaces defer to the root's extension.
install_tasks_extension(workspace_mcp)
workspace_mcp.add_middleware(StructuredToolErrorMiddleware())
# Between the error envelope and the deadline: a deadline or failure while a
# non-repeatable Google call may have been applied becomes outcome_unknown.
workspace_mcp.add_middleware(OperationOutcomeMiddleware())
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
    mount_apps_dashboard(workspace_mcp, apps_mcp, namespace="apps")
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
    run_self_test: Annotated[
        bool,
        (
            "When true, stores an empty mcp-app-self-test-<random>.txt upload in the caller's upload area "
            "through the hidden store_files callback, then deletes it through delete_file, and reports "
            "self_test status 'passed' with the handle and delete result. False (the default) touches no "
            "storage and reports self_test status 'not_run'."
        ),
    ] = False,
) -> McpAppsDiagnostics:
    """Report how the Workspace Files upload picker (files_file_manager) is wired for MCP Apps hosts, to
    troubleshoot uploads that never arrive. Returns picker_tool, resource_uri, resource_mime_type, renderer_mode,
    hidden_callbacks (the hashed addresses of the store_files and delete_file callbacks), max_file_bytes and self_test.
    With run_self_test the call fails if the store callback returns no usable file handle."""
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
    """Describe what this server offers without calling Google: enabled tool namespaces, which optional integrations
    are on, the capability names connect_google_workspace accepts, and how to pass files to tools. For the user's
    actual grants use get_google_connection_status instead. Returns enabled_namespaces, optional_namespaces,
    oauth_capabilities, file_inputs and discovery."""
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


# Metadata the search hit needs from each Gmail message; no body is fetched.
_GMAIL_HIT_FIELDS = "id,threadId,labelIds,snippet,internalDate,payload/headers"


def _escape_drive_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


@workspace_mcp.tool(name="search_workspace")
async def search_workspace(
    query: Annotated[
        str,
        (
            "Search text, must not be blank. Drive matches it against file names and content (fullText "
            "contains, plus name contains for name prefixes), contacts by prefix of names, emails, phone "
            "numbers or organizations, and Gmail treats it as a Gmail search query, e.g. 'from:alice invoice'."
        ),
    ],
    services: Annotated[
        list[Literal["drive", "people", "gmail"]] | None,
        (
            "Services to search: any of 'drive', 'people' (the user's saved contacts) and 'gmail'; "
            "omit to search all three."
        ),
    ] = None,
    max_results_per_service: Annotated[
        int, "Maximum hits from each searched service, 1 to 25."
    ] = 5,
) -> dict[str, object]:
    """Search Drive, the user's saved contacts and Gmail with one query, for a first look when the right service is
    unclear. Drive matches names and content (name matches first, no shared drives); Gmail takes Gmail query syntax and
    each hit is read in one metadata request. For full queries and paging use drive_list_files, people_search_contacts
    or gmail_search_emails. Returns matches (service, id, kind, resource; Drive name, mime_type, modified_at, web_url;
    contact name, email; Gmail thread_id, subject, from, date, snippet, unread), count, account_timezone and errors per
    failed service; the call fails only when every service fails."""
    selected = services or ["drive", "people", "gmail"]
    unknown = sorted(set(selected) - {"drive", "people", "gmail"})
    if unknown:
        raise ValueError(f"Unsupported search services: {', '.join(unknown)}")
    if not query.strip():
        raise ValueError("query must not be empty")
    if not 1 <= max_results_per_service <= 25:
        raise ValueError("max_results_per_service must be between 1 and 25")
    found: dict[str, list[dict[str, object]]] = {}
    errors: dict[str, str] = {}
    resolved_timezone: list[str] = []
    timezone_lock = anyio.Lock()

    async def account_timezone() -> str:
        async with timezone_lock:
            if not resolved_timezone:
                resolved_timezone.append(await resolve_user_timezone())
        return resolved_timezone[0]

    async def search_drive() -> list[dict[str, object]]:
        escaped = _escape_drive_query(query)
        result = await execute_google_request(
            build_drive_service()
            .files()
            .list(
                q=f"(fullText contains '{escaped}' or name contains '{escaped}') and trashed = false",
                pageSize=max_results_per_service,
                fields="files(id,name,mimeType,webViewLink,modifiedTime)",
            )
        )
        zone = await account_timezone()
        needle = query.strip().casefold()
        files = result.get("files", [])
        # Drive orders fullText results by relevance; a name match ranks first.
        files = sorted(files, key=lambda item: needle not in str(item.get("name") or "").casefold())
        return [
            {
                "service": "drive",
                "id": item.get("id"),
                "kind": file_kind(item.get("mimeType")),
                "name": item.get("name"),
                "mime_type": item.get("mimeType"),
                "modified_at": in_account_timezone(item.get("modifiedTime"), zone),
                "web_url": clean_web_url(item.get("webViewLink")),
                "resource": resource_handle("drive_file", str(item.get("id"))),
            }
            for item in files
        ]

    async def search_people() -> list[dict[str, object]]:
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
        hits: list[dict[str, object]] = []
        for result_item in result.get("results", []):
            person = result_item.get("person", {})
            names = person.get("names", [])
            emails = person.get("emailAddresses", [])
            hits.append(
                {
                    "service": "people",
                    "id": person.get("resourceName"),
                    "kind": "contact",
                    "name": names[0].get("displayName") if names else None,
                    "email": emails[0].get("value") if emails else None,
                    "resource": resource_handle("contact", str(person.get("resourceName"))),
                }
            )
        return hits

    async def search_gmail() -> list[dict[str, object]]:
        service = build_gmail_service()
        listed = await execute_google_request(
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results_per_service)
        )
        zone = await account_timezone()

        async def metadata(message_id: str) -> dict[str, Any] | None:
            try:
                return await execute_google_request(
                    service.users().messages().get(
                        userId="me",
                        id=message_id,
                        format="metadata",
                        metadataHeaders=["From", "Subject", "Date"],
                        fields=_GMAIL_HIT_FIELDS,
                    )
                )
            except HttpError as exc:
                if getattr(exc.resp, "status", None) != 404:
                    raise
                return None  # deleted between the search and the read

        messages = await gather_in_order(
            [str(item.get("id")) for item in listed.get("messages", [])], metadata
        )
        hits: list[dict[str, object]] = []
        for message in messages:
            if message is None:
                continue
            item = gmail_envelope(message, account_timezone=zone)
            hits.append(
                {
                    "service": "gmail",
                    "id": item["id"],
                    "kind": "message",
                    "thread_id": item["thread_id"],
                    "subject": item["subject"],
                    "from": item["from"],
                    "date": item["date"],
                    "snippet": item["snippet"],
                    "unread": item["unread"],
                    "resource": resource_handle("gmail_message", str(item["id"])),
                }
            )
        return hits

    searchers = {
        "drive": search_drive,
        "people": search_people,
        "gmail": search_gmail,
    }

    async def run(service_name: str) -> None:
        try:
            found[service_name] = await searchers[service_name]()
        except Exception as exc:
            errors[service_name] = str(exc)

    async with anyio.create_task_group() as task_group:
        for service_name in selected:
            task_group.start_soon(run, service_name)
    if errors and len(errors) == len(set(selected)):
        # Partial-success policy: some services answered -> a successful
        # result listing per-service errors; none answered -> a tool
        # execution error, never an empty success.
        raise RecoverableToolError(
            "provider_unavailable",
            "No Workspace service could be searched.",
            required_action={"action": "retry", "after_seconds": 5},
            retryable=True,
            retry_after=5,
            details={"errors": dict(sorted(errors.items()))},
        )
    # Services in the order the caller listed them, hits in each service's order.
    matches = [hit for service_name in dict.fromkeys(selected) for hit in found.get(service_name, [])]
    return {
        "query": query,
        "services": selected,
        "matches": matches,
        "count": len(matches),
        "account_timezone": resolved_timezone[0] if resolved_timezone else None,
        "errors": errors,
    }


@workspace_mcp.tool(name="resolve_workspace_resource")
async def resolve_workspace_resource(
    uri: Annotated[
        str,
        (
            "The uri of a resource handle from an earlier result, e.g. 'gdrive:///<file_id>' or "
            "'gmail-message:///<message_id>'; other schemes are rejected."
        ),
    ],
) -> dict[str, object]:
    """Turn a resource URI from an earlier result's resource handle back into a handle with current metadata. Drive
    files are re-read for a fresh name, mime_type, modified_at (account time zone), web_url and etag, and Gmail messages
    are checked to still exist; threads, events, contacts and tasks are only parsed, without a Google call. Returns
    resource and verified (true when Google confirmed the resource exists); pass resource.id to the matching service
    tool for content."""
    kind, resource_id = parse_resource_uri(uri)
    if kind == "drive_file":
        item = await execute_google_request(
            build_drive_service().files().get(
                fileId=resource_id,
                fields="id,name,mimeType,modifiedTime,webViewLink,etag",
            )
        )
        return {
            "resource": resource_handle(
                kind,
                resource_id,
                name=item.get("name"),
                mime_type=item.get("mimeType"),
                modified_at=in_account_timezone(item.get("modifiedTime"), await resolve_user_timezone()),
                web_url=item.get("webViewLink"),
                etag=item.get("etag"),
            ),
            "verified": True,
        }
    if kind == "gmail_message":
        await execute_google_request(
            build_gmail_service().users().messages().get(
                userId="me", id=resource_id, format="minimal", fields="id"
            )
        )
        return {"resource": resource_handle(kind, resource_id), "verified": True}
    return {"resource": resource_handle(kind, resource_id), "verified": False}


#: Tools that use prepare/commit (common/approvals.py CONSEQUENTIAL_TOOLS).
ConsequentialToolName = Literal[
    "calendar_update_event",
    "drive_create_permission",
    "gmail_batch_modify",
    "gmail_send_email",
    "sheets_batch_update_spreadsheet",
]
if set(get_args(ConsequentialToolName)) != CONSEQUENTIAL_TOOLS:  # pragma: no cover - import-time guard
    raise RuntimeError("ConsequentialToolName is out of sync with CONSEQUENTIAL_TOOLS.")


@workspace_mcp.tool(name="prepare_workspace_action")
async def prepare_workspace_action(
    tool_name: Annotated[
        ConsequentialToolName,
        (
            "Published name of the tool to prepare: "
            f"{', '.join(sorted(CONSEQUENTIAL_TOOLS))}. No other tool uses prepare/commit."
        ),
    ],
    arguments: Annotated[
        dict[str, object],
        (
            "The keyword arguments for tool_name exactly as in a direct call. They are stored unchanged, "
            "not validated until commit, and cannot be edited afterwards."
        ),
    ],
) -> dict[str, object]:
    """Preview a high-impact action and bind its exact arguments to a one-time commit token, then run it with
    commit_workspace_action. A direct call fails with prepare_required for gmail_send_email with 10 or more
    recipients by default (to, cc and bcc together), drive_create_permission with type 'anyone', gmail_batch_modify
    on 10 or more messages, calendar_update_event that sets recurrence, and sheets_batch_update_spreadsheet with 10
    or more requests. Returns commit_token, expires_at (epoch seconds, 10 minutes ahead by default), impact (counts and
    warnings) and next_action; nothing runs until commit."""
    return await prepare_action(tool_name, arguments)


@workspace_mcp.tool(name="commit_workspace_action")
async def commit_workspace_action(
    commit_token: Annotated[
        str,
        (
            "One-time, principal-bound token from prepare_workspace_action's response; "
            "expires 10 minutes after issuance by default (MCP_CONFIRMATION_TTL_SECONDS). "
            "The bound action runs at most once: repeating a commit that already ran returns "
            "its saved result, and a confirmation question keeps the token valid for the "
            "answering call."
        ),
    ],
) -> dict[str, object] | ToolResult | mcp_types.InputRequiredResult:
    """Run an action prepared with prepare_workspace_action, with exactly the bound arguments, at most once; the bound
    tool's own checks and confirmation questions still apply. A token that is invalid, expired or another user's is
    rejected. Returns status 'committed' with tool and the bound tool's result, that tool's error result if it
    failed, or its confirmation question if it asks one."""
    store = get_operation_store()
    ref = operation_ref(commit_token)
    outcome = await store.claim(
        operation_key(current_principal().storage_key, commit_token), kind="commit", ref=ref
    )
    if outcome.status in ("missing", "answer_changed", "payload_changed"):
        raise ValueError(INVALID_COMMIT)
    if outcome.status != "claimed" or outcome.handle is None or outcome.record is None:
        # Already ran (saved result), running elsewhere, failed, or unknown:
        # never execute the bound call a second time.
        return await resolve_claim(outcome, tool=str((outcome.record or {}).get("tool", "")), ref=ref)
    tracker = current_tracker()
    if tracker is not None:
        # The confirmation guard around this tool settles the claim.
        tracker.operation = outcome.handle
    tool = str(outcome.record["tool"])
    arguments = outcome.record.get("arguments")
    if not isinstance(arguments, dict):
        raise ValueError(INVALID_COMMIT)
    active_token = COMMIT_ACTIVE.set(True)
    try:
        # Re-enter the complete middleware chain so revocation, admission,
        # deadlines, input limits, handle resolution, telemetry, and structured
        # errors still apply at commit time. COMMIT_ACTIVE bypasses only the
        # consequential-action gate for this exact bound invocation.
        result = await workspace_mcp.call_tool(tool, arguments)
    finally:
        COMMIT_ACTIVE.reset(active_token)
    if isinstance(result, InputRequiredToolResult):
        # The bound tool asked a confirmation question: nothing ran yet. The
        # guard parks the operation (awaiting_input) for the answering call.
        return result.input_required
    if result.is_error:
        return result
    return {
        "status": "committed",
        "tool": tool,
        "result": result.structured_content or {
            "content": [item.model_dump() for item in result.content]
        },
    }
apply_default_tool_annotations(workspace_mcp)
