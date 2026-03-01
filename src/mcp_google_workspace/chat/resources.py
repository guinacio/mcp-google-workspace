"""MCP resources for Google Chat data."""

from __future__ import annotations

import json

from fastmcp import FastMCP

from .client import chat_service, normalize_space_name, normalize_user_name


def register_resources(server: FastMCP) -> None:
    @server.resource("chat://spaces", name="chat_spaces")
    async def chat_spaces() -> str:
        service = chat_service()
        spaces = service.spaces().list(pageSize=100).execute()
        return json.dumps(spaces, indent=2)

    @server.resource("chat://space/{space_id}/messages", name="chat_space_messages")
    async def chat_space_messages(space_id: str) -> str:
        service = chat_service()
        parent = normalize_space_name(space_id)
        messages = service.spaces().messages().list(parent=parent, pageSize=100).execute()
        return json.dumps(messages, indent=2)

    @server.resource("chat://space/{space_id}/members", name="chat_space_members")
    async def chat_space_members(space_id: str) -> str:
        service = chat_service()
        parent = normalize_space_name(space_id)
        memberships = service.spaces().members().list(parent=parent, pageSize=100).execute()
        return json.dumps(memberships, indent=2)

    async def _get_chat_user(user_ref: str) -> str:
        service = chat_service()
        name = normalize_user_name(user_ref)
        user = service.users().get(name=name).execute()
        return json.dumps(user, indent=2)

    @server.resource("chat://users/{user_ref}", name="chat_users")
    async def chat_users(user_ref: str) -> str:
        return await _get_chat_user(user_ref)

    # Static "current user" resource appears clearly in resource listings.
    @server.resource("chat://users/me", name="chat_users_me")
    async def chat_users_me() -> str:
        return await _get_chat_user("me")
