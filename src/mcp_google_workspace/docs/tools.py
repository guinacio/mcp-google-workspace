"""FastMCP Docs tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

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
    async def get_document(
        document_id: str,
        include_tabs_content: bool = False,
        suggestions_view_mode: str | None = None,
    ) -> dict[str, Any]:
        return get_document_payload(
            GetDocumentRequest(
                document_id=document_id,
                include_tabs_content=include_tabs_content,
                suggestions_view_mode=suggestions_view_mode,
            )
        )

    @server.tool(name="create_document")
    async def create_document(title: str) -> dict[str, Any]:
        return create_document_payload(CreateDocumentRequest(title=title))

    @server.tool(name="append_document_text")
    async def append_document_text(document_id: str, text: str) -> dict[str, Any]:
        return append_document_text_payload(AppendDocumentTextRequest(document_id=document_id, text=text))

    @server.tool(name="replace_document_text")
    async def replace_document_text(
        document_id: str,
        contains_text: str,
        replace_text: str,
        match_case: bool = False,
    ) -> dict[str, Any]:
        return replace_document_text_payload(
            ReplaceDocumentTextRequest(
                document_id=document_id,
                contains_text=contains_text,
                replace_text=replace_text,
                match_case=match_case,
            )
        )

    @server.tool(name="batch_update_document")
    async def batch_update_document(
        document_id: str,
        requests: list[dict[str, Any]],
        write_control: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return batch_update_document_payload(
            BatchUpdateDocumentRequest(
                document_id=document_id,
                requests=requests,
                write_control=write_control,
            )
        )
