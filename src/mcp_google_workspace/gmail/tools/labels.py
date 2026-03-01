"""Gmail label management tools."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from ..client import gmail_service
from ..schemas import LabelCreateRequest, LabelDeleteRequest, LabelUpdateRequest, ModifyMessageRequest


def _label_payload(
    name: str | None,
    message_list_visibility: str | None,
    label_list_visibility: str | None,
    background_color: str | None,
    text_color: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if message_list_visibility is not None:
        payload["messageListVisibility"] = message_list_visibility
    if label_list_visibility is not None:
        payload["labelListVisibility"] = label_list_visibility
    if background_color or text_color:
        payload["color"] = {}
        if background_color:
            payload["color"]["backgroundColor"] = background_color
        if text_color:
            payload["color"]["textColor"] = text_color
    return payload


def register(server: FastMCP) -> None:
    @server.tool(name="list_labels")
    async def list_labels(ctx: Context) -> dict[str, Any]:
        """List all Gmail labels available in the mailbox."""
        service = gmail_service()
        await ctx.info("Listing Gmail labels.")
        labels = service.users().labels().list(userId="me").execute().get("labels", [])
        return {"labels": labels, "count": len(labels)}

    @server.tool(name="create_label")
    async def create_label(
        name: str,
        message_list_visibility: str | None = None,
        label_list_visibility: str | None = None,
        background_color: str | None = None,
        text_color: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create a user label with optional visibility/color settings."""
        request = LabelCreateRequest(
            name=name,
            message_list_visibility=message_list_visibility,
            label_list_visibility=label_list_visibility,
            background_color=background_color,
            text_color=text_color,
        )
        service = gmail_service()
        payload = _label_payload(
            request.name,
            request.message_list_visibility,
            request.label_list_visibility,
            request.background_color,
            request.text_color,
        )
        if ctx is not None:
            await ctx.info(f"Creating label {request.name}.")
        created = service.users().labels().create(userId="me", body=payload).execute()
        return {"label": created}

    @server.tool(name="update_label")
    async def update_label(
        label_id: str,
        name: str | None = None,
        message_list_visibility: str | None = None,
        label_list_visibility: str | None = None,
        background_color: str | None = None,
        text_color: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Patch an existing Gmail label's properties."""
        request = LabelUpdateRequest(
            label_id=label_id,
            name=name,
            message_list_visibility=message_list_visibility,
            label_list_visibility=label_list_visibility,
            background_color=background_color,
            text_color=text_color,
        )
        service = gmail_service()
        payload = _label_payload(
            request.name,
            request.message_list_visibility,
            request.label_list_visibility,
            request.background_color,
            request.text_color,
        )
        if ctx is not None:
            await ctx.info(f"Updating label {request.label_id}.")
        updated = (
            service.users().labels().patch(userId="me", id=request.label_id, body=payload).execute()
        )
        return {"label": updated}

    @server.tool(name="delete_label")
    async def delete_label(
        label_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Delete a user-created Gmail label."""
        request = LabelDeleteRequest(label_id=label_id)
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Deleting label {request.label_id}.")
        service.users().labels().delete(userId="me", id=request.label_id).execute()
        return {"status": "ok", "label_id": request.label_id}

    @server.tool(name="apply_labels")
    async def apply_labels(
        message_id: str,
        add_label_ids: list[str] = [],
        remove_label_ids: list[str] = [],
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Apply/remove labels on a single message."""
        request = ModifyMessageRequest(
            message_id=message_id,
            add_label_ids=add_label_ids,
            remove_label_ids=remove_label_ids,
        )
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Applying labels on message {request.message_id}.")
        result = (
            service.users()
            .messages()
            .modify(
                userId="me",
                id=request.message_id,
                body={
                    "addLabelIds": request.add_label_ids,
                    "removeLabelIds": request.remove_label_ids,
                },
            )
            .execute()
        )
        return {"message": result}
