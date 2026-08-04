"""Google Keep tools for note operations and collaboration."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import execute_google_request
from ..common.errors import tool_error_payload
from ..common.timezone import resolve_user_timezone
from .client import keep_service, normalize_note_name
from .presentation import note_envelope
from .schemas import (
    AppendNoteRequest,
    CreateNoteRequest,
    DeleteNoteRequest,
    GetNoteRequest,
    ListNotesRequest,
    PatchChecklistItemRequest,
    ShareNoteRequest,
    UnshareNoteRequest,
    UpdateNoteRequest,
)


def _build_note_body(request: CreateNoteRequest | UpdateNoteRequest) -> dict[str, Any]:
    note: dict[str, Any] = {}
    if request.title:
        note["title"] = request.title

    if request.checklist_items:
        note["body"] = {
            "list": {
                "listItems": [
                    {"text": {"text": item.text}, "checked": item.checked}
                    for item in request.checklist_items
                ]
            }
        }
    elif request.text_body:
        note["body"] = {"text": {"text": request.text_body}}
    return note


def _extract_note_text(note: dict[str, Any]) -> str:
    return (
        note.get("body", {})
        .get("text", {})
        .get("text", "")
    )


def _extract_note_checklist(note: dict[str, Any]) -> list[dict[str, Any]]:
    list_items = note.get("body", {}).get("list", {}).get("listItems", [])
    extracted: list[dict[str, Any]] = []
    for item in list_items:
        if not isinstance(item, dict):
            continue
        text_value = item.get("text", {}).get("text", "")
        extracted.append({"text": text_value, "checked": bool(item.get("checked", False))})
    return extracted


def _build_replacement_note(title: str | None, text_body: str | None, checklist: list[dict[str, Any]]) -> dict[str, Any]:
    note: dict[str, Any] = {}
    if title:
        note["title"] = title
    if checklist:
        note["body"] = {
            "list": {
                "listItems": [
                    {"text": {"text": str(item.get("text", ""))}, "checked": bool(item.get("checked", False))}
                    for item in checklist
                ]
            }
        }
    elif text_body:
        note["body"] = {"text": {"text": text_body}}
    return note


def register_tools(server: FastMCP) -> None:
    @server.tool(name="create_note")
    async def create_note(request: CreateNoteRequest, ctx: Context) -> dict[str, Any]:
        """Create a text or checklist note, optionally adding writer collaborators.

        The request supplies the title, one body format, optional collaborator emails,
        and confirmation behavior. Returns the created note with ``status=ok``, a
        cancellation status, or a structured provider error.
        """
        service = keep_service()
        if request.confirm_create:
            response = await ctx.elicit(
                f"Create Keep note titled '{request.title or '(untitled)'}'?",
                response_type=bool,  # type: ignore[arg-type]
            )
            if response.action != "accept" or not bool(response.data):
                return {"status": "cancelled"}

        note = _build_note_body(request)
        await ctx.info("Creating Google Keep note.")
        try:
            created = await execute_google_request(service.notes().create(body=note))

            if request.collaborator_emails:
                await ctx.info("Applying collaborator permissions.")
                parent = created.get("name")
                create_requests = [
                    {
                        "parent": parent,
                        "permission": {"email": str(email), "role": "WRITER"},
                    }
                    for email in request.collaborator_emails
                ]
                await execute_google_request(
                    service.notes().permissions().batchCreate(
                        parent=parent, body={"requests": create_requests}
                    )
                )
        except HttpError as exc:
            return tool_error_payload(exc, title=request.title)

        return {"status": "ok", "note": created}

    @server.tool(name="get_note")
    async def get_note(request: GetNoteRequest, ctx: Context) -> dict[str, Any]:
        """Get one Keep note by bare ID or canonical ``notes/...`` name.

        Note names come from ``create_note`` or ``list_notes``. Returns a normalized
        note envelope with full text/checklist content, or a structured provider error.
        """
        service = keep_service()
        name = normalize_note_name(request.note_name)
        await ctx.info(f"Fetching Keep note {name}.")
        try:
            account_timezone = await resolve_user_timezone()
            return note_envelope(
                await execute_google_request(service.notes().get(name=name)),
                account_timezone=account_timezone,
                max_text=None,
            )
        except HttpError as exc:
            return tool_error_payload(exc, note_name=name)

    @server.tool(name="list_notes")
    async def list_notes(request: ListNotesRequest, ctx: Context) -> dict[str, Any]:
        """List a page of Keep notes with optional API filter and pagination token.

        Returns normalized note envelopes, count, account timezone, and the next page
        token when more notes exist, or a structured provider error.
        """
        service = keep_service()
        await ctx.info("Listing Keep notes.")
        try:
            account_timezone = await resolve_user_timezone()
            result = await execute_google_request(
                service.notes()
                .list(
                    filter=request.filter,
                    pageSize=request.page_size,
                    pageToken=request.page_token,
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, filter=request.filter, page_token=request.page_token)
        notes = result.get("notes", [])
        await ctx.report_progress(len(notes), request.page_size, "Keep notes page loaded")
        return {
            "notes": [note_envelope(note, account_timezone=account_timezone) for note in notes],
            "next_page_token": result.get("nextPageToken"),
            "count": len(notes),
            "account_timezone": account_timezone,
        }

    @server.tool(name="delete_note")
    async def delete_note(request: DeleteNoteRequest, ctx: Context) -> dict[str, Any]:
        """Permanently delete a Keep note after optional interactive confirmation.

        The note name comes from ``create_note`` or ``list_notes``. Returns ``status=ok``
        with the canonical note name, a cancellation status, or a provider error.
        """
        service = keep_service()
        name = normalize_note_name(request.note_name)
        if request.confirm_delete:
            response = await ctx.elicit(
                f"Permanently delete Keep note {name}? This cannot be undone.",
                response_type=bool,  # type: ignore[arg-type]
            )
            if response.action != "accept" or not bool(response.data):
                return {"status": "cancelled"}
        try:
            await execute_google_request(service.notes().delete(name=name))
        except HttpError as exc:
            return tool_error_payload(exc, note_name=name)
        return {"status": "ok", "note_name": name}

    @server.tool(name="update_note")
    async def update_note(request: UpdateNoteRequest, ctx: Context) -> dict[str, Any]:
        """Explain that direct note updates are unavailable in Google Keep API v1.

        Accepts the desired replacement content for compatibility and returns an
        ``unsupported`` response pointing callers to the create/delete workflow.
        """
        _ = request
        await ctx.warning(
            "Google Keep API v1 currently exposes create/get/list/delete; update/patch is not available."
        )
        return {
            "status": "unsupported",
            "reason": "Google Keep API v1 does not provide notes.patch/notes.update.",
            "suggested_workflow": "Create a replacement note with create_note, then delete the original.",
        }

    @server.tool(name="archive_note")
    async def archive_note(note_name: str, ctx: Context) -> dict[str, Any]:
        """Report that archiving ``note_name`` is unavailable in Google Keep API v1.

        Returns a stable ``unsupported`` response with the API limitation.
        """
        _ = note_name
        await ctx.warning("Archive action is not exposed by Google Keep API v1.")
        return {
            "status": "unsupported",
            "reason": "Google Keep API v1 does not provide archive/unarchive endpoints.",
        }

    @server.tool(name="unarchive_note")
    async def unarchive_note(note_name: str, ctx: Context) -> dict[str, Any]:
        """Report that unarchiving ``note_name`` is unavailable in Google Keep API v1.

        Returns a stable ``unsupported`` response with the API limitation.
        """
        _ = note_name
        await ctx.warning("Unarchive action is not exposed by Google Keep API v1.")
        return {
            "status": "unsupported",
            "reason": "Google Keep API v1 does not provide archive/unarchive endpoints.",
        }

    @server.tool(name="list_keep_labels")
    async def list_keep_labels(ctx: Context) -> dict[str, Any]:
        """Report that listing labels is unavailable in Google Keep API v1.

        Returns a stable ``unsupported`` response with the API limitation.
        """
        await ctx.warning("Google Keep API v1 does not provide a labels resource.")
        return {
            "status": "unsupported",
            "reason": "Keep API v1 has no dedicated labels endpoints.",
        }

    @server.tool(name="create_keep_label")
    async def create_keep_label(label_name: str, ctx: Context) -> dict[str, Any]:
        """Report that creating ``label_name`` is unavailable in Google Keep API v1.

        Returns a stable ``unsupported`` response with the API limitation.
        """
        _ = label_name
        await ctx.warning("Google Keep API v1 does not provide label creation endpoints.")
        return {
            "status": "unsupported",
            "reason": "Keep API v1 has no dedicated labels endpoints.",
        }

    @server.tool(name="delete_keep_label")
    async def delete_keep_label(label_name: str, ctx: Context) -> dict[str, Any]:
        """Report that deleting ``label_name`` is unavailable in Google Keep API v1.

        Returns a stable ``unsupported`` response with the API limitation.
        """
        _ = label_name
        await ctx.warning("Google Keep API v1 does not provide label deletion endpoints.")
        return {
            "status": "unsupported",
            "reason": "Keep API v1 has no dedicated labels endpoints.",
        }

    @server.tool(name="add_checklist_item")
    async def add_checklist_item(note_name: str, text: str, ctx: Context) -> dict[str, Any]:
        """Explain how to add ``text`` to ``note_name`` through a replacement note.

        Returns ``unsupported`` for direct mutation and points to
        ``patch_note_checklist`` as the supported preview/apply workflow.
        """
        _ = (note_name, text)
        await ctx.warning("Checklist mutation requires update API, which Keep API v1 does not expose.")
        return {
            "status": "unsupported",
            "reason": "Keep API v1 does not support note patch/update.",
            "suggested_tool": "patch_note_checklist",
        }

    @server.tool(name="toggle_checklist_item")
    async def toggle_checklist_item(note_name: str, index: int, checked: bool, ctx: Context) -> dict[str, Any]:
        """Explain how to set a checklist item's checked state via a replacement note.

        ``index`` is zero-based. Returns ``unsupported`` for direct mutation and points
        to ``patch_note_checklist`` as the supported preview/apply workflow.
        """
        _ = (note_name, index, checked)
        await ctx.warning("Checklist mutation requires update API, which Keep API v1 does not expose.")
        return {
            "status": "unsupported",
            "reason": "Keep API v1 does not support note patch/update.",
            "suggested_tool": "patch_note_checklist",
        }

    @server.tool(name="remove_checklist_item")
    async def remove_checklist_item(note_name: str, index: int, ctx: Context) -> dict[str, Any]:
        """Explain how to remove a zero-based checklist item via a replacement note.

        Returns ``unsupported`` for direct mutation and points to
        ``patch_note_checklist`` as the supported preview/apply workflow.
        """
        _ = (note_name, index)
        await ctx.warning("Checklist mutation requires update API, which Keep API v1 does not expose.")
        return {
            "status": "unsupported",
            "reason": "Keep API v1 does not support note patch/update.",
            "suggested_tool": "patch_note_checklist",
        }

    @server.tool(name="append_note_content")
    async def append_note_content(request: AppendNoteRequest, ctx: Context) -> dict[str, Any]:
        """Preview or apply appending content through a replacement-note workflow.

        The request identifies the note and text/checklist additions, whether to apply,
        and whether to delete the original. Returns a preview, applied replacement, or
        structured provider error.
        """
        service = keep_service()
        source_name = normalize_note_name(request.note_name)
        await ctx.info(f"Preparing append operation for Keep note {source_name}.")
        try:
            original = await execute_google_request(service.notes().get(name=source_name))
            existing_text = _extract_note_text(original)
            existing_checklist = _extract_note_checklist(original)
            title = original.get("title")

            checklist_mode = bool(existing_checklist or request.checklist_append)
            replacement_text = existing_text
            replacement_checklist = list(existing_checklist)
            if checklist_mode:
                if request.text_append:
                    replacement_checklist.append({"text": request.text_append, "checked": False})
                for item in request.checklist_append:
                    replacement_checklist.append({"text": item.text, "checked": item.checked})
                replacement_text = ""
            elif request.text_append:
                replacement_text = (
                    f"{existing_text}\n{request.text_append}".strip()
                    if existing_text
                    else request.text_append
                )

            replacement_note = _build_replacement_note(title, replacement_text, replacement_checklist)
            response: dict[str, Any] = {
                "status": "preview",
                "mode": "replacement",
                "source_note": source_name,
                "replacement_note_payload": replacement_note,
                "note": "Keep API has no patch endpoint; this workflow creates a replacement note.",
            }
            if not request.apply_via_replacement:
                return response

            await ctx.warning("Applying replacement-note workflow (create new note).")
            created = await execute_google_request(service.notes().create(body=replacement_note))
            response.update(
                {
                    "status": "applied",
                    "replacement_note": created,
                }
            )
            if request.delete_original_on_apply:
                await execute_google_request(service.notes().delete(name=source_name))
                response["original_deleted"] = True
            return response
        except HttpError as exc:
            return tool_error_payload(exc, note_name=source_name)

    @server.tool(name="patch_note_checklist")
    async def patch_note_checklist(request: PatchChecklistItemRequest, ctx: Context) -> dict[str, Any]:
        """Preview or apply a checklist edit through a replacement-note workflow.

        Supports add, remove, set_checked, and set_text operations; indexes are
        zero-based. Returns a preview, applied replacement, or structured provider error.
        """
        service = keep_service()
        source_name = normalize_note_name(request.note_name)
        await ctx.info(f"Preparing checklist patch for Keep note {source_name}.")
        try:
            original = await execute_google_request(service.notes().get(name=source_name))
            title = original.get("title")
            checklist = _extract_note_checklist(original)

            if request.operation == "add":
                if not request.text:
                    raise ValueError("text is required when operation='add'.")
                checklist.append({"text": request.text, "checked": bool(request.checked)})
            elif request.operation == "remove":
                if request.index is None:
                    raise ValueError("index is required when operation='remove'.")
                if request.index < 0 or request.index >= len(checklist):
                    raise IndexError("index out of range for checklist.")
                checklist.pop(request.index)
            elif request.operation == "set_checked":
                if request.index is None or request.checked is None:
                    raise ValueError("index and checked are required when operation='set_checked'.")
                if request.index < 0 or request.index >= len(checklist):
                    raise IndexError("index out of range for checklist.")
                checklist[request.index]["checked"] = request.checked
            elif request.operation == "set_text":
                if request.index is None or not request.text:
                    raise ValueError("index and text are required when operation='set_text'.")
                if request.index < 0 or request.index >= len(checklist):
                    raise IndexError("index out of range for checklist.")
                checklist[request.index]["text"] = request.text

            replacement_note = _build_replacement_note(title, None, checklist)
            response: dict[str, Any] = {
                "status": "preview",
                "mode": "replacement",
                "source_note": source_name,
                "replacement_note_payload": replacement_note,
                "note": "Keep API has no patch endpoint; this workflow creates a replacement note.",
            }
            if not request.apply_via_replacement:
                return response

            await ctx.warning("Applying replacement-note checklist patch (create new note).")
            created = await execute_google_request(service.notes().create(body=replacement_note))
            response.update(
                {
                    "status": "applied",
                    "replacement_note": created,
                }
            )
            if request.delete_original_on_apply:
                await execute_google_request(service.notes().delete(name=source_name))
                response["original_deleted"] = True
            return response
        except HttpError as exc:
            return tool_error_payload(exc, note_name=source_name, operation=request.operation)

    @server.tool(name="share_note")
    async def share_note(request: ShareNoteRequest, ctx: Context) -> dict[str, Any]:
        """Grant writer access to one or more collaborators on a Keep note.

        The note name comes from ``create_note`` or ``list_notes`` and the request lists
        collaborator emails. Returns the batch permission result or a provider error.
        """
        service = keep_service()
        note_name = normalize_note_name(request.note_name)
        await ctx.info(f"Adding collaborators to {note_name}.")
        create_requests = [
            {
                "parent": note_name,
                "permission": {"email": str(email), "role": "WRITER"},
            }
            for email in request.collaborator_emails
        ]
        try:
            result = await execute_google_request(
                service.notes().permissions().batchCreate(
                    parent=note_name, body={"requests": create_requests}
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, note_name=note_name)
        return {"status": "ok", "result": result}

    @server.tool(name="unshare_note")
    async def unshare_note(request: UnshareNoteRequest, ctx: Context) -> dict[str, Any]:
        """Remove named collaborator permissions from a Keep note.

        Permission names come from the Keep API permission resources. Returns the names
        removed with ``status=ok``, or a structured provider error.
        """
        service = keep_service()
        note_name = normalize_note_name(request.note_name)
        await ctx.info(f"Removing collaborators from {note_name}.")
        try:
            await execute_google_request(
                service.notes().permissions().batchDelete(
                    parent=note_name,
                    body={"names": request.permission_names},
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, note_name=note_name)
        return {"status": "ok", "removed_permissions": request.permission_names}
