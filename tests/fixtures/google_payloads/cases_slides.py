"""Golden cases for the slides_ response normalizers."""

from __future__ import annotations

from mcp_google_workspace.slides.presentation import (
    batch_update_presentation_envelope,
    created_presentation_envelope,
    page_envelope,
    presentation_envelope,
    raw_pages_view,
    raw_presentation_view,
    replace_text_envelope,
    thumbnail_envelope,
)

from . import Case

PRESENTATION_ID = "1ZyXwVuTsRqPoNmLkJiHgFeDcBa9876543210zyxwvutsrq"

CASES: list[Case] = [
    Case("slides_get_presentation", "slides_get_presentation.json", presentation_envelope, items_key="slides"),
    Case("slides_get_slide_page", "slides_get_slide_page.json", page_envelope, items_key="elements"),
    # format=raw: Google's Presentation, first page (the fixture is larger than one page).
    Case("slides_read_presentation_raw", "slides_get_presentation.json", raw_presentation_view, opaque_keys=("presentation",)),
    # format=raw with slide_ids: Google's Page resources from presentations.pages.get.
    Case(
        "slides_read_presentation_raw_pages",
        "slides_get_slide_page.json",
        lambda payload: raw_pages_view(PRESENTATION_ID, [payload]),
        opaque_keys=("pages",),
    ),
    Case(
        "slides_get_slide_thumbnail",
        "slides_get_slide_thumbnail.json",
        lambda payload: thumbnail_envelope(payload, mime_type="PNG"),
    ),
    Case("slides_create_presentation", "slides_create_presentation.json", created_presentation_envelope, items_key="slides"),
    Case(
        "slides_replace_text_in_presentation",
        "slides_replace_text_in_presentation.json",
        lambda payload: replace_text_envelope(payload, presentation_id=PRESENTATION_ID),
    ),
    Case(
        "slides_replace_text_in_presentation_no_match",
        "slides_replace_text_in_presentation_no_match.json",
        lambda payload: replace_text_envelope(payload, presentation_id=PRESENTATION_ID),
    ),
    Case(
        "slides_batch_update_presentation",
        "slides_batch_update_presentation.json",
        lambda payload: batch_update_presentation_envelope(payload, presentation_id=PRESENTATION_ID),
        opaque_keys=("replies",),
    ),
]
