"""Gmail filter (rule) management tools."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from ..client import gmail_service
from ..schemas import CreateFilterRequest, DeleteFilterRequest


def register(server: FastMCP) -> None:
    @server.tool(name="list_filters")
    async def list_filters(ctx: Context) -> dict[str, Any]:
        """List Gmail server-side filters (rules)."""
        service = gmail_service()
        await ctx.info("Listing Gmail filters.")
        result = service.users().settings().filters().list(userId="me").execute()
        filters = result.get("filter", [])
        return {"filters": filters, "count": len(filters)}

    @server.tool(name="create_filter")
    async def create_filter(request: CreateFilterRequest, ctx: Context) -> dict[str, Any]:
        """Create a Gmail filter from criteria and action blocks."""
        service = gmail_service()
        criteria = request.criteria.to_api()
        action = request.action.to_api()
        if not criteria:
            raise ValueError("criteria must include at least one match field.")
        if not action:
            raise ValueError("action must include add/remove labels or forward.")

        await ctx.info("Creating Gmail filter.")
        created = (
            service.users()
            .settings()
            .filters()
            .create(userId="me", body={"criteria": criteria, "action": action})
            .execute()
        )
        return {"filter": created}

    @server.tool(name="delete_filter")
    async def delete_filter(request: DeleteFilterRequest, ctx: Context) -> dict[str, Any]:
        """Delete a Gmail filter by ID."""
        service = gmail_service()
        await ctx.info(f"Deleting Gmail filter {request.filter_id}.")
        service.users().settings().filters().delete(userId="me", id=request.filter_id).execute()
        return {"status": "ok", "filter_id": request.filter_id}
