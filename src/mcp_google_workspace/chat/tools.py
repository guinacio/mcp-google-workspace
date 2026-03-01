"""Google Chat tools for spaces and messages."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from .client import chat_service, normalize_message_name, normalize_space_name
from .schemas import (
    CreateMessageRequest,
    DeleteMessageRequest,
    GetMessageRequest,
    GetSpaceRequest,
    ListMessagesRequest,
    ListSpacesRequest,
    UpdateMessageRequest,
)


def register_tools(server: FastMCP) -> None:
    @server.tool(name="list_spaces")
    async def list_spaces(request: ListSpacesRequest, ctx: Context) -> dict[str, Any]:
        service = chat_service()
        await ctx.info("Listing Google Chat spaces.")
        result = (
            service.spaces()
            .list(
                pageSize=request.page_size,
                pageToken=request.page_token,
                filter=request.filter,
            )
            .execute()
        )
        spaces = result.get("spaces", [])
        await ctx.report_progress(len(spaces), request.page_size, "Chat spaces page loaded")
        return {
            "spaces": spaces,
            "next_page_token": result.get("nextPageToken"),
            "count": len(spaces),
        }

    @server.tool(name="get_space")
    async def get_space(request: GetSpaceRequest, ctx: Context) -> dict[str, Any]:
        service = chat_service()
        name = normalize_space_name(request.space_name)
        await ctx.info(f"Getting Chat space {name}.")
        return service.spaces().get(name=name).execute()

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
        result = service.spaces().messages().list(**query).execute()
        messages = result.get("messages", [])
        await ctx.report_progress(len(messages), request.page_size, "Chat messages page loaded")
        return {
            "messages": messages,
            "next_page_token": result.get("nextPageToken"),
            "count": len(messages),
        }

    @server.tool(name="get_message")
    async def get_message(request: GetMessageRequest, ctx: Context) -> dict[str, Any]:
        service = chat_service()
        name = normalize_message_name(request.message_name)
        await ctx.info(f"Getting Chat message {name}.")
        return service.spaces().messages().get(name=name).execute()

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
        created = service.spaces().messages().create(**query).execute()
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
        service.spaces().messages().delete(name=name).execute()
        return {"status": "ok", "message_name": name}

    @server.tool(name="update_message")
    async def update_message(request: UpdateMessageRequest, ctx: Context) -> dict[str, Any]:
        service = chat_service()
        name = normalize_message_name(request.message_name)
        await ctx.info(f"Updating Chat message {name}.")
        updated = service.spaces().messages().patch(
            name=name,
            updateMask=request.update_mask,
            body={"name": name, "text": request.text},
        ).execute()
        return {"status": "ok", "message": updated}
