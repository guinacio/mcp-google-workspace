"""Google Chat tools for spaces and messages."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastmcp import Context, FastMCP

from ..common.timezone import resolve_user_timezone
from googleapiclient.errors import HttpError

from ..common.async_ops import execute_google_request
from .client import chat_service, normalize_message_name, normalize_space_name, normalize_user_name, resolve_space_members
from .presentation import enrich_messages, space_envelope
from .schemas import (
    CreateMessageRequest,
    DeleteMessageRequest,
    FindDirectMessageRequest,
    GetMessageRequest,
    GetSpaceRequest,
    ListMessagesRequest,
    ListSpaceMembersRequest,
    ListSpacesRequest,
    PostSimpleMessageRequest,
    ReplyToMessageRequest,
    UpdateMessageRequest,
)

LOGGER = logging.getLogger(__name__)


def register_tools(server: FastMCP) -> None:
    @server.tool(name="list_spaces")
    async def list_spaces(request: ListSpacesRequest, ctx: Context) -> dict[str, Any]:
        """List Chat spaces, optionally enriching DMs with peer user details."""
        service = chat_service()
        await ctx.info("Listing Google Chat spaces.")
        result = await execute_google_request(
            service.spaces()
            .list(
                pageSize=request.page_size,
                pageToken=request.page_token,
                filter=request.filter,
            )
        )
        spaces = result.get("spaces", [])
        await ctx.report_progress(len(spaces), request.page_size, "Chat spaces page loaded")

        enriched_by_id: dict[str, dict[str, Any]] = {}
        if request.enrich_dms:
            dm_spaces = [s for s in spaces if s.get("spaceType") == "DIRECT_MESSAGE"]
            semaphore = asyncio.Semaphore(10)

            async def enrich_dm(space: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
                space_name = space.get("name", "")
                async with semaphore:
                    try:
                        peers, _ = await resolve_space_members(space_name)
                        return space_name, space_envelope(space, peers[0]) if peers else None
                    except Exception:
                        LOGGER.debug("DM enrichment failed for %s", space_name, exc_info=True)
                        return space_name, None

            for idx, (space_name, enriched) in enumerate(await asyncio.gather(*(enrich_dm(space) for space in dm_spaces)), 1):
                if enriched is not None:
                    enriched_by_id[space_name] = enriched
                await ctx.report_progress(
                    len(spaces) + idx, len(spaces) + len(dm_spaces), "Enriching DM spaces"
                )

        if request.enrich_dms:
            enriched_spaces = [
                enriched_by_id.get(str(space.get("name")), space_envelope(space)) for space in spaces
            ]
        else:
            enriched_spaces = [space_envelope(space) for space in spaces]
        return {
            "spaces": enriched_spaces,
            "next_page_token": result.get("nextPageToken"),
            "count": len(spaces),
        }

    @server.tool(name="get_space")
    async def get_space(request: GetSpaceRequest, ctx: Context) -> dict[str, Any]:
        service = chat_service()
        name = normalize_space_name(request.space_name)
        await ctx.info(f"Getting Chat space {name}.")
        space = await execute_google_request(service.spaces().get(name=name))
        try:
            peers, _ = await resolve_space_members(name) if space.get("spaceType") == "DIRECT_MESSAGE" else ([], None)
        except Exception:
            LOGGER.debug("DM enrichment failed for %s", name, exc_info=True)
            peers = []
        return space_envelope(space, peers[0] if peers else None)

    @server.tool(name="find_direct_message")
    async def find_direct_message(
        request: FindDirectMessageRequest, ctx: Context
    ) -> dict[str, Any]:
        """Find the direct-message space between you and another user."""
        service = chat_service()
        user_name = normalize_user_name(request.user)
        await ctx.info(f"Finding DM space with {user_name}.")
        try:
            space = await execute_google_request(
                service.spaces().findDirectMessage(name=user_name)
            )
        except HttpError as exc:
            if exc.resp.status == 404:
                return {
                    "found": False,
                    "user": user_name,
                    "reason": "No direct message space exists with this user.",
                }
            raise
        return {
            "found": True,
            "user": user_name,
            "space": space_envelope(space),
        }

    @server.tool(name="list_messages")
    async def list_messages(request: ListMessagesRequest, ctx: Context) -> dict[str, Any]:
        service = chat_service()
        parent = normalize_space_name(request.space_name)
        await ctx.info(f"Listing messages for {parent}.")
        query: dict[str, Any] = {
            "parent": parent,
            "pageSize": request.page_size,
            "pageToken": request.page_token,
            "filter": request.filter,
            "orderBy": request.order_by,
        }
        if request.thread_name:
            query["thread.name"] = request.thread_name
        result = await execute_google_request(service.spaces().messages().list(**query))
        account_timezone = await resolve_user_timezone()
        messages = result.get("messages", [])
        await ctx.report_progress(len(messages), request.page_size, "Chat messages page loaded")
        return {
            "messages": (
                await enrich_messages(messages, account_timezone=account_timezone)
                if request.enrich_authors
                else messages
            ),
            "next_page_token": result.get("nextPageToken"),
            "count": len(messages),
            "account_timezone": account_timezone,
        }

    @server.tool(name="get_message")
    async def get_message(request: GetMessageRequest, ctx: Context) -> dict[str, Any]:
        service = chat_service()
        name = normalize_message_name(request.message_name)
        await ctx.info(f"Getting Chat message {name}.")
        message = await execute_google_request(service.spaces().messages().get(name=name))
        account_timezone = await resolve_user_timezone()
        return (await enrich_messages([message], account_timezone=account_timezone, max_text=None))[0]

    @server.tool(name="list_space_members")
    async def list_space_members(
        request: ListSpaceMembersRequest, ctx: Context
    ) -> dict[str, Any]:
        """List people in a space with display names and email addresses when available."""
        space_name = normalize_space_name(request.space_name)
        await ctx.info(f"Listing members for Chat space {space_name}.")
        members, next_page_token = await resolve_space_members(
            space_name, exclude_self=not request.include_self, page_token=request.page_token
        )
        return {
            "space_id": space_name,
            "members": [
                {
                    "name": member.get("displayName") or member.get("name"),
                    "email": member.get("email"),
                    "user_id": member.get("name"),
                    "type": member.get("type"),
                }
                for member in members
            ],
            "count": len(members),
            "next_page_token": next_page_token,
        }

    @server.tool(name="create_message")
    async def create_message(request: CreateMessageRequest, ctx: Context) -> dict[str, Any]:
        service = chat_service()
        parent = normalize_space_name(request.space_name)
        body: dict[str, Any] = {"text": request.text}
        if request.private_message_viewer:
            body["privateMessageViewer"] = {"name": request.private_message_viewer}
        query: dict[str, Any] = {
            "parent": parent,
            "body": body,
            "threadKey": request.thread_key,
            "requestId": request.request_id,
            "messageId": request.message_id,
            "messageReplyOption": request.message_reply_option,
        }
        await ctx.info(f"Creating Chat message in {parent}.")
        if request.notify:
            response = await ctx.elicit(
                f"Send Chat message to {parent}?",
                response_type=bool,  # type: ignore[arg-type]
            )
            if response.action != "accept" or not bool(response.data):
                return {"status": "cancelled"}
        created = await execute_google_request(service.spaces().messages().create(**query))
        return {"status": "ok", "message": created}

    @server.tool(name="delete_message")
    async def delete_message(request: DeleteMessageRequest, ctx: Context) -> dict[str, Any]:
        service = chat_service()
        name = normalize_message_name(request.message_name)
        if not request.force:
            response = await ctx.elicit(
                f"Delete Chat message {name}?",
                response_type=bool,  # type: ignore[arg-type]
            )
            if response.action != "accept" or not bool(response.data):
                return {"status": "cancelled"}
        await execute_google_request(service.spaces().messages().delete(name=name))
        return {"status": "ok", "message_name": name}

    @server.tool(name="update_message")
    async def update_message(request: UpdateMessageRequest, ctx: Context) -> dict[str, Any]:
        service = chat_service()
        name = normalize_message_name(request.message_name)
        await ctx.info(f"Updating Chat message {name}.")
        updated = await execute_google_request(
            service.spaces().messages().patch(
                name=name,
                updateMask=request.update_mask,
                body={"name": name, "text": request.text},
            )
        )
        return {"status": "ok", "message": updated}

    @server.tool(name="post_message_simple")
    async def post_message_simple(request: PostSimpleMessageRequest, ctx: Context) -> dict[str, Any]:
        """Post a simple text message to a Chat space."""
        service = chat_service()
        parent = normalize_space_name(request.space_name)
        if request.notify:
            response = await ctx.elicit(
                f"Send Chat message to {parent}?",
                response_type=bool,  # type: ignore[arg-type]
            )
            if response.action != "accept" or not bool(response.data):
                return {"status": "cancelled"}
        created = await execute_google_request(
            service.spaces().messages().create(
                parent=parent,
                body={"text": request.text},
            )
        )
        return {"status": "ok", "message": created}

    @server.tool(name="reply_to_message")
    async def reply_to_message(request: ReplyToMessageRequest, ctx: Context) -> dict[str, Any]:
        """Reply to an existing message, preserving its thread context."""
        service = chat_service()
        message_name = normalize_message_name(request.message_name)
        if request.notify:
            response = await ctx.elicit(
                f"Reply to Chat message {message_name}?",
                response_type=bool,  # type: ignore[arg-type]
            )
            if response.action != "accept" or not bool(response.data):
                return {"status": "cancelled"}
        source = await execute_google_request(service.spaces().messages().get(name=message_name))
        parent = message_name.split("/messages/", 1)[0]
        body: dict[str, Any] = {"text": request.text}
        thread_name = source.get("thread", {}).get("name")
        if thread_name:
            body["thread"] = {"name": thread_name}
        created = await execute_google_request(
            service.spaces().messages().create(
                parent=parent,
                body=body,
            )
        )
        return {"status": "ok", "message": created, "replied_to": message_name, "thread_name": thread_name}
