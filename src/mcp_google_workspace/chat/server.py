"""Google Chat FastMCP subserver."""

from __future__ import annotations

from fastmcp import Context, FastMCP

from .client import chat_service, normalize_space_name
from .prompts import register_prompts
from .resources import register_resources
from .tools import register_tools

chat_mcp = FastMCP(name="chat-mcp", instructions="Google Chat MCP subserver.")

register_tools(chat_mcp)
register_resources(chat_mcp)
register_prompts(chat_mcp)


@chat_mcp.tool(name="summarize_space_messages")
async def summarize_space_messages(space_name: str, ctx: Context, limit: int = 20) -> dict[str, str]:
    service = chat_service()
    parent = normalize_space_name(space_name)
    result = service.spaces().messages().list(parent=parent, pageSize=limit).execute()
    messages = result.get("messages", [])
    text_blob = "\n".join((m.get("text") or "").strip() for m in messages if m.get("text"))
    if not text_blob:
        return {"space": parent, "summary": "No text messages available to summarize."}
    summary = await ctx.sample(
        f"Summarize these Chat messages in 5 bullets:\n\n{text_blob}",
        max_tokens=220,
    )
    return {"space": parent, "summary": summary.text or ""}
