"""FastMCP Slides tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import run_blocking
from ..common.errors import provider_tool_error
from ..tool_tiers import require_extended_tier
from .client import slides_service
from .presentation import (
    batch_update_presentation_envelope,
    created_presentation_envelope,
    presentation_envelope,
    raw_pages_view,
    raw_presentation_view,
    replace_text_envelope,
    thumbnail_envelope,
)
from .schemas import (
    BatchUpdatePresentationRequest,
    CreatePresentationRequest,
    GetSlideThumbnailRequest,
    ReadPresentationRequest,
    ReplaceTextInPresentationRequest,
)


def build_replace_all_text_request(contains_text: str, replace_text: str, *, match_case: bool = False) -> dict[str, Any]:
    return {
        "replaceAllText": {
            "containsText": {"text": contains_text, "matchCase": match_case},
            "replaceText": replace_text,
        }
    }


def read_presentation_payload(request: ReadPresentationRequest) -> dict[str, Any]:
    service = slides_service()
    return service.presentations().get(presentationId=request.presentation_id).execute()


def read_pages_payload(request: ReadPresentationRequest) -> list[dict[str, Any]]:
    """presentations.pages.get for each requested slide, in the order given (duplicates once)."""
    service = slides_service()
    return [
        service.presentations().pages().get(presentationId=request.presentation_id, pageObjectId=page_id).execute()
        for page_id in dict.fromkeys(request.slide_ids or [])
    ]


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
    @server.tool(name="read_presentation")
    def read_presentation(
        presentation_id: str,
        slide_ids: Annotated[
            list[str] | None,
            (
                "Object IDs of slides to read in detail, from the object_id of a slide in an earlier "
                "result: only those slides come back, in the order given, each element with its size "
                "(width_pt, height_pt) and position (x_pt, y_pt of the top-left corner). Omitted, every "
                "slide is outlined without geometry. With format raw, Google's Page of each, in pages."
            ),
        ] = None,
        format: Annotated[
            Literal["normalized", "raw"],
            (
                "'normalized' (the default) returns the outline described above. 'raw' returns Google's "
                "Presentation resource unchanged in presentation (masters, layouts, notes master, "
                "transforms, styles and fills included), or with slide_ids Google's Page resources in "
                "pages, for slides_batch_update_presentation edits; it needs the extended tool tier and "
                "fails with invalid_input under MCP_TOOL_TIER=core."
            ),
        ] = "normalized",
        offset: Annotated[
            int,
            (
                "Raw only (normalized ignores it): the next_offset of a truncated raw result. Raw pages "
                "hold about 32,000 characters of JSON and are cut between whole slides, layouts and "
                "masters, in that order, the first page holding every other field; joining each list "
                "across pages gives Google's resource. 0 starts at the beginning."
            ),
        ] = 0,
    ) -> dict[str, Any]:
        """Read a Google Slides presentation as an outline: every slide with its title, text, speaker notes and page elements, or with slide_ids only those slides, their elements adding size and position for layout edits. The presentation_id comes from drive_list_files, search_workspace or slides_create_presentation; for a picture of a slide use slides_get_slide_thumbnail. Returns presentation_id, title, revision_id, url, page_size (width_pt, height_pt), count and slides, each with object_id (the page_object_id other slides_ tools take), index, layout, title, text (one shape per line), notes and elements (object_id, type, placeholder, text). Format raw returns Google's JSON instead, paged."""
        request = ReadPresentationRequest(
            presentation_id=presentation_id, slide_ids=slide_ids, format=format, offset=offset
        )
        if request.format == "raw":
            require_extended_tier("format", "raw", instead="Omit format for the slide outline.")
        try:
            if request.format == "raw" and request.slide_ids:
                pages = read_pages_payload(request)
                return raw_pages_view(presentation_id, pages, offset=request.offset)
            payload = read_presentation_payload(request)
        except HttpError as exc:
            raise provider_tool_error(exc, presentation_id=presentation_id) from exc
        if request.format == "raw":
            return raw_presentation_view(payload, offset=request.offset)
        return presentation_envelope(payload, slide_ids=slide_ids)

    @server.tool(name="create_presentation")
    def create_presentation(
        title: Annotated[str, "Title of the new presentation, also used as its Drive file name."],
    ) -> dict[str, Any]:
        """Create a new Google Slides presentation in the root of the user's My Drive. Add content with slides_batch_update_presentation (extended tool tier), or copy an existing deck with drive_copy_file and fill its placeholders with slides_replace_text_in_presentation. Returns status ok, presentation_id (the ID every other slides_ tool takes), title, url and slides, each with object_id and index."""
        try:
            return created_presentation_envelope(
                create_presentation_payload(CreatePresentationRequest(title=title))
            )
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
        """Replace every occurrence of a piece of text on all slides of a Google Slides presentation, e.g. a template placeholder such as {{name}}. The presentation_id comes from drive_list_files, search_workspace or slides_create_presentation; for any other edit use slides_batch_update_presentation (extended tool tier). Returns status ok, presentation_id and occurrences_changed, the number of replacements (0 when nothing matched)."""
        try:
            return replace_text_envelope(
                replace_text_in_presentation_payload(
                    ReplaceTextInPresentationRequest(
                        presentation_id=presentation_id,
                        contains_text=contains_text,
                        replace_text=replace_text,
                        match_case=match_case,
                    )
                ),
                presentation_id=presentation_id,
            )
        except HttpError as exc:
            raise provider_tool_error(exc, presentation_id=presentation_id) from exc

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
        """Render one slide of a Google Slides presentation as an image and return a link to it. Use slides_read_presentation instead to read the slide's text and elements; it also supplies the presentation and slide IDs. Returns content_url, a temporary image URL (valid for about 30 minutes), width and height in pixels, and mime_type."""
        try:
            return thumbnail_envelope(
                get_slide_thumbnail_payload(
                    GetSlideThumbnailRequest(
                        presentation_id=presentation_id,
                        page_object_id=page_object_id,
                        mime_type=mime_type,
                        thumbnail_size=thumbnail_size,
                    )
                ),
                mime_type=mime_type,
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
        dry_run: Annotated[
            bool,
            (
                "True returns the impact preview (status 'dry_run', preview with the request count by "
                "kind, consent_required, action) without sending anything to Google."
            ),
        ] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Apply raw Google Slides API batchUpdate requests for edits beyond text replacement: creating or deleting slides, adding shapes, images and tables, inserting or styling text. To replace existing or placeholder text across the deck use slides_replace_text_in_presentation instead. Ten or more requests, or any delete* request, ask the user to confirm first; dry_run previews without changing anything. Requests apply atomically: if one is invalid, none is applied. Returns status ok, presentation_id, revision_id (the revision after the update), replies (Google's, one per request in order, e.g. createSlide.objectId for a new slide) and count."""
        if dry_run:
            raise RuntimeError("dry_run is answered by the consent gate before the tool body runs.")
        try:
            return batch_update_presentation_envelope(
                await run_blocking(
                    batch_update_presentation_payload,
                    BatchUpdatePresentationRequest(presentation_id=presentation_id, requests=requests),
                ),
                presentation_id=presentation_id,
            )
        except HttpError as exc:
            raise provider_tool_error(exc, presentation_id=presentation_id) from exc
