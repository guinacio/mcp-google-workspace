"""Gmail filter (rule) management tools."""

from __future__ import annotations

import logging

from typing import Annotated, Any

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ...common.confirmation import confirm_destructive_action
from ..client import gmail_service
from ..schemas import CreateFilterRequest, DeleteFilterRequest, FilterActionInput, FilterCriteriaInput

LOGGER = logging.getLogger(__name__)


def register(server: FastMCP) -> None:
    @server.tool(name="list_filters")
    async def list_filters(ctx: Context) -> dict[str, Any]:
        """List the account's Gmail filters, the rules that label, archive or forward incoming mail. Returns filters, each with its id, criteria and action as Gmail stores them, and count. Pass a filter's id to gmail_delete_filter; filters cannot be edited, so replace one with gmail_create_filter."""
        service = gmail_service()
        LOGGER.debug("Listing Gmail filters.")
        result = await execute_google_request(service.users().settings().filters().list(userId="me"))
        filters = result.get("filter", [])
        return {"filters": filters, "count": len(filters)}

    @server.tool(name="create_filter")
    async def create_filter(
        criteria: Annotated[
            FilterCriteriaInput,
            "Which incoming messages the filter matches; set at least one field, and a message must match all of the fields given.",
        ],
        action: Annotated[
            FilterActionInput,
            "What happens to matching messages; set at least one of add_label_ids, remove_label_ids or forward.",
        ],
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create a Gmail filter that acts on incoming mail matching criteria: label it, archive it (remove INBOX), mark it read (remove UNREAD) or forward it. It applies to mail that arrives afterwards; to change existing messages, find them with gmail_search_emails and use gmail_batch_modify. Returns the created Gmail filter under filter, including its id."""
        request = CreateFilterRequest(
            criteria=criteria,
            action=action,
        )
        service = gmail_service()
        criteria_api = request.criteria.to_api()
        action_api = request.action.to_api()
        if not criteria_api:
            raise ValueError("criteria must include at least one match field.")
        if not action_api:
            raise ValueError("action must include add/remove labels or forward.")

        LOGGER.debug("Creating Gmail filter.")
        created = await execute_google_request(
            service.users()
            .settings()
            .filters()
            .create(userId="me", body={"criteria": criteria_api, "action": action_api})
        )
        return {"filter": created}

    @server.tool(name="delete_filter")
    async def delete_filter(
        filter_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Delete one Gmail filter after asking the user to confirm; mail it already labeled or moved stays as it is. Returns status 'ok' with the filter_id, or status 'cancelled' if the user declines. Cannot be undone, but the filter can be recreated with gmail_create_filter from the criteria and action shown by gmail_list_filters."""
        request = DeleteFilterRequest(filter_id=filter_id)
        if not await confirm_destructive_action(
            ctx, "delete_filter", f"Permanently delete Gmail filter {request.filter_id}?"
        ):
            return {"status": "cancelled", "filter_id": request.filter_id}
        service = gmail_service()
        LOGGER.debug(f"Deleting Gmail filter {request.filter_id}.")
        await execute_google_request(
            service.users().settings().filters().delete(userId="me", id=request.filter_id)
        )
        return {"status": "ok", "filter_id": request.filter_id}
