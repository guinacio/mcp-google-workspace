"""Google Keep FastMCP subserver."""

from __future__ import annotations

from fastmcp import Context, FastMCP

from ..common.component_annotations import apply_default_tool_annotations
from .client import keep_service, normalize_note_name
from .prompts import register_prompts
from .resources import register_resources
from .tools import register_tools

keep_mcp = FastMCP(name="keep-mcp", instructions="Google Keep MCP subserver.")

register_tools(keep_mcp)
register_resources(keep_mcp)
register_prompts(keep_mcp)


@keep_mcp.tool(name="summarize_note")
async def summarize_note(note_name: str, ctx: Context) -> dict[str, str]:
    service = keep_service()
    name = normalize_note_name(note_name)
    note = service.notes().get(name=name).execute()
    body = note.get("body", {})
    text = body.get("text", {}).get("text", "")
    if not text:
        items = body.get("list", {}).get("listItems", [])
        text = "\n".join(
            f"- [{'x' if item.get('checked') else ' '}] {item.get('text', {}).get('text', '')}"
            for item in items
        )
    summary = await ctx.sample(
        f"Summarize this Keep note in up to 5 bullets:\n\n{text}",
        max_tokens=220,
    )
    return {"note_name": name, "summary": summary.text or ""}


apply_default_tool_annotations(keep_mcp)
