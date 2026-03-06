"""Gmail FastMCP server registration."""

from __future__ import annotations

from fastmcp import Context, FastMCP

from ..common.component_annotations import apply_default_tool_annotations
from .client import gmail_service
from .mime_utils import extract_message_bodies
from .prompts import register_prompts
from .resources import register_resources
from .tools import register_tools

gmail_mcp = FastMCP(name="gmail-mcp", instructions="Production Gmail MCP server.")

register_tools(gmail_mcp)
register_resources(gmail_mcp)
register_prompts(gmail_mcp)


@gmail_mcp.tool(name="summarize_email")
async def summarize_email(message_id: str, ctx: Context) -> dict[str, str]:
    """Use MCP sampling to summarize an email body."""
    service = gmail_service()
    message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    payload = message.get("payload", {})
    bodies = extract_message_bodies(payload)
    source = bodies.get("text") or message.get("snippet", "")

    summary = await ctx.sample(
        f"Summarize this email in up to 5 bullets:\n\n{source}",
        max_tokens=240,
    )
    return {"message_id": message_id, "summary": summary.text or ""}


apply_default_tool_annotations(gmail_mcp)
