"""Attachment-focused Gmail tools."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from fastmcp import Context, FastMCP

from ..client import gmail_service
from ..mime_utils import flatten_parts
from ..schemas import DownloadAttachmentRequest, ListAttachmentsRequest


def register(server: FastMCP) -> None:
    @server.tool(name="list_attachments")
    async def list_attachments(request: ListAttachmentsRequest, ctx: Context) -> dict[str, Any]:
        """List attachment metadata for a given Gmail message."""
        service = gmail_service()
        await ctx.info(f"Reading attachment metadata for {request.message_id}.")
        message = (
            service.users()
            .messages()
            .get(userId="me", id=request.message_id, format="full")
            .execute()
        )
        payload = message.get("payload", {})
        attachments: list[dict[str, Any]] = []
        for part in flatten_parts(payload):
            filename = part.get("filename")
            body = part.get("body", {})
            attachment_id = body.get("attachmentId")
            if filename and attachment_id:
                attachments.append(
                    {
                        "filename": filename,
                        "mime_type": part.get("mimeType"),
                        "size": body.get("size", 0),
                        "download_id": attachment_id,
                    }
                )
        return {"message_id": request.message_id, "attachments": attachments}

    @server.tool(name="download_attachment")
    async def download_attachment(request: DownloadAttachmentRequest, ctx: Context) -> dict[str, Any]:
        """Download one Gmail attachment and save it to the local filesystem path."""
        service = gmail_service()
        await ctx.info(f"Downloading attachment {request.attachment_id}.")
        response = (
            service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=request.message_id, id=request.attachment_id)
            .execute()
        )
        data = response.get("data")
        if not data:
            raise ValueError("Attachment has no binary content in payload.")

        output = Path(request.output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        blob = base64.urlsafe_b64decode(data.encode("utf-8"))
        size = len(blob)
        await ctx.report_progress(25, 100, "Attachment downloaded from API")
        output.write_bytes(blob)
        await ctx.report_progress(100, 100, "Attachment saved to filesystem")
        return {"status": "ok", "saved_to": str(output), "bytes_written": size}
