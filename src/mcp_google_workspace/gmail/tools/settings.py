"""Gmail settings tools: forwarding addresses and vacation responder."""

from __future__ import annotations

import logging

from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ...common.confirmation import confirm_destructive_action
from ..client import gmail_service
from ..schemas import ForwardingAddressRequest, UpdateVacationSettingsRequest

LOGGER = logging.getLogger(__name__)


def register(server: FastMCP) -> None:
    @server.tool(name="manage_forwarding")
    async def manage_forwarding(
        action: Annotated[
            Literal["list", "get", "create", "delete"],
            (
                "'list' returns every forwarding address; 'get' one address; 'create' registers a new one; "
                "'delete' removes one after the user confirms. get, create and delete need forwarding_email."
            ),
        ],
        forwarding_email: Annotated[
            str | None,
            "For get, create and delete: the forwarding address, e.g. 'archive@example.com'; must be a valid email address.",
        ] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List, look up, register or remove the addresses this Gmail account may forward mail to, with their verification status. Actual forwarding is set up with a gmail_manage_filters filter whose forward address is 'accepted'; create and delete work only for service accounts with domain-wide delegation, so an ordinary user sign-in gets a permission error. Returns action with forwarding_addresses and count (list), or forwarding_address with forwardingEmail and verificationStatus ('accepted' or 'pending'); delete returns status 'ok' or 'cancelled'. Creating may email the address a verification link; deleting asks the user to confirm."""
        service = gmail_service()
        addresses = service.users().settings().forwardingAddresses()
        if action == "list":
            LOGGER.debug("Listing Gmail forwarding addresses.")
            result = await execute_google_request(addresses.list(userId="me"))
            listed = result.get("forwardingAddresses", [])
            return {"action": action, "forwarding_addresses": listed, "count": len(listed)}
        if not forwarding_email:
            raise ValueError(f"action '{action}' requires forwarding_email.")
        request = ForwardingAddressRequest(forwarding_email=forwarding_email)
        email = str(request.forwarding_email)
        if action == "get":
            LOGGER.debug(f"Fetching forwarding address {email}.")
            address = await execute_google_request(addresses.get(userId="me", forwardingEmail=email))
            return {"action": action, "forwarding_address": address}
        if action == "create":
            LOGGER.debug(f"Creating forwarding address {email}.")
            created = await execute_google_request(
                addresses.create(userId="me", body={"forwardingEmail": email})
            )
            return {"status": "ok", "action": action, "forwarding_address": created}
        if not await confirm_destructive_action(
            ctx,
            "delete_forwarding_address",
            f"Delete Gmail forwarding address {request.forwarding_email}?",
        ):
            return {"status": "cancelled", "action": action, "forwarding_email": email}
        LOGGER.debug(f"Deleting forwarding address {email}.")
        await execute_google_request(addresses.delete(userId="me", forwardingEmail=email))
        return {"status": "ok", "action": action, "forwarding_email": email}

    @server.tool(name="manage_vacation")
    async def manage_vacation(
        action: Annotated[
            Literal["get", "update"],
            "'get' reads the current auto-reply settings; 'update' saves new ones (see enable_auto_reply).",
        ],
        enable_auto_reply: Annotated[
            bool,
            (
                "For update: true turns the auto-reply on (between start_time and end_time when set); false, "
                "the default, turns it off, so an update without it switches the auto-reply off."
            ),
        ] = False,
        response_subject: Annotated[str | None, "For update: subject line of the auto-reply."] = None,
        response_body_plain_text: Annotated[str | None, "For update: plain-text body of the auto-reply."] = None,
        response_body_html: Annotated[str | None, "For update: HTML body of the auto-reply."] = None,
        restrict_to_contacts: Annotated[
            bool, "For update: when true only senders in the user's contacts get the auto-reply; default false (every sender)."
        ] = False,
        restrict_to_domain: Annotated[
            bool,
            "For update: when true only senders in the account's own Google Workspace domain get the auto-reply (Workspace accounts only); default false.",
        ] = False,
        start_time: Annotated[
            int | None, "For update: auto-reply start as epoch milliseconds; omit for no start bound."
        ] = None,
        end_time: Annotated[
            int | None, "For update: auto-reply end as epoch milliseconds; omit for no end bound."
        ] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read or change the Gmail vacation auto-reply: on or off, subject, body, audience and schedule. An update sends enable_auto_reply, restrict_to_contacts and restrict_to_domain on every call with default false, so read the current values with action 'get' first and pass again what should stay. Returns action and vacation with enableAutoReply, responseSubject, responseBodyPlainText, responseBodyHtml, restrictToContacts, restrictToDomain and startTime/endTime in epoch milliseconds, plus status 'ok' for update. While on, Gmail answers incoming mail automatically."""
        service = gmail_service()
        if action == "get":
            LOGGER.debug("Getting Gmail vacation settings.")
            settings = await execute_google_request(service.users().settings().getVacation(userId="me"))
            return {"action": action, "vacation": settings}
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
        return {"status": "ok", "action": action, "vacation": updated}
