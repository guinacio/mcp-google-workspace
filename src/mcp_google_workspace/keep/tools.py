"""Google Keep tools for note operations and collaboration."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from .client import keep_service, normalize_note_name
from .schemas import (
    CreateNoteRequest,
    DeleteNoteRequest,
    GetNoteRequest,
    ListNotesRequest,
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


def register_tools(server: FastMCP) -> None:
    @server.tool(name="create_note")
    async def create_note(request: CreateNoteRequest, ctx: Context) -> dict[str, Any]:
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
        created = service.notes().create(body=note).execute()

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
            service.notes().permissions().batchCreate(
                parent=parent, body={"requests": create_requests}
            ).execute()

        return {"status": "ok", "note": created}

    @server.tool(name="get_note")
    async def get_note(request: GetNoteRequest, ctx: Context) -> dict[str, Any]:
        service = keep_service()
        name = normalize_note_name(request.note_name)
        await ctx.info(f"Fetching Keep note {name}.")
        return service.notes().get(name=name).execute()

    @server.tool(name="list_notes")
    async def list_notes(request: ListNotesRequest, ctx: Context) -> dict[str, Any]:
        service = keep_service()
        await ctx.info("Listing Keep notes.")
        result = (
            service.notes()
            .list(
                filter=request.filter,
                pageSize=request.page_size,
                pageToken=request.page_token,
            )
            .execute()
        )
        notes = result.get("notes", [])
        await ctx.report_progress(len(notes), request.page_size, "Keep notes page loaded")
        return {
            "notes": notes,
            "next_page_token": result.get("nextPageToken"),
            "count": len(notes),
        }

    @server.tool(name="delete_note")
    async def delete_note(request: DeleteNoteRequest, ctx: Context) -> dict[str, Any]:
        service = keep_service()
        name = normalize_note_name(request.note_name)
        if request.confirm_delete:
            response = await ctx.elicit(
                f"Permanently delete Keep note {name}? This cannot be undone.",
                response_type=bool,  # type: ignore[arg-type]
            )
            if response.action != "accept" or not bool(response.data):
                return {"status": "cancelled"}
        service.notes().delete(name=name).execute()
        return {"status": "ok", "note_name": name}

    @server.tool(name="update_note")
    async def update_note(request: UpdateNoteRequest, ctx: Context) -> dict[str, Any]:
        """Expose explicit limitation: Keep API has no patch/update endpoint."""
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
        _ = note_name
        await ctx.warning("Archive action is not exposed by Google Keep API v1.")
        return {
            "status": "unsupported",
            "reason": "Google Keep API v1 does not provide archive/unarchive endpoints.",
        }

    @server.tool(name="unarchive_note")
    async def unarchive_note(note_name: str, ctx: Context) -> dict[str, Any]:
        _ = note_name
        await ctx.warning("Unarchive action is not exposed by Google Keep API v1.")
        return {
            "status": "unsupported",
            "reason": "Google Keep API v1 does not provide archive/unarchive endpoints.",
        }

    @server.tool(name="list_keep_labels")
    async def list_keep_labels(ctx: Context) -> dict[str, Any]:
        await ctx.warning("Google Keep API v1 does not provide a labels resource.")
        return {
            "status": "unsupported",
            "reason": "Keep API v1 has no dedicated labels endpoints.",
        }

    @server.tool(name="create_keep_label")
    async def create_keep_label(label_name: str, ctx: Context) -> dict[str, Any]:
        _ = label_name
        await ctx.warning("Google Keep API v1 does not provide label creation endpoints.")
        return {
            "status": "unsupported",
            "reason": "Keep API v1 has no dedicated labels endpoints.",
        }

    @server.tool(name="delete_keep_label")
    async def delete_keep_label(label_name: str, ctx: Context) -> dict[str, Any]:
        _ = label_name
        await ctx.warning("Google Keep API v1 does not provide label deletion endpoints.")
        return {
            "status": "unsupported",
            "reason": "Keep API v1 has no dedicated labels endpoints.",
        }

    @server.tool(name="add_checklist_item")
    async def add_checklist_item(note_name: str, text: str, ctx: Context) -> dict[str, Any]:
        _ = (note_name, text)
        await ctx.warning("Checklist mutation requires update API, which Keep API v1 does not expose.")
        return {
            "status": "unsupported",
            "reason": "Keep API v1 does not support note patch/update.",
        }

    @server.tool(name="toggle_checklist_item")
    async def toggle_checklist_item(note_name: str, index: int, checked: bool, ctx: Context) -> dict[str, Any]:
        _ = (note_name, index, checked)
        await ctx.warning("Checklist mutation requires update API, which Keep API v1 does not expose.")
        return {
            "status": "unsupported",
            "reason": "Keep API v1 does not support note patch/update.",
        }

    @server.tool(name="remove_checklist_item")
    async def remove_checklist_item(note_name: str, index: int, ctx: Context) -> dict[str, Any]:
        _ = (note_name, index)
        await ctx.warning("Checklist mutation requires update API, which Keep API v1 does not expose.")
        return {
            "status": "unsupported",
            "reason": "Keep API v1 does not support note patch/update.",
        }

    @server.tool(name="share_note")
    async def share_note(request: ShareNoteRequest, ctx: Context) -> dict[str, Any]:
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
        result = service.notes().permissions().batchCreate(
            parent=note_name, body={"requests": create_requests}
        ).execute()
        return {"status": "ok", "result": result}

    @server.tool(name="unshare_note")
    async def unshare_note(request: UnshareNoteRequest, ctx: Context) -> dict[str, Any]:
        service = keep_service()
        note_name = normalize_note_name(request.note_name)
        await ctx.info(f"Removing collaborators from {note_name}.")
        service.notes().permissions().batchDelete(
            parent=note_name,
            body={"names": request.permission_names},
        ).execute()
        return {"status": "ok", "removed_permissions": request.permission_names}
