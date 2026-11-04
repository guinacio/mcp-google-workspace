"""Gmail settings tools: forwarding addresses and vacation responder."""

from __future__ import annotations

import logging

from typing import Annotated, Any

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ...common.confirmation import confirm_destructive_action
from ..client import gmail_service
from ..schemas import ForwardingAddressRequest, UpdateVacationSettingsRequest

LOGGER = logging.getLogger(__name__)


def register(server: FastMCP) -> None:
    @server.tool(name="list_forwarding_addresses")
    async def list_forwarding_addresses(ctx: Context) -> dict[str, Any]:
        """List the addresses this Gmail account may forward mail to, with their verification status. A filter's forward action (gmail_create_filter) needs an address whose status is 'accepted'. Returns forwarding_addresses (forwardingEmail and verificationStatus, 'accepted' or 'pending') and count."""
        service = gmail_service()
        LOGGER.debug("Listing Gmail forwarding addresses.")
        result = await execute_google_request(
            service.users().settings().forwardingAddresses().list(userId="me")
        )
        addresses = result.get("forwardingAddresses", [])
        return {"forwarding_addresses": addresses, "count": len(addresses)}

    @server.tool(name="get_forwarding_address")
    async def get_forwarding_address(
        forwarding_email: Annotated[str, "Forwarding address to look up, e.g. 'archive@example.com'; must be a valid email address."],
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Look up one forwarding address of this Gmail account and its verification status. Use gmail_list_forwarding_addresses to see all of them at once. Returns forwarding_address with forwardingEmail and verificationStatus ('accepted' or 'pending')."""
        request = ForwardingAddressRequest(forwarding_email=forwarding_email)
        service = gmail_service()
        LOGGER.debug(f"Fetching forwarding address {request.forwarding_email}.")
        address = await execute_google_request(
            service.users()
            .settings()
            .forwardingAddresses()
            .get(userId="me", forwardingEmail=str(request.forwarding_email))
        )
        return {"forwarding_address": address}

    @server.tool(name="create_forwarding_address")
    async def create_forwarding_address(
        forwarding_email: Annotated[str, "Address to allow forwarding to, e.g. 'archive@example.com'; must be a valid email address."],
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Register an address this Gmail account may forward mail to; when Gmail requires ownership verification it emails that address, and the status stays 'pending' until the link is confirmed. Gmail allows this only for service accounts with domain-wide delegation, so an ordinary user sign-in gets a permission error. Returns forwarding_address with forwardingEmail and verificationStatus. Actual forwarding is set up with a gmail_create_filter forward action."""
        request = ForwardingAddressRequest(forwarding_email=forwarding_email)
        service = gmail_service()
        LOGGER.debug(f"Creating forwarding address {request.forwarding_email}.")
        created = await execute_google_request(
            service.users()
            .settings()
            .forwardingAddresses()
            .create(userId="me", body={"forwardingEmail": str(request.forwarding_email)})
        )
        return {"forwarding_address": created}

    @server.tool(name="delete_forwarding_address")
    async def delete_forwarding_address(
        forwarding_email: Annotated[str, "Forwarding address to remove, as listed by gmail_list_forwarding_addresses."],
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Remove one forwarding address from this Gmail account after asking the user to confirm. Like gmail_create_forwarding_address, Gmail allows it only for service accounts with domain-wide delegation. Returns status 'ok' with the forwarding_email, or status 'cancelled' if the user declines."""
        request = ForwardingAddressRequest(forwarding_email=forwarding_email)
        if not await confirm_destructive_action(
            ctx,
            "delete_forwarding_address",
            f"Delete Gmail forwarding address {request.forwarding_email}?",
        ):
            return {"status": "cancelled", "forwarding_email": str(request.forwarding_email)}
        service = gmail_service()
        LOGGER.debug(f"Deleting forwarding address {request.forwarding_email}.")
        await execute_google_request(
            service.users().settings().forwardingAddresses().delete(
                userId="me",
                forwardingEmail=str(request.forwarding_email),
            )
        )
        return {"status": "ok", "forwarding_email": str(request.forwarding_email)}

    @server.tool(name="get_vacation_settings")
    async def get_vacation_settings(ctx: Context) -> dict[str, Any]:
        """Read the Gmail vacation auto-reply settings. Check them before gmail_update_vacation_settings, which resets the on/off and restriction flags on every call. Returns vacation with enableAutoReply, responseSubject, responseBodyPlainText, responseBodyHtml, restrictToContacts, restrictToDomain, and startTime/endTime in epoch milliseconds."""
        service = gmail_service()
        LOGGER.debug("Getting Gmail vacation settings.")
        settings = await execute_google_request(service.users().settings().getVacation(userId="me"))
        return {"vacation": settings}

    @server.tool(name="update_vacation_settings")
    async def update_vacation_settings(
        enable_auto_reply: Annotated[
            bool,
            "When true, the auto-reply is on (between start_time and end_time when set); false, the default, turns it off.",
        ] = False,
        response_subject: Annotated[str | None, "Subject line used on the vacation auto-reply."] = None,
        response_body_plain_text: Annotated[
            str | None, "Plain-text body of the vacation auto-reply."
        ] = None,
        response_body_html: Annotated[str | None, "HTML body of the vacation auto-reply."] = None,
        restrict_to_contacts: Annotated[
            bool, "When true, only senders in the user's contacts get the auto-reply; default false (every sender)."
        ] = False,
        restrict_to_domain: Annotated[
            bool,
            "When true, only senders in the account's own Google Workspace domain get the auto-reply (Workspace accounts only); default false.",
        ] = False,
        start_time: Annotated[
            int | None, "Auto-reply start time as epoch milliseconds; omit for immediate/no start bound."
        ] = None,
        end_time: Annotated[
            int | None, "Auto-reply end time as epoch milliseconds; omit for no end bound."
        ] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Turn the Gmail vacation auto-reply on or off and set its subject, body, audience and schedule. enable_auto_reply, restrict_to_contacts and restrict_to_domain are sent on every call and default to false, so a call without enable_auto_reply=true turns the auto-reply off; read the current values with gmail_get_vacation_settings first. Returns the saved settings under vacation. While on, Gmail answers incoming mail automatically."""
        request = UpdateVacationSettingsRequest(
            enable_auto_reply=enable_auto_reply,
            response_subject=response_subject,
            response_body_plain_text=response_body_plain_text,
            response_body_html=response_body_html,
            restrict_to_contacts=restrict_to_contacts,
            restrict_to_domain=restrict_to_domain,
            start_time=start_time,
            end_time=end_time,
        )
        service = gmail_service()
        LOGGER.debug("Updating Gmail vacation settings.")
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
        updated = await execute_google_request(
            service.users().settings().updateVacation(userId="me", body=payload)
        )
        return {"vacation": updated}
