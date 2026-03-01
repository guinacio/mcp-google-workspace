"""MCP resources for Gmail datasets."""

from __future__ import annotations

import json

from fastmcp import Context, FastMCP

from .client import gmail_service
from .mime_utils import decode_rfc2047


def register_resources(server: FastMCP) -> None:
    @server.resource("gmail://inbox/summary", name="inbox_summary")
    async def inbox_summary(ctx: Context) -> str:
        service = gmail_service()
        unread = (
            service.users()
            .messages()
            .list(userId="me", q="is:unread in:inbox", maxResults=20)
            .execute()
            .get("resultSizeEstimate", 0)
        )
        latest = (
            service.users().messages().list(userId="me", labelIds=["INBOX"], maxResults=10).execute()
        )
        items = []
        for msg in latest.get("messages", []):
            full = (
                service.users().messages().get(userId="me", id=msg["id"], format="metadata").execute()
            )
            headers = {
                h.get("name", "").lower(): h.get("value", "")
                for h in full.get("payload", {}).get("headers", [])
            }
            items.append(
                {
                    "id": full.get("id"),
                    "subject": decode_rfc2047(headers.get("subject")),
                    "from": decode_rfc2047(headers.get("from")),
                    "date": headers.get("date"),
                }
            )
        await ctx.info("Built inbox summary resource.")
        return json.dumps({"unread_count": unread, "latest_messages": items}, indent=2)

    @server.resource("gmail://labels", name="gmail_labels")
    async def gmail_labels() -> str:
        service = gmail_service()
        labels = service.users().labels().list(userId="me").execute().get("labels", [])
        return json.dumps({"count": len(labels), "labels": labels}, indent=2)

    @server.resource("gmail://email/{message_id}", name="email_by_id")
    async def email_by_id(message_id: str) -> str:
        service = gmail_service()
        result = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        return json.dumps(result, indent=2)
