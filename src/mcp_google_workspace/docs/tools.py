"""FastMCP Docs tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import run_blocking
from ..common.errors import provider_tool_error
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
        include_tabs_content: Annotated[
            bool,
            (
                "Return every tab's content in the tabs list instead of filling body with the first "
                "tab only; set it for documents with several tabs."
            ),
        ] = False,
        suggestions_view_mode: Annotated[
            Literal[
                "DEFAULT_FOR_CURRENT_ACCESS",
                "SUGGESTIONS_INLINE",
                "PREVIEW_SUGGESTIONS_ACCEPTED",
                "PREVIEW_WITHOUT_SUGGESTIONS",
            ]
            | None,
            (
                "How pending suggested edits appear: SUGGESTIONS_INLINE marks them inline, "
                "PREVIEW_SUGGESTIONS_ACCEPTED shows the text as if all were accepted, "
                "PREVIEW_WITHOUT_SUGGESTIONS as if all were rejected. Omitted or "
                "DEFAULT_FOR_CURRENT_ACCESS: inline for editors, without suggestions for viewers."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Read a Google Docs document's full structured content: paragraphs, tables, lists and styles. The document_id comes from drive_list_files, search_workspace or docs_create_document; to change the text use docs_append_document_text or docs_replace_document_text. Returns the Google Document resource: documentId, title, revisionId and body.content (with include_tabs_content, the content is under tabs instead)."""
        try:
            return get_document_payload(
                GetDocumentRequest(
                    document_id=document_id,
                    include_tabs_content=include_tabs_content,
                    suggestions_view_mode=suggestions_view_mode,
                )
            )
        except HttpError as exc:
            raise provider_tool_error(exc, document_id=document_id) from exc

    @server.tool(name="create_document")
    def create_document(
        title: Annotated[str, "Title of the new document, also used as its Drive file name."],
    ) -> dict[str, Any]:
        """Create a new, empty Google Docs document in the root of the user's My Drive. Add text afterwards with docs_append_document_text, or copy an existing document with drive_copy_file instead. Returns the created Google Document resource; its documentId is the ID every other docs_ tool takes."""
        try:
            return create_document_payload(CreateDocumentRequest(title=title))
        except HttpError as exc:
            raise provider_tool_error(exc, title=title) from exc

    @server.tool(name="append_document_text")
    def append_document_text(
        document_id: str,
        text: Annotated[
            str,
            (
                "Plain text inserted at the end of the body exactly as given; no newline is added, so "
                "start it with a newline to begin a new paragraph."
            ),
        ],
    ) -> dict[str, Any]:
        """Append plain, unformatted text to the end of a Google Docs document. The document_id comes from drive_list_files, search_workspace or docs_create_document; to change existing text use docs_replace_document_text, and for formatting or inserts at a position use docs_batch_update_document. Returns the Docs batchUpdate response: documentId, writeControl.requiredRevisionId and one empty entry in replies."""
        try:
            return append_document_text_payload(AppendDocumentTextRequest(document_id=document_id, text=text))
        except HttpError as exc:
            raise provider_tool_error(exc, document_id=document_id) from exc

    @server.tool(name="replace_document_text")
    def replace_document_text(
        document_id: str,
        contains_text: str,
        replace_text: str,
        match_case: Annotated[
            bool, "Match letter case exactly when true; by default matching ignores case."
        ] = False,
    ) -> dict[str, Any]:
        """Replace every occurrence of a piece of text in a Google Docs document. The document_id comes from drive_list_files, search_workspace or docs_create_document; to add text use docs_append_document_text, and for formatting or targeted edits use docs_batch_update_document. Returns the Docs batchUpdate response: documentId and replies[0].replaceAllText.occurrencesChanged, which is 0 or missing when nothing matched."""
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
            raise provider_tool_error(exc, document_id=document_id) from exc

    @server.tool(name="batch_update_document", task=True)
    async def batch_update_document(
        document_id: str,
        requests: Annotated[
            list[dict[str, Any]],
            (
                "Docs API batchUpdate request objects, applied in order, e.g. "
                '[{"insertText": {"location": {"index": 1}, "text": "Hello"}}] or '
                "replaceAllText/deleteContentRange/updateTextStyle requests."
            ),
        ],
        write_control: Annotated[
            dict[str, Any] | None,
            (
                "Docs writeControl object that guards against concurrent edits, e.g. "
                '{"requiredRevisionId": "<revisionId from docs_get_document>"}: the update fails if '
                "the document changed since that revision."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Apply raw Google Docs API batchUpdate requests for edits the simpler tools cannot make: formatting, tables, images, inserts at an index and deletions. For plain appends use docs_append_document_text, and for find-and-replace docs_replace_document_text. The requests are applied atomically: if one is invalid, none is applied. Returns the batchUpdate response: documentId, replies (one per request, in order) and writeControl.requiredRevisionId."""
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
            raise provider_tool_error(exc, document_id=document_id) from exc
