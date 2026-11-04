"""Google Keep tools for note operations and collaboration."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import execute_google_request
from ..common.confirmation import confirm_destructive_action
from ..common.errors import provider_tool_error
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

LOGGER = logging.getLogger(__name__)

CreateNoteArg = Annotated[CreateNoteRequest, "Title, a plain-text or checklist body, optional collaborators and a confirmation flag for a new note."]
GetNoteArg = Annotated[GetNoteRequest, "Identifies the Keep note to fetch."]
ListNotesArg = Annotated[ListNotesRequest, "Filter and paging options for listing Keep notes."]
DeleteNoteArg = Annotated[DeleteNoteRequest, "Identifies the Keep note to delete and whether to ask the user to confirm first."]
UpdateNoteArg = Annotated[UpdateNoteRequest, "Replacement note content; accepted for compatibility, but Keep cannot edit notes so nothing is changed."]
AppendNoteArg = Annotated[AppendNoteRequest, "The note to extend, the text or checklist items to add, and whether to create the replacement note."]
PatchChecklistArg = Annotated[PatchChecklistItemRequest, "The note, the checklist edit to make and its arguments, and whether to create the replacement note."]
ShareNoteArg = Annotated[ShareNoteRequest, "The note and the email addresses to grant writer access."]
UnshareNoteArg = Annotated[UnshareNoteRequest, "The note and the permission names to remove from it."]

NoteNameArg = Annotated[
    str,
    (
        "Keep note resource name 'notes/ABC123' or the bare note ID. Accepted for compatibility; "
        "the tool does nothing with it and always reports that the operation is unsupported."
    ),
]


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
    async def create_note(request: CreateNoteArg, ctx: Context) -> dict[str, Any]:
        """Create a Keep note with a plain-text or checklist body, optionally shared with collaborators.

        Keep cannot edit notes afterwards, so to change content use keep_append_note_content or
        keep_patch_note_checklist. Returns status 'ok' with the created note, whose name field
        ('notes/...') identifies it for later calls. With confirm_create true the user is asked first and the call returns
        status 'cancelled' if declined. Collaborators are given writer access.
        """
        service = keep_service()
        if request.confirm_create:
            if not await confirm_destructive_action(
                ctx,
                "create_note",
                f"Create Keep note titled '{request.title or '(untitled)'}'?",
            ):
                return {"status": "cancelled"}

        note = _build_note_body(request)
        LOGGER.debug("Creating Google Keep note.")
        try:
            created = await execute_google_request(service.notes().create(body=note))

            if request.collaborator_emails:
                LOGGER.debug("Applying collaborator permissions.")
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
            raise provider_tool_error(exc, title=request.title) from exc

        return {"status": "ok", "note": created}

    @server.tool(name="get_note")
    async def get_note(request: GetNoteArg, ctx: Context) -> dict[str, Any]:
        """Get one Keep note with its full text, by bare ID or canonical 'notes/...' name.

        Find the name with keep_list_notes or keep_create_note first. Returns id, title, text
        (full, not truncated), checklist counts (total and completed), attachment_count,
        created_at, updated_at and trashed_at in the account time zone.
        """
        service = keep_service()
        name = normalize_note_name(request.note_name)
        LOGGER.debug(f"Fetching Keep note {name}.")
        try:
            account_timezone = await resolve_user_timezone()
            return note_envelope(
                await execute_google_request(service.notes().get(name=name)),
                account_timezone=account_timezone,
                max_text=None,
            )
        except HttpError as exc:
            raise provider_tool_error(exc, note_name=name) from exc

    @server.tool(name="list_notes")
    async def list_notes(request: ListNotesArg, ctx: Context) -> dict[str, Any]:
        """List a page of Keep notes, optionally filtered by creation, update or trash time.

        To read one note in full use keep_get_note. Returns notes (id, title, text cut to 500
        characters with text_truncated, checklist counts, attachment_count and timestamps),
        count, next_page_token when more notes exist, and account_timezone.
        """
        service = keep_service()
        LOGGER.debug("Listing Keep notes.")
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
            raise provider_tool_error(exc, filter=request.filter, page_token=request.page_token) from exc
        notes = result.get("notes", [])
        await ctx.report_progress(len(notes), request.page_size, "Keep notes page loaded")
        return {
            "notes": [note_envelope(note, account_timezone=account_timezone) for note in notes],
            "next_page_token": result.get("nextPageToken"),
            "count": len(notes),
            "account_timezone": account_timezone,
        }

    @server.tool(name="delete_note")
    async def delete_note(request: DeleteNoteArg, ctx: Context) -> dict[str, Any]:
        """Permanently delete a Keep note; this cannot be undone.

        Find the note name with keep_list_notes. Returns status 'ok' with the note_name. With
        confirm_delete true the user is asked first and the call returns status 'cancelled'
        if declined.
        """
        service = keep_service()
        name = normalize_note_name(request.note_name)
        if request.confirm_delete:
            if not await confirm_destructive_action(
                ctx,
                "delete_note",
                f"Permanently delete Keep note {name}? This cannot be undone.",
            ):
                return {"status": "cancelled"}
        try:
            await execute_google_request(service.notes().delete(name=name))
        except HttpError as exc:
            raise provider_tool_error(exc, note_name=name) from exc
        return {"status": "ok", "note_name": name}

    @server.tool(name="update_note")
    async def update_note(request: UpdateNoteArg, ctx: Context) -> dict[str, Any]:
        """Report that editing a note in place is not possible, because Google Keep API v1 has no update call.

        Instead create a replacement with keep_create_note and delete the original with
        keep_delete_note, or use keep_append_note_content or keep_patch_note_checklist. Returns
        status 'unsupported' with a reason and suggested_workflow; nothing is changed.
        """
        _ = request
        LOGGER.debug(
            "Google Keep API v1 currently exposes create/get/list/delete; update/patch is not available."
        )
        return {
            "status": "unsupported",
            "reason": "Google Keep API v1 does not provide notes.patch/notes.update.",
            "suggested_workflow": "Create a replacement note with create_note, then delete the original.",
        }

    @server.tool(name="archive_note")
    async def archive_note(note_name: NoteNameArg, ctx: Context) -> dict[str, Any]:
        """Report that archiving a note is not possible, because Google Keep API v1 has no archive call.

        Returns status 'unsupported' with a reason; nothing is changed.
        """
        _ = note_name
        LOGGER.debug("Archive action is not exposed by Google Keep API v1.")
        return {
            "status": "unsupported",
            "reason": "Google Keep API v1 does not provide archive/unarchive endpoints.",
        }

    @server.tool(name="unarchive_note")
    async def unarchive_note(note_name: NoteNameArg, ctx: Context) -> dict[str, Any]:
        """Report that unarchiving a note is not possible, because Google Keep API v1 has no archive call.

        Returns status 'unsupported' with a reason; nothing is changed.
        """
        _ = note_name
        LOGGER.debug("Unarchive action is not exposed by Google Keep API v1.")
        return {
            "status": "unsupported",
            "reason": "Google Keep API v1 does not provide archive/unarchive endpoints.",
        }

    @server.tool(name="list_keep_labels")
    async def list_keep_labels(ctx: Context) -> dict[str, Any]:
        """Report that listing labels is not possible, because Google Keep API v1 has no labels resource.

        Returns status 'unsupported' with a reason; nothing is changed.
        """
        LOGGER.debug("Google Keep API v1 does not provide a labels resource.")
        return {
            "status": "unsupported",
            "reason": "Keep API v1 has no dedicated labels endpoints.",
        }

    @server.tool(name="create_keep_label")
    async def create_keep_label(
        label_name: Annotated[
            str,
            "Name of the label to create. Accepted for compatibility; no label is created.",
        ],
        ctx: Context,
    ) -> dict[str, Any]:
        """Report that creating a label is not possible, because Google Keep API v1 has no labels resource.

        Returns status 'unsupported' with a reason; no label is created.
        """
        _ = label_name
        LOGGER.debug("Google Keep API v1 does not provide label creation endpoints.")
        return {
            "status": "unsupported",
            "reason": "Keep API v1 has no dedicated labels endpoints.",
        }

    @server.tool(name="delete_keep_label")
    async def delete_keep_label(
        label_name: Annotated[
            str,
            "Name of the label to delete. Accepted for compatibility; no label is deleted.",
        ],
        ctx: Context,
    ) -> dict[str, Any]:
        """Report that deleting a label is not possible, because Google Keep API v1 has no labels resource.

        Returns status 'unsupported' with a reason; no label is deleted.
        """
        _ = label_name
        LOGGER.debug("Google Keep API v1 does not provide label deletion endpoints.")
        return {
            "status": "unsupported",
            "reason": "Keep API v1 has no dedicated labels endpoints.",
        }

    @server.tool(name="add_checklist_item")
    async def add_checklist_item(
        note_name: NoteNameArg,
        text: Annotated[str, "Text of the checklist item to add. Accepted for compatibility and not used."],
        ctx: Context,
    ) -> dict[str, Any]:
        """Report that adding a checklist item in place is not possible, because Google Keep API v1 has no update call.

        Use keep_patch_note_checklist with operation 'add' instead, which previews or creates a
        replacement note. Returns status 'unsupported' with a reason and suggested_tool; nothing
        is changed.
        """
        _ = (note_name, text)
        LOGGER.debug("Checklist mutation requires update API, which Keep API v1 does not expose.")
        return {
            "status": "unsupported",
            "reason": "Keep API v1 does not support note patch/update.",
            "suggested_tool": "patch_note_checklist",
        }

    @server.tool(name="toggle_checklist_item")
    async def toggle_checklist_item(
        note_name: NoteNameArg,
        index: Annotated[int, "Zero-based position of the checklist item. Accepted for compatibility and not used."],
        checked: Annotated[bool, "Checked state to set on the item. Accepted for compatibility and not used."],
        ctx: Context,
    ) -> dict[str, Any]:
        """Report that ticking or unticking a checklist item in place is not possible, because Google Keep API v1 has no update call.

        Use keep_patch_note_checklist with operation 'set_checked' instead, which previews or
        creates a replacement note. Returns status 'unsupported' with a reason and suggested_tool;
        nothing is changed.
        """
        _ = (note_name, index, checked)
        LOGGER.debug("Checklist mutation requires update API, which Keep API v1 does not expose.")
        return {
            "status": "unsupported",
            "reason": "Keep API v1 does not support note patch/update.",
            "suggested_tool": "patch_note_checklist",
        }

    @server.tool(name="remove_checklist_item")
    async def remove_checklist_item(
        note_name: NoteNameArg,
        index: Annotated[int, "Zero-based position of the checklist item to remove. Accepted for compatibility and not used."],
        ctx: Context,
    ) -> dict[str, Any]:
        """Report that removing a checklist item in place is not possible, because Google Keep API v1 has no update call.

        Use keep_patch_note_checklist with operation 'remove' instead, which previews or creates a
        replacement note. Returns status 'unsupported' with a reason and suggested_tool; nothing
        is changed.
        """
        _ = (note_name, index)
        LOGGER.debug("Checklist mutation requires update API, which Keep API v1 does not expose.")
        return {
            "status": "unsupported",
            "reason": "Keep API v1 does not support note patch/update.",
            "suggested_tool": "patch_note_checklist",
        }

    @server.tool(name="append_note_content")
    async def append_note_content(request: AppendNoteArg, ctx: Context) -> dict[str, Any]:
        """Append text or checklist items to a Keep note by building a replacement note, since Keep cannot edit in place.

        By default only a preview is returned; set apply_via_replacement to create the new note.
        Returns status 'preview' with source_note and replacement_note_payload, or status
        'applied' with replacement_note (and original_deleted when the original was removed).
        Only the title and text or checklist are carried over.
        """
        service = keep_service()
        source_name = normalize_note_name(request.note_name)
        LOGGER.debug(f"Preparing append operation for Keep note {source_name}.")
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

            LOGGER.debug("Applying replacement-note workflow (create new note).")
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
            raise provider_tool_error(exc, note_name=source_name) from exc

    @server.tool(name="patch_note_checklist")
    async def patch_note_checklist(request: PatchChecklistArg, ctx: Context) -> dict[str, Any]:
        """Add, remove, tick or retitle one checklist item by building a replacement note, since Keep cannot edit in place.

        By default only a preview is returned; set apply_via_replacement to create the new note.
        Returns status 'preview' with source_note and replacement_note_payload, or status
        'applied' with replacement_note (and original_deleted when the original was removed).
        Only the title and checklist are carried over.
        """
        service = keep_service()
        source_name = normalize_note_name(request.note_name)
        LOGGER.debug(f"Preparing checklist patch for Keep note {source_name}.")
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

            LOGGER.debug("Applying replacement-note checklist patch (create new note).")
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
            raise provider_tool_error(exc, note_name=source_name, operation=request.operation) from exc

    @server.tool(name="share_note")
    async def share_note(request: ShareNoteArg, ctx: Context) -> dict[str, Any]:
        """Give people writer access to a Keep note.

        To revoke access use keep_unshare_note. Returns status 'ok' and result, the Keep batch
        response whose permissions list holds one entry per collaborator with the permission
        name needed for keep_unshare_note.
        """
        service = keep_service()
        note_name = normalize_note_name(request.note_name)
        LOGGER.debug(f"Adding collaborators to {note_name}.")
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
            raise provider_tool_error(exc, note_name=note_name) from exc
        return {"status": "ok", "result": result}

    @server.tool(name="unshare_note")
    async def unshare_note(request: UnshareNoteArg, ctx: Context) -> dict[str, Any]:
        """Remove collaborators' access to a Keep note by permission name.

        Permission names come from keep_share_note results. Returns status 'ok' and
        removed_permissions, the names that were removed. The owner's permission cannot be removed.
        """
        service = keep_service()
        note_name = normalize_note_name(request.note_name)
        LOGGER.debug(f"Removing collaborators from {note_name}.")
        try:
            await execute_google_request(
                service.notes().permissions().batchDelete(
                    parent=note_name,
                    body={"names": request.permission_names},
                )
            )
        except HttpError as exc:
            raise provider_tool_error(exc, note_name=note_name) from exc
        return {"status": "ok", "removed_permissions": request.permission_names}
