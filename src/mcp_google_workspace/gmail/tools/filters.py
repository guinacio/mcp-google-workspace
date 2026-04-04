"""Gmail filter (rule) management tools."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ..client import gmail_service
from ..schemas import CreateFilterRequest, DeleteFilterRequest, FilterActionInput, FilterCriteriaInput


def register(server: FastMCP) -> None:
    @server.tool(name="list_filters")
    async def list_filters(ctx: Context) -> dict[str, Any]:
        """List Gmail server-side filters (rules)."""
        service = gmail_service()
        await ctx.info("Listing Gmail filters.")
        result = await execute_google_request(service.users().settings().filters().list(userId="me"))
        filters = result.get("filter", [])
        return {"filters": filters, "count": len(filters)}

    @server.tool(name="create_filter")
    async def create_filter(
        criteria: FilterCriteriaInput,
        action: FilterActionInput,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create a Gmail filter from criteria and action blocks."""
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

        if ctx is not None:
            await ctx.info("Creating Gmail filter.")
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
        """Delete a Gmail filter by ID."""
        request = DeleteFilterRequest(filter_id=filter_id)
        service = gmail_service()
        if ctx is not None:
            await ctx.info(f"Deleting Gmail filter {request.filter_id}.")
        await execute_google_request(
            service.users().settings().filters().delete(userId="me", id=request.filter_id)
        )
        return {"status": "ok", "filter_id": request.filter_id}
