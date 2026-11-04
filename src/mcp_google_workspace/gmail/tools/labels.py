"""Gmail label management tools."""

from __future__ import annotations

import logging

from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ..client import gmail_service
from ..label_resolver import LabelResolver
from ..schemas import LabelCreateRequest, LabelDeleteRequest, LabelUpdateRequest

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
        """List every label in the mailbox: system labels (INBOX, SENT, DRAFT, SPAM, TRASH, UNREAD, STARRED, IMPORTANT, CATEGORY_*) and the user's own. The other Gmail tools accept label names directly; use this to see which labels exist, their IDs and types. Returns labels (Gmail label resources with id, name and type 'system' or 'user') and count."""
        service = gmail_service()
        LOGGER.debug("Listing Gmail labels.")
        result = await execute_google_request(service.users().labels().list(userId="me"))
        labels = result.get("labels", [])
        return {"labels": labels, "count": len(labels)}

    @server.tool(name="manage_labels")
    async def manage_labels(
        action: Annotated[
            Literal["create", "update", "delete"],
            (
                "'create' adds a user label (needs name); 'update' renames it or changes its visibility or "
                "color (needs label; omitted fields keep their values); 'delete' removes it after the user confirms."
            ),
        ],
        label: Annotated[
            str | None,
            (
                "For update and delete: the label's name as shown in Gmail or its ID (Label_...); an unknown "
                "or ambiguous name fails with not_found and the candidates. Not used by create."
            ),
        ] = None,
        name: Annotated[
            str | None,
            (
                "Label name to create, or the new name for update; a '/' nests it under a parent, e.g. "
                "'Clients/Acme'. Must differ from every existing label."
            ),
        ] = None,
        message_list_visibility: Annotated[
            Literal["show", "hide"] | None,
            "Whether messages with this label show it in the message list ('show') or not ('hide').",
        ] = None,
        label_list_visibility: Annotated[
            Literal["labelShow", "labelShowIfUnread", "labelHide"] | None,
            "Whether the label appears in Gmail's label list always, only when it has unread mail, or never.",
        ] = None,
        background_color: Annotated[
            str | None,
            "Background color as a hex value from Gmail's label palette, e.g. '#fb4c2f'; set it together with text_color.",
        ] = None,
        text_color: Annotated[
            str | None,
            "Text color as a hex value from Gmail's label palette, e.g. '#ffffff'; set it together with background_color.",
        ] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create, update or delete a Gmail user label. To put labels on mail or take them off use gmail_modify_messages, and to see existing labels gmail_list_labels. Returns status 'ok' and action with label (the Gmail label resource, including its id) for create and update, or label_id for delete; status 'cancelled' if the user declines a delete. Deleting asks the user to confirm and cannot be undone; Gmail removes the label from every message, but the messages are kept."""
        service = gmail_service()
        if action == "create":
            if label is not None:
                raise ValueError("action 'create' takes name, not label.")
            if not name:
                raise ValueError("action 'create' requires name.")
            created_request = LabelCreateRequest(
                name=name,
                message_list_visibility=message_list_visibility,
                label_list_visibility=label_list_visibility,
                background_color=background_color,
                text_color=text_color,
            )
            payload = _label_payload(
                created_request.name,
                created_request.message_list_visibility,
                created_request.label_list_visibility,
                created_request.background_color,
                created_request.text_color,
            )
            LOGGER.debug(f"Creating label {created_request.name}.")
            created = await execute_google_request(
                service.users().labels().create(userId="me", body=payload)
            )
            return {"status": "ok", "action": action, "label": created}
        if not label:
            raise ValueError(f"action '{action}' requires label (a label name or ID).")
        label_id = await LabelResolver(service, execute_google_request).resolve(label)
        if action == "update":
            update_request = LabelUpdateRequest(
                label_id=label_id,
                name=name,
                message_list_visibility=message_list_visibility,
                label_list_visibility=label_list_visibility,
                background_color=background_color,
                text_color=text_color,
            )
            payload = _label_payload(
                update_request.name,
                update_request.message_list_visibility,
                update_request.label_list_visibility,
                update_request.background_color,
                update_request.text_color,
            )
            if not payload:
                raise ValueError("action 'update' needs at least one of name, visibility or color.")
            LOGGER.debug(f"Updating label {update_request.label_id}.")
            updated = await execute_google_request(
                service.users().labels().patch(userId="me", id=update_request.label_id, body=payload)
            )
            return {"status": "ok", "action": action, "label": updated}
        delete_request = LabelDeleteRequest(label_id=label_id)
        LOGGER.debug(f"Deleting label {delete_request.label_id}.")
        await execute_google_request(
            service.users().labels().delete(userId="me", id=delete_request.label_id)
        )
        return {"status": "ok", "action": action, "label_id": delete_request.label_id}
