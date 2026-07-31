"""FastMCP Docs tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import run_blocking
from ..common.errors import tool_error_payload
from .client import docs_service
from .schemas import (
    AppendDocumentTextRequest,
    BatchUpdateDocumentRequest,
    CreateDocumentRequest,
    GetDocumentRequest,
    ReplaceDocumentTextRequest,
)


def build_append_text_requests(text: str) -> list[dict[str, Any]]:
    return [{"insertText": {"endOfSegmentLocation": {}, "text": text}}]


def build_replace_text_requests(contains_text: str, replace_text: str, *, match_case: bool = False) -> list[dict[str, Any]]:
    return [
        {
            "replaceAllText": {
                "containsText": {"text": contains_text, "matchCase": match_case},
                "replaceText": replace_text,
            }
        }
    ]


def get_document_payload(request: GetDocumentRequest) -> dict[str, Any]:
    service = docs_service()
    return service.documents().get(
        documentId=request.document_id,
        includeTabsContent=request.include_tabs_content,
        suggestionsViewMode=request.suggestions_view_mode,
    ).execute()


def create_document_payload(request: CreateDocumentRequest) -> dict[str, Any]:
    service = docs_service()
    return service.documents().create(body={"title": request.title}).execute()


def append_document_text_payload(request: AppendDocumentTextRequest) -> dict[str, Any]:
    service = docs_service()
    return service.documents().batchUpdate(
        documentId=request.document_id,
        body={"requests": build_append_text_requests(request.text)},
    ).execute()


def replace_document_text_payload(request: ReplaceDocumentTextRequest) -> dict[str, Any]:
    service = docs_service()
    return service.documents().batchUpdate(
        documentId=request.document_id,
        body={
            "requests": build_replace_text_requests(
                request.contains_text,
                request.replace_text,
                match_case=request.match_case,
            )
        },
    ).execute()


def batch_update_document_payload(request: BatchUpdateDocumentRequest) -> dict[str, Any]:
    service = docs_service()
    body: dict[str, Any] = {"requests": request.requests}
    if request.write_control is not None:
        body["writeControl"] = request.write_control
    return service.documents().batchUpdate(documentId=request.document_id, body=body).execute()


def register_tools(server: FastMCP) -> None:
    @server.tool(name="get_document")
    def get_document(
        document_id: str,
        include_tabs_content: bool = False,
        suggestions_view_mode: Annotated[
            Literal[
                "DEFAULT_FOR_CURRENT_ACCESS",
                "SUGGESTIONS_INLINE",
                "PREVIEW_SUGGESTIONS_ACCEPTED",
                "PREVIEW_WITHOUT_SUGGESTIONS",
            ]
            | None,
            "How pending suggestions are rendered in the returned document body.",
        ] = None,
    ) -> dict[str, Any]:
        """Fetch a document's full structured body (paragraphs, tables, styles).

        document_id is the doc's Drive file ID — there is no Docs-side lookup
        tool, so obtain it via drive_list_files or search_workspace first. Set
        include_tabs_content=true to include all tabs. Returns the Document
        resource, or {"error": ...} on API failure.
        """
        try:
            return get_document_payload(
                GetDocumentRequest(
                    document_id=document_id,
                    include_tabs_content=include_tabs_content,
                    suggestions_view_mode=suggestions_view_mode,
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, document_id=document_id)

    @server.tool(name="create_document")
    def create_document(title: str) -> dict[str, Any]:
        """Create a new empty document with the given title.

        Returns the created Document resource (including its 'documentId' for
        the other docs tools), or {"error": ...} on API failure.
        """
        try:
            return create_document_payload(CreateDocumentRequest(title=title))
        except HttpError as exc:
            return tool_error_payload(exc, title=title)

    @server.tool(name="append_document_text")
    def append_document_text(
        document_id: str,
        text: Annotated[str, "Text appended to the end of the document."],
    ) -> dict[str, Any]:
        """Append plain text to the end of a document.

        document_id comes from create_document or Drive discovery
        (drive_list_files / search_workspace). Returns the batchUpdate reply,
        or {"error": ...} on API failure.
        """
        try:
            return append_document_text_payload(AppendDocumentTextRequest(document_id=document_id, text=text))
        except HttpError as exc:
            return tool_error_payload(exc, document_id=document_id)

    @server.tool(name="replace_document_text")
    def replace_document_text(
        document_id: str,
        contains_text: str,
        replace_text: str,
        match_case: bool = False,
    ) -> dict[str, Any]:
        """Replace every occurrence of contains_text in a document with replace_text.

        document_id comes from create_document or Drive discovery
        (drive_list_files / search_workspace). Returns the batchUpdate reply
        (including occurrencesChanged), or {"error": ...} on API failure.
        """
        try:
            return replace_document_text_payload(
                ReplaceDocumentTextRequest(
                    document_id=document_id,
                    contains_text=contains_text,
                    replace_text=replace_text,
                    match_case=match_case,
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, document_id=document_id)

    @server.tool(name="batch_update_document", task=True)
    async def batch_update_document(
        document_id: str,
        requests: Annotated[
            list[dict[str, Any]],
            (
                "Raw Docs API batchUpdate request objects, e.g. "
                '[{"insertText": {"location": {"index": 1}, "text": "Hello"}}] or '
                "replaceAllText/deleteContentRange/updateTextStyle requests."
            ),
        ],
        write_control: Annotated[
            dict[str, Any] | None,
            "Optional Docs writeControl object (e.g. requiredRevisionId) guarding against concurrent edits.",
        ] = None,
    ) -> dict[str, Any]:
        """Apply raw Docs API batchUpdate requests for edits beyond append/replace.

        document_id comes from create_document or Drive discovery
        (drive_list_files / search_workspace). Returns the batchUpdate replies,
        or {"error": ...} on API failure (e.g. a stale requiredRevisionId).
        """
        try:
            return await run_blocking(
                batch_update_document_payload,
                BatchUpdateDocumentRequest(
                    document_id=document_id,
                    requests=requests,
                    write_control=write_control,
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, document_id=document_id)
