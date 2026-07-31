"""FastMCP Slides tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import run_blocking
from ..common.errors import tool_error_payload
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
    def get_presentation(presentation_id: str) -> dict[str, Any]:
        """Fetch a presentation's full structure: slides, layouts, masters, and elements.

        presentation_id is the file's Drive ID — there is no Slides-side lookup
        tool, so obtain it via drive_list_files or search_workspace first.
        Returns the Presentation resource (each slide's 'objectId' feeds
        get_slide_page / get_slide_thumbnail), or {"error": ...} on API
        failure.
        """
        try:
            return get_presentation_payload(GetPresentationRequest(presentation_id=presentation_id))
        except HttpError as exc:
            return tool_error_payload(exc, presentation_id=presentation_id)

    @server.tool(name="create_presentation")
    def create_presentation(title: str) -> dict[str, Any]:
        """Create a new presentation with a single default title slide.

        Returns the created Presentation resource (including its
        'presentationId' for the other slides tools), or {"error": ...} on API
        failure.
        """
        try:
            return create_presentation_payload(CreatePresentationRequest(title=title))
        except HttpError as exc:
            return tool_error_payload(exc, title=title)

    @server.tool(name="replace_text_in_presentation")
    def replace_text_in_presentation(
        presentation_id: str,
        contains_text: str,
        replace_text: str,
        match_case: bool = False,
    ) -> dict[str, Any]:
        """Replace every occurrence of contains_text across all slides with replace_text.

        presentation_id comes from create_presentation or Drive discovery
        (drive_list_files / search_workspace). Returns the batchUpdate reply
        (including occurrencesChanged), or {"error": ...} on API failure.
        """
        try:
            return replace_text_in_presentation_payload(
                ReplaceTextInPresentationRequest(
                    presentation_id=presentation_id,
                    contains_text=contains_text,
                    replace_text=replace_text,
                    match_case=match_case,
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, presentation_id=presentation_id)

    @server.tool(name="get_slide_page")
    def get_slide_page(presentation_id: str, page_object_id: str) -> dict[str, Any]:
        """Fetch one slide (page) with its elements, by the slide's objectId.

        page_object_id comes from get_presentation's slides list. Returns the
        Page resource, or {"error": ...} on API failure.
        """
        try:
            return get_slide_page_payload(
                GetSlidePageRequest(presentation_id=presentation_id, page_object_id=page_object_id)
            )
        except HttpError as exc:
            return tool_error_payload(exc, presentation_id=presentation_id, page_object_id=page_object_id)

    @server.tool(name="get_slide_thumbnail")
    def get_slide_thumbnail(
        presentation_id: str,
        page_object_id: str,
        mime_type: Literal["PNG", "JPEG"] = "PNG",
        thumbnail_size: Annotated[
            Literal["THUMBNAIL_SIZE_UNSPECIFIED", "LARGE", "MEDIUM", "SMALL"],
            "Requested thumbnail image resolution, from largest to smallest.",
        ] = "LARGE",
    ) -> dict[str, Any]:
        """Generate a thumbnail image of one slide and return its download URL.

        page_object_id comes from get_presentation's slides list. Returns the
        Thumbnail payload ('contentUrl' plus pixel dimensions; the URL is
        short-lived), or {"error": ...} on API failure.
        """
        try:
            return get_slide_thumbnail_payload(
                GetSlideThumbnailRequest(
                    presentation_id=presentation_id,
                    page_object_id=page_object_id,
                    mime_type=mime_type,
                    thumbnail_size=thumbnail_size,
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, presentation_id=presentation_id, page_object_id=page_object_id)

    @server.tool(name="batch_update_presentation", task=True)
    async def batch_update_presentation(
        presentation_id: str,
        requests: Annotated[
            list[dict[str, Any]],
            (
                "Raw Slides API batchUpdate request objects, e.g. "
                '[{"createSlide": {"insertionIndex": 1}}] or createShape/insertText/'
                "deleteObject/updatePageElementTransform requests."
            ),
        ],
    ) -> dict[str, Any]:
        """Apply raw Slides API batchUpdate requests (create slides, insert text/shapes, etc.).

        presentation_id comes from create_presentation or Drive discovery
        (drive_list_files / search_workspace). Returns the batchUpdate replies,
        or {"error": ...} on API failure.
        """
        try:
            return await run_blocking(
                batch_update_presentation_payload,
                BatchUpdatePresentationRequest(presentation_id=presentation_id, requests=requests)
            )
        except HttpError as exc:
            return tool_error_payload(exc, presentation_id=presentation_id)
