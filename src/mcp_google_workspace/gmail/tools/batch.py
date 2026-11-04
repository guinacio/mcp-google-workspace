"""Batch Gmail operations."""

from __future__ import annotations

import logging

from typing import Annotated, Any

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request
from ...common.confirmation import confirm_destructive_action
from ..client import gmail_service
from ..schemas import BatchDeleteRequest, BatchModifyRequest

LOGGER = logging.getLogger(__name__)


def register(server: FastMCP) -> None:
    @server.tool(name="batch_modify")
    async def batch_modify(
        message_ids: list[str],
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Change labels on many Gmail messages in one call, e.g. archive them (remove INBOX), mark them read (remove UNREAD) or unread, star them or file them under a user label. Use gmail_apply_labels or gmail_move_email for one message and gmail_modify_thread for every message of one conversation; at least one label list is required. Returns status 'ok' and processed, the number of IDs sent, with no per-message result. A call with 10 or more message IDs must first go through prepare_workspace_action and commit_workspace_action."""
        request = BatchModifyRequest(
            message_ids=message_ids,
            add_label_ids=add_label_ids or [],
            remove_label_ids=remove_label_ids or [],
        )
        if not request.add_label_ids and not request.remove_label_ids:
            raise ValueError("At least one of add_label_ids/remove_label_ids must be provided.")
        service = gmail_service()
        total = max(len(request.message_ids), 1)
        LOGGER.debug(f"Batch modifying {len(request.message_ids)} messages.")
        for index, _ in enumerate(request.message_ids, start=1):
            if ctx is not None:
                await ctx.report_progress(index, total, f"Preparing message {index}/{total}")

        await execute_google_request(
            service.users().messages().batchModify(
                userId="me",
                body={
                    "ids": request.message_ids,
                    "addLabelIds": request.add_label_ids,
                    "removeLabelIds": request.remove_label_ids,
                },
            )
        )
        if ctx is not None:
            await ctx.report_progress(total, total, "Batch modify completed")
        return {"status": "ok", "processed": len(request.message_ids)}

    @server.tool(name="batch_delete")
    async def batch_delete(
        message_ids: list[str],
        permanent: Annotated[
            bool,
            (
                "When true, delete all the messages for good in one batch instead of moving them to Trash; "
                "the user is asked to confirm first, and a client that cannot ask makes the call fail without "
                "deleting. Default false moves each one to Trash, reversible with gmail_untrash_email."
            ),
        ] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Move many Gmail messages to Trash, or with permanent=true delete them all for good. For one message use gmail_delete_email and for a whole conversation gmail_trash_thread or gmail_delete_thread. Returns status 'ok', mode ('trash' or 'permanent') and processed (the number of IDs), or status 'cancelled' if the user declines. Trashing goes one message at a time, so a failure part-way leaves the earlier ones in Trash; permanent deletion asks the user to confirm and cannot be undone."""
        request = BatchDeleteRequest(message_ids=message_ids, permanent=permanent)
        service = gmail_service()
        if request.permanent:
            if not await confirm_destructive_action(
                ctx,
                "batch_delete",
                f"Permanently delete {len(request.message_ids)} messages?",
            ):
                return {"status": "cancelled"}
            await execute_google_request(
                service.users().messages().batchDelete(userId="me", body={"ids": request.message_ids})
            )
            return {"status": "ok", "mode": "permanent", "processed": len(request.message_ids)}

        total = max(len(request.message_ids), 1)
        for index, message_id in enumerate(request.message_ids, start=1):
            await execute_google_request(service.users().messages().trash(userId="me", id=message_id))
            if ctx is not None:
                await ctx.report_progress(index, total, f"Moved {index}/{total} to trash")
        return {"status": "ok", "mode": "trash", "processed": len(request.message_ids)}
