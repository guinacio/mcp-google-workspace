"""FastMCP Slides tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import run_blocking
from ..common.errors import provider_tool_error
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
        """Read a Google Slides presentation's full structure: slides with their page elements and text, layouts and masters. The presentation_id comes from drive_list_files, search_workspace or slides_create_presentation; for one slide use slides_get_slide_page. Returns the Google Presentation resource: presentationId, title, pageSize and slides, where each slide's objectId is the page_object_id for slides_get_slide_page and slides_get_slide_thumbnail."""
        try:
            return get_presentation_payload(GetPresentationRequest(presentation_id=presentation_id))
        except HttpError as exc:
            raise provider_tool_error(exc, presentation_id=presentation_id) from exc

    @server.tool(name="create_presentation")
    def create_presentation(
        title: Annotated[str, "Title of the new presentation, also used as its Drive file name."],
    ) -> dict[str, Any]:
        """Create a new Google Slides presentation in the root of the user's My Drive. Add content with slides_batch_update_presentation, or copy an existing deck with drive_copy_file and fill its placeholders with slides_replace_text_in_presentation. Returns the created Google Presentation resource: presentationId (the ID every other slides_ tool takes) and slides with each slide's objectId."""
        try:
            return create_presentation_payload(CreatePresentationRequest(title=title))
        except HttpError as exc:
            raise provider_tool_error(exc, title=title) from exc

    @server.tool(name="replace_text_in_presentation")
    def replace_text_in_presentation(
        presentation_id: str,
        contains_text: str,
        replace_text: str,
        match_case: Annotated[
            bool, "Match letter case exactly when true; by default matching ignores case."
        ] = False,
    ) -> dict[str, Any]:
        """Replace every occurrence of a piece of text on all slides of a Google Slides presentation, e.g. a template placeholder such as {{name}}. The presentation_id comes from drive_list_files, search_workspace or slides_create_presentation; for any other edit use slides_batch_update_presentation. Returns the Slides batchUpdate response: presentationId and replies[0].replaceAllText.occurrencesChanged, which is 0 or missing when nothing matched."""
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
            raise provider_tool_error(exc, presentation_id=presentation_id) from exc

    @server.tool(name="get_slide_page")
    def get_slide_page(presentation_id: str, page_object_id: str) -> dict[str, Any]:
        """Read one slide of a Google Slides presentation with all its page elements: shapes, text, images and tables. For the whole deck use slides_get_presentation (which also supplies the presentation and slide IDs), and for a picture of the slide use slides_get_slide_thumbnail. Returns the Google Page resource: objectId, pageType, pageElements (each with its objectId, size, transform and content) and slideProperties."""
        try:
            return get_slide_page_payload(
                GetSlidePageRequest(presentation_id=presentation_id, page_object_id=page_object_id)
            )
        except HttpError as exc:
            raise provider_tool_error(exc, presentation_id=presentation_id, page_object_id=page_object_id) from exc

    @server.tool(name="get_slide_thumbnail")
    def get_slide_thumbnail(
        presentation_id: str,
        page_object_id: str,
        mime_type: Annotated[Literal["PNG", "JPEG"], "Image format of the rendered thumbnail."] = "PNG",
        thumbnail_size: Annotated[
            Literal["THUMBNAIL_SIZE_UNSPECIFIED", "LARGE", "MEDIUM", "SMALL"],
            (
                "Image width: LARGE 1600 px, MEDIUM 800 px, SMALL 200 px; THUMBNAIL_SIZE_UNSPECIFIED "
                "lets Google choose. The height follows the slide's aspect ratio."
            ),
        ] = "LARGE",
    ) -> dict[str, Any]:
        """Render one slide of a Google Slides presentation as an image and return a link to it. Use slides_get_slide_page instead to read the slide's text and elements; the presentation and slide IDs come from slides_get_presentation. Returns the Google Thumbnail: contentUrl, a temporary image URL (valid for about 30 minutes), plus width and height in pixels."""
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
            raise provider_tool_error(exc, presentation_id=presentation_id, page_object_id=page_object_id) from exc

    @server.tool(name="batch_update_presentation", task=True)
    async def batch_update_presentation(
        presentation_id: str,
        requests: Annotated[
            list[dict[str, Any]],
            (
                "Slides API batchUpdate request objects, applied in order, e.g. "
                '[{"createSlide": {"insertionIndex": 1}}] or createShape/insertText/'
                "deleteObject/updatePageElementTransform requests."
            ),
        ],
    ) -> dict[str, Any]:
        """Apply raw Google Slides API batchUpdate requests for edits beyond text replacement: creating or deleting slides, adding shapes, images and tables, inserting or styling text. To replace existing or placeholder text across the deck use slides_replace_text_in_presentation instead. Requests apply atomically: if one is invalid, none is applied. Returns the batchUpdate response: presentationId and replies, one per request in order (e.g. createSlide.objectId for a new slide)."""
        try:
            return await run_blocking(
                batch_update_presentation_payload,
                BatchUpdatePresentationRequest(presentation_id=presentation_id, requests=requests)
            )
        except HttpError as exc:
            raise provider_tool_error(exc, presentation_id=presentation_id) from exc
