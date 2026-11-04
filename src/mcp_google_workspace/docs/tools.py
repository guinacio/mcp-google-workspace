"""FastMCP Docs tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import run_blocking
from ..common.errors import provider_tool_error
from .client import docs_service
from .presentation import (
    batch_update_result,
    created_document,
    document_text_view,
    raw_document_view,
    replace_text_result,
    text_update_result,
)
from .schemas import (
    AppendDocumentTextRequest,
    BatchUpdateDocumentRequest,
    CreateDocumentRequest,
    ReadDocumentRequest,
    ReplaceDocumentTextRequest,
    SuggestionsViewMode,
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


def read_document_payload(request: ReadDocumentRequest) -> dict[str, Any]:
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
    @server.tool(name="read_document")
    def read_document(
        document_id: Annotated[
            str,
            (
                "Google Docs document ID: the Drive file ID, also the long ID in the document's URL, "
                "from drive_list_files, search_workspace or docs_create_document."
            ),
        ],
        format: Annotated[
            Literal["text", "markdown", "raw"],
            (
                "Output form: 'text' returns clean readable text; 'markdown' the same content as Markdown "
                "(headings, lists, pipe tables, links, footnotes); both come in pages of about 8,000 "
                "characters. 'raw' returns the editing outline, unpaged: every structural element with "
                "the start_index and end_index docs_batch_update_document requests need, styles left out."
            ),
        ] = "text",
        offset: Annotated[
            int,
            (
                "Character offset into the rendered text to resume from: the next_offset of a truncated "
                "'text' or 'markdown' result, with the same format; 0 starts at the beginning and 'raw' "
                "ignores it."
            ),
        ] = 0,
        include_tabs_content: Annotated[
            bool,
            (
                "Read every tab, child tabs included, in every format (the default). When false Google "
                "returns only the first tab, so tabs then holds one entry whose tab_id and title are null."
            ),
        ] = True,
        suggestions_view_mode: Annotated[
            SuggestionsViewMode | None,
            (
                "How pending suggested edits appear, in every format: SUGGESTIONS_INLINE marks them "
                "inline (text and markdown leave suggested insertions out), PREVIEW_SUGGESTIONS_ACCEPTED "
                "shows the text as if all were accepted, PREVIEW_WITHOUT_SUGGESTIONS as if all were "
                "rejected. Omitted or DEFAULT_FOR_CURRENT_ACCESS: inline for editors, without "
                "suggestions for viewers."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Read a Google Docs document, every tab in order, as clean text or Markdown to read or summarize it, or with format 'raw' as the editing outline whose indices docs_batch_update_document needs (docs_append_document_text and docs_replace_document_text need none). Text and markdown return document_id, title, revision_id, url, format, tabs (tab_id, title) and one page of body with truncated, next_offset, body_chars and body_sha256; continue with offset=next_offset until it is null. Raw returns the same identity, suggestions_view_mode and tabs with content (kind, start_index, end_index, text with one U+FFFC per image or chip, style, list, inline), headers, footers, footnotes and named_ranges."""
        request = ReadDocumentRequest(
            document_id=document_id,
            format=format,
            offset=offset,
            include_tabs_content=include_tabs_content,
            suggestions_view_mode=suggestions_view_mode,
        )
        try:
            payload = read_document_payload(request)
        except HttpError as exc:
            raise provider_tool_error(exc, document_id=document_id) from exc
        if request.format == "raw":
            return raw_document_view(payload)
        return document_text_view(payload, format=request.format, offset=request.offset)

    @server.tool(name="create_document")
    def create_document(
        title: Annotated[str, "Title of the new document, also used as its Drive file name."],
    ) -> dict[str, Any]:
        """Create a new, empty Google Docs document in the root of the user's My Drive. Add text afterwards with docs_append_document_text, or copy an existing document with drive_copy_file instead. Returns status 'ok', document_id (the ID every other docs_ tool takes), title, revision_id and url."""
        try:
            payload = create_document_payload(CreateDocumentRequest(title=title))
        except HttpError as exc:
            raise provider_tool_error(exc, title=title) from exc
        return created_document(payload)

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
        """Append plain, unformatted text to the end of a Google Docs document. The document_id comes from drive_list_files, search_workspace or docs_create_document; to change existing text use docs_replace_document_text, and for formatting or inserts at a position use docs_batch_update_document. Returns status 'ok', document_id and revision_id, the document's revision after the append."""
        try:
            payload = append_document_text_payload(AppendDocumentTextRequest(document_id=document_id, text=text))
        except HttpError as exc:
            raise provider_tool_error(exc, document_id=document_id) from exc
        return text_update_result(payload)

    @server.tool(name="replace_document_text")
    def replace_document_text(
        document_id: str,
        contains_text: str,
        replace_text: str,
        match_case: Annotated[
            bool, "Match letter case exactly when true; by default matching ignores case."
        ] = False,
    ) -> dict[str, Any]:
        """Replace every occurrence of a piece of text in a Google Docs document. The document_id comes from drive_list_files, search_workspace or docs_create_document; to add text use docs_append_document_text, and for formatting or targeted edits use docs_batch_update_document. Returns status 'ok', document_id, revision_id and occurrences_changed, which is 0 when nothing matched."""
        try:
            payload = replace_document_text_payload(
                ReplaceDocumentTextRequest(
                    document_id=document_id,
                    contains_text=contains_text,
                    replace_text=replace_text,
                    match_case=match_case,
                )
            )
        except HttpError as exc:
            raise provider_tool_error(exc, document_id=document_id) from exc
        return replace_text_result(payload)

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
                '{"requiredRevisionId": "<revision_id from docs_read_document>"}: the update fails if '
                "the document changed since that revision."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Apply raw Google Docs API batchUpdate requests for edits the simpler tools cannot make: formatting, tables, images, inserts at an index and deletions. For plain appends use docs_append_document_text, and for find-and-replace docs_replace_document_text. The requests are applied atomically: if one is invalid, none is applied. Returns status 'ok', document_id, revision_id (the revision after the update), replies (Google's reply objects, one per request, in order) and count."""
        try:
            payload = await run_blocking(
                batch_update_document_payload,
                BatchUpdateDocumentRequest(
                    document_id=document_id,
                    requests=requests,
                    write_control=write_control,
                )
            )
        except HttpError as exc:
            raise provider_tool_error(exc, document_id=document_id) from exc
        return batch_update_result(payload)
