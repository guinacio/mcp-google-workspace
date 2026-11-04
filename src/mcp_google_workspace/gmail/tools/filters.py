"""Gmail filter (rule) management tool."""

from __future__ import annotations

import logging

from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ...common.confirmation import confirm_destructive_action
from ..client import gmail_service
from ..label_resolver import LabelResolver
from ..schemas import CreateFilterRequest, DeleteFilterRequest, FilterActionInput, FilterCriteriaInput

LOGGER = logging.getLogger(__name__)


def register(server: FastMCP) -> None:
    @server.tool(name="manage_filters")
    async def manage_filters(
        action: Annotated[
            Literal["list", "create", "delete"],
            (
                "'list' returns every filter; 'create' adds one (needs criteria and filter_action); 'delete' "
                "removes one after the user confirms (needs filter_id). Filters cannot be edited: delete and recreate."
            ),
        ],
        criteria: Annotated[
            FilterCriteriaInput | None,
            "For create: which incoming messages the filter matches; set at least one field, and a message must match all of them.",
        ] = None,
        filter_action: Annotated[
            FilterActionInput | None,
            "For create: what happens to matching messages; set at least one of add_labels, remove_labels or forward.",
        ] = None,
        filter_id: Annotated[str | None, "For delete: the filter's id, as returned by action 'list'."] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List, create or delete Gmail filters, the rules that label, archive, mark read or forward incoming mail as it arrives. A filter acts only on mail that arrives afterwards; to change existing messages use gmail_modify_messages. Returns action with filters and count for list, status 'ok' and filter (including its id) for create, or status 'ok' and filter_id for delete; status 'cancelled' if the user declines a delete. Deleting asks the user to confirm; mail a filter already labeled or moved stays as it is."""
        service = gmail_service()
        if action == "list":
            LOGGER.debug("Listing Gmail filters.")
            result = await execute_google_request(service.users().settings().filters().list(userId="me"))
            filters = result.get("filter", [])
            return {"action": action, "filters": filters, "count": len(filters)}
        if action == "create":
            if criteria is None or filter_action is None:
                raise ValueError("action 'create' requires criteria and filter_action.")
            request = CreateFilterRequest(criteria=criteria, action=filter_action)
            criteria_api = request.criteria.to_api()
            if not criteria_api:
                raise ValueError("criteria must include at least one match field.")
            resolver = LabelResolver(service, execute_google_request)
            action_api = request.action.to_api(
                add_label_ids=await resolver.resolve_all(request.action.add_labels),
                remove_label_ids=await resolver.resolve_all(request.action.remove_labels),
            )
            if not action_api:
                raise ValueError("filter_action must include add_labels, remove_labels or forward.")
            LOGGER.debug("Creating Gmail filter.")
            created = await execute_google_request(
                service.users()
                .settings()
                .filters()
                .create(userId="me", body={"criteria": criteria_api, "action": action_api})
            )
            return {"status": "ok", "action": action, "filter": created}
        if not filter_id:
            raise ValueError("action 'delete' requires filter_id.")
        delete_request = DeleteFilterRequest(filter_id=filter_id)
        if not await confirm_destructive_action(
            ctx, "delete_filter", f"Permanently delete Gmail filter {delete_request.filter_id}?"
        ):
            return {"status": "cancelled", "action": action, "filter_id": delete_request.filter_id}
        LOGGER.debug(f"Deleting Gmail filter {delete_request.filter_id}.")
        await execute_google_request(
            service.users().settings().filters().delete(userId="me", id=delete_request.filter_id)
        )
        return {"status": "ok", "action": action, "filter_id": delete_request.filter_id}
