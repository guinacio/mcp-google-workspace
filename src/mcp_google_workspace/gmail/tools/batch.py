"""Batch Gmail operations."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from ..client import gmail_service
from ..schemas import BatchDeleteRequest, BatchModifyRequest


def register(server: FastMCP) -> None:
    @server.tool(name="batch_modify")
    async def batch_modify(request: BatchModifyRequest, ctx: Context) -> dict[str, Any]:
        """Apply label modifications to multiple messages in a single API call."""
        service = gmail_service()
        total = max(len(request.message_ids), 1)
        await ctx.info(f"Batch modifying {len(request.message_ids)} messages.")
        for index, _ in enumerate(request.message_ids, start=1):
            await ctx.report_progress(index, total, f"Preparing message {index}/{total}")

        service.users().messages().batchModify(
            userId="me",
            body={
                "ids": request.message_ids,
                "addLabelIds": request.add_label_ids,
                "removeLabelIds": request.remove_label_ids,
            },
        ).execute()
        await ctx.report_progress(total, total, "Batch modify completed")
        return {"status": "ok", "processed": len(request.message_ids)}

    @server.tool(name="batch_delete")
    async def batch_delete(request: BatchDeleteRequest, ctx: Context) -> dict[str, Any]:
        """Trash or permanently delete multiple messages."""
        service = gmail_service()
        if request.permanent:
            response = await ctx.elicit(
                f"Permanently delete {len(request.message_ids)} messages?",
                response_type=bool,  # type: ignore[arg-type]
            )
            if response.action != "accept" or not bool(response.data):
                return {"status": "cancelled"}
            service.users().messages().batchDelete(
                userId="me", body={"ids": request.message_ids}
            ).execute()
            return {"status": "ok", "mode": "permanent", "processed": len(request.message_ids)}

        total = max(len(request.message_ids), 1)
        for index, message_id in enumerate(request.message_ids, start=1):
            service.users().messages().trash(userId="me", id=message_id).execute()
            await ctx.report_progress(index, total, f"Moved {index}/{total} to trash")
        return {"status": "ok", "mode": "trash", "processed": len(request.message_ids)}
