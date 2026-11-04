"""Gmail label management tools."""

from __future__ import annotations

import logging

from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ...common.confirmation import confirm_destructive_action
from ..client import gmail_service
from ..schemas import LabelCreateRequest, LabelDeleteRequest, LabelUpdateRequest, ModifyMessageRequest

LOGGER = logging.getLogger(__name__)


def _label_payload(
    name: str | None,
    message_list_visibility: Literal["show", "hide"] | None,
    label_list_visibility: Literal["labelShow", "labelShowIfUnread", "labelHide"] | None,
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
        """List every label in the mailbox: system labels (INBOX, SENT, DRAFT, SPAM, TRASH, UNREAD, STARRED, IMPORTANT, CATEGORY_*) and the user's own. Use it to turn a label name into the ID that the label parameters of the other Gmail tools require. Returns labels (Gmail label resources with id, name and type 'system' or 'user') and count."""
        service = gmail_service()
        LOGGER.debug("Listing Gmail labels.")
        result = await execute_google_request(service.users().labels().list(userId="me"))
        labels = result.get("labels", [])
        return {"labels": labels, "count": len(labels)}

    @server.tool(name="create_label")
    async def create_label(
        name: Annotated[
            str,
            "Label name as shown in Gmail, different from every existing label; a '/' nests it under a parent, e.g. 'Clients/Acme'.",
        ],
        message_list_visibility: Literal["show", "hide"] | None = None,
        label_list_visibility: Literal["labelShow", "labelShowIfUnread", "labelHide"] | None = None,
        background_color: str | None = None,
        text_color: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create a user label in Gmail, optionally with its visibility and color. Apply it afterwards with gmail_apply_labels, gmail_batch_modify or gmail_modify_thread using the returned ID, and change it with gmail_update_label. Returns the created Gmail label resource under label, including its id (Label_...)."""
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
        LOGGER.debug(f"Creating label {request.name}.")
        created = await execute_google_request(
            service.users().labels().create(userId="me", body=payload)
        )
        return {"label": created}

    @server.tool(name="update_label")
    async def update_label(
        label_id: str,
        name: Annotated[str | None, "New label name; a '/' nests it under a parent label. Omit to keep the current name."] = None,
        message_list_visibility: Literal["show", "hide"] | None = None,
        label_list_visibility: Literal["labelShow", "labelShowIfUnread", "labelHide"] | None = None,
        background_color: str | None = None,
        text_color: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Rename a Gmail label or change its visibility or color; fields you omit keep their current values. Use gmail_create_label for a new label and gmail_delete_label to remove one. Returns the updated Gmail label resource under label."""
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
        LOGGER.debug(f"Updating label {request.label_id}.")
        updated = await execute_google_request(
            service.users().labels().patch(userId="me", id=request.label_id, body=payload)
        )
        return {"label": updated}

    @server.tool(name="delete_label")
    async def delete_label(
        label_id: Annotated[
            str, "User label ID (Label_...) from gmail_list_labels; system labels such as INBOX cannot be deleted."
        ],
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Delete a user label after asking the user to confirm; Gmail removes it from every message and thread, but the messages themselves are kept. To take a label off some messages only, use gmail_apply_labels or gmail_batch_modify. Returns status 'ok' with the label_id, or status 'cancelled' if the user declines. Cannot be undone."""
        request = LabelDeleteRequest(label_id=label_id)
        if not await confirm_destructive_action(
            ctx, "delete_label", f"Permanently delete Gmail label {request.label_id}?"
        ):
            return {"status": "cancelled", "label_id": request.label_id}
        service = gmail_service()
        LOGGER.debug(f"Deleting label {request.label_id}.")
        await execute_google_request(service.users().labels().delete(userId="me", id=request.label_id))
        return {"status": "ok", "label_id": request.label_id}

    @server.tool(name="apply_labels")
    async def apply_labels(
        message_id: str,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Add or remove labels on one Gmail message, e.g. star it (add STARRED), mark it important (add IMPORTANT) or tag it with a user label; at least one label list is required. It makes the same Gmail call as gmail_move_email, which reads better for archiving and filing; for many messages use gmail_batch_modify and for a whole conversation gmail_modify_thread. Returns the updated Gmail message (id, threadId, labelIds) under message, with no status field."""
        request = ModifyMessageRequest(
            message_id=message_id,
            add_label_ids=add_label_ids or [],
            remove_label_ids=remove_label_ids or [],
        )
        if not request.add_label_ids and not request.remove_label_ids:
            raise ValueError("At least one of add_label_ids/remove_label_ids must be provided.")
        service = gmail_service()
        LOGGER.debug(f"Applying labels on message {request.message_id}.")
        result = await execute_google_request(
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
        )
        return {"message": result}
