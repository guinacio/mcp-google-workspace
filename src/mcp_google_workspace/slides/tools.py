"""FastMCP Slides tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from .client import slides_service
from .schemas import (
    BatchUpdatePresentationRequest,
    CreatePresentationRequest,
    GetPresentationRequest,
    GetSlidePageRequest,
    GetSlideThumbnailRequest,
    ReplaceTextInPresentationRequest,
)


def build_replace_all_text_request(contains_text: str, replace_text: str, *, match_case: bool = False) -> dict[str, Any]:
    return {
        "replaceAllText": {
            "containsText": {"text": contains_text, "matchCase": match_case},
            "replaceText": replace_text,
        }
    }


def get_presentation_payload(request: GetPresentationRequest) -> dict[str, Any]:
    service = slides_service()
    return service.presentations().get(presentationId=request.presentation_id).execute()


def create_presentation_payload(request: CreatePresentationRequest) -> dict[str, Any]:
    service = slides_service()
    return service.presentations().create(body={"title": request.title}).execute()


def replace_text_in_presentation_payload(request: ReplaceTextInPresentationRequest) -> dict[str, Any]:
    service = slides_service()
    return service.presentations().batchUpdate(
        presentationId=request.presentation_id,
        body={
            "requests": [
                build_replace_all_text_request(
                    request.contains_text,
                    request.replace_text,
                    match_case=request.match_case,
                )
            ]
        },
    ).execute()


def get_slide_page_payload(request: GetSlidePageRequest) -> dict[str, Any]:
    service = slides_service()
    return service.presentations().pages().get(
        presentationId=request.presentation_id,
        pageObjectId=request.page_object_id,
    ).execute()


def get_slide_thumbnail_payload(request: GetSlideThumbnailRequest) -> dict[str, Any]:
    service = slides_service()
    return service.presentations().pages().getThumbnail(
        presentationId=request.presentation_id,
        pageObjectId=request.page_object_id,
        **{
            "thumbnailProperties.mimeType": request.mime_type,
            "thumbnailProperties.thumbnailSize": request.thumbnail_size,
        },
    ).execute()


def batch_update_presentation_payload(request: BatchUpdatePresentationRequest) -> dict[str, Any]:
    service = slides_service()
    return service.presentations().batchUpdate(
        presentationId=request.presentation_id,
        body={"requests": request.requests},
    ).execute()


def register_tools(server: FastMCP) -> None:
    @server.tool(name="get_presentation")
    async def get_presentation(presentation_id: str) -> dict[str, Any]:
        return get_presentation_payload(GetPresentationRequest(presentation_id=presentation_id))

    @server.tool(name="create_presentation")
    async def create_presentation(title: str) -> dict[str, Any]:
        return create_presentation_payload(CreatePresentationRequest(title=title))

    @server.tool(name="replace_text_in_presentation")
    async def replace_text_in_presentation(
        presentation_id: str,
        contains_text: str,
        replace_text: str,
        match_case: bool = False,
    ) -> dict[str, Any]:
        return replace_text_in_presentation_payload(
            ReplaceTextInPresentationRequest(
                presentation_id=presentation_id,
                contains_text=contains_text,
                replace_text=replace_text,
                match_case=match_case,
            )
        )

    @server.tool(name="get_slide_page")
    async def get_slide_page(presentation_id: str, page_object_id: str) -> dict[str, Any]:
        return get_slide_page_payload(
            GetSlidePageRequest(presentation_id=presentation_id, page_object_id=page_object_id)
        )

    @server.tool(name="get_slide_thumbnail")
    async def get_slide_thumbnail(
        presentation_id: str,
        page_object_id: str,
        mime_type: str = "PNG",
        thumbnail_size: str = "LARGE",
    ) -> dict[str, Any]:
        return get_slide_thumbnail_payload(
            GetSlideThumbnailRequest(
                presentation_id=presentation_id,
                page_object_id=page_object_id,
                mime_type=mime_type,
                thumbnail_size=thumbnail_size,
            )
        )

    @server.tool(name="batch_update_presentation")
    async def batch_update_presentation(
        presentation_id: str,
        requests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return batch_update_presentation_payload(
            BatchUpdatePresentationRequest(presentation_id=presentation_id, requests=requests)
        )
