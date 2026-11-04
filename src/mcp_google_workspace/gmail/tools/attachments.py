"""Attachment-focused Gmail tools."""

from __future__ import annotations

import logging

import base64
from pathlib import Path
from typing import Annotated, Any

from fastmcp import Context, FastMCP

from ...common.async_ops import execute_google_request, run_blocking, write_bytes_file
from ...file_uploads import require_local_filesystem
from ..client import gmail_service
from ..mime_utils import flatten_parts
from ..schemas import DownloadAttachmentRequest, ListAttachmentsRequest

LOGGER = logging.getLogger(__name__)


def register(server: FastMCP) -> None:
    @server.tool(name="list_attachments")
    async def list_attachments(
        message_id: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List the attachments of one Gmail message without downloading them. Use it to get the download_id that gmail_download_attachment needs; gmail_read_emails lists attachments too, but without download IDs. Returns message_id and attachments, each with filename, mime_type, size in bytes and download_id."""
        request = ListAttachmentsRequest(message_id=message_id)
        service = gmail_service()
        LOGGER.debug(f"Reading attachment metadata for {request.message_id}.")
        message = await execute_google_request(
            service.users()
            .messages()
            .get(userId="me", id=request.message_id, format="full")
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

    @server.tool(name="download_attachment", task=True)
    async def download_attachment(
        message_id: str,
        attachment_id: Annotated[
            str, "The attachment's download_id from gmail_list_attachments for the same message_id."
        ],
        output_path: Annotated[
            str,
            "File path on the server's machine to write, e.g. '/tmp/invoice.pdf'; missing folders are created and an existing file is overwritten.",
        ],
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Download one attachment of a Gmail message and save it to a file on the server's own filesystem. It works only when the server runs locally (stdio) and is refused over remote MCP; get the attachment ID from gmail_list_attachments first. Returns status 'ok', saved_to (the path written) and bytes_written. Creates missing parent folders and overwrites an existing file at output_path."""
        request = DownloadAttachmentRequest(
            message_id=message_id,
            attachment_id=attachment_id,
            output_path=output_path,
        )
        require_local_filesystem("Gmail attachment download")
        service = gmail_service()
        LOGGER.debug(f"Downloading attachment {request.attachment_id}.")
        response = await execute_google_request(
            service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=request.message_id, id=request.attachment_id)
        )
        data = response.get("data")
        if not data:
            raise ValueError("Attachment has no binary content in payload.")

        output = Path(request.output_path)
        await run_blocking(output.parent.mkdir, parents=True, exist_ok=True)
        blob = base64.urlsafe_b64decode(data.encode("utf-8"))
        size = len(blob)
        if ctx is not None:
            await ctx.report_progress(25, 100, "Attachment downloaded from API")
        await write_bytes_file(output, blob)
        if ctx is not None:
            await ctx.report_progress(100, 100, "Attachment saved to filesystem")
        return {"status": "ok", "saved_to": str(output), "bytes_written": size}
