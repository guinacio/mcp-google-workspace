"""Gmail settings tools: forwarding addresses and vacation responder."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from ..client import gmail_service
from ..schemas import ForwardingAddressRequest, UpdateVacationSettingsRequest


def register(server: FastMCP) -> None:
    @server.tool(name="list_forwarding_addresses")
    async def list_forwarding_addresses(ctx: Context) -> dict[str, Any]:
        """List forwarding addresses configured in Gmail settings."""
        service = gmail_service()
        await ctx.info("Listing Gmail forwarding addresses.")
        result = service.users().settings().forwardingAddresses().list(userId="me").execute()
        addresses = result.get("forwardingAddresses", [])
        return {"forwarding_addresses": addresses, "count": len(addresses)}

    @server.tool(name="get_forwarding_address")
    async def get_forwarding_address(request: ForwardingAddressRequest, ctx: Context) -> dict[str, Any]:
        """Get forwarding address status/details for a specific email."""
        service = gmail_service()
        await ctx.info(f"Fetching forwarding address {request.forwarding_email}.")
        address = (
            service.users()
            .settings()
            .forwardingAddresses()
            .get(userId="me", forwardingEmail=str(request.forwarding_email))
            .execute()
        )
        return {"forwarding_address": address}

    @server.tool(name="create_forwarding_address")
    async def create_forwarding_address(request: ForwardingAddressRequest, ctx: Context) -> dict[str, Any]:
        """Create a forwarding address entry in Gmail settings."""
        service = gmail_service()
        await ctx.info(f"Creating forwarding address {request.forwarding_email}.")
        created = (
            service.users()
            .settings()
            .forwardingAddresses()
            .create(userId="me", body={"forwardingEmail": str(request.forwarding_email)})
            .execute()
        )
        return {"forwarding_address": created}

    @server.tool(name="delete_forwarding_address")
    async def delete_forwarding_address(request: ForwardingAddressRequest, ctx: Context) -> dict[str, Any]:
        """Delete a forwarding address from Gmail settings."""
        service = gmail_service()
        await ctx.info(f"Deleting forwarding address {request.forwarding_email}.")
        service.users().settings().forwardingAddresses().delete(
            userId="me",
            forwardingEmail=str(request.forwarding_email),
        ).execute()
        return {"status": "ok", "forwarding_email": str(request.forwarding_email)}

    @server.tool(name="get_vacation_settings")
    async def get_vacation_settings(ctx: Context) -> dict[str, Any]:
        """Read current vacation responder (auto-reply) settings."""
        service = gmail_service()
        await ctx.info("Getting Gmail vacation settings.")
        settings = service.users().settings().getVacation(userId="me").execute()
        return {"vacation": settings}

    @server.tool(name="update_vacation_settings")
    async def update_vacation_settings(
        request: UpdateVacationSettingsRequest,
        ctx: Context,
    ) -> dict[str, Any]:
        """Update vacation responder settings and scheduling options."""
        service = gmail_service()
        await ctx.info("Updating Gmail vacation settings.")
        payload: dict[str, Any] = {
            "enableAutoReply": request.enable_auto_reply,
            "restrictToContacts": request.restrict_to_contacts,
            "restrictToDomain": request.restrict_to_domain,
        }
        if request.response_subject is not None:
            payload["responseSubject"] = request.response_subject
        if request.response_body_plain_text is not None:
            payload["responseBodyPlainText"] = request.response_body_plain_text
        if request.response_body_html is not None:
            payload["responseBodyHtml"] = request.response_body_html
        if request.start_time is not None:
            payload["startTime"] = request.start_time
        if request.end_time is not None:
            payload["endTime"] = request.end_time
        updated = service.users().settings().updateVacation(userId="me", body=payload).execute()
        return {"vacation": updated}
