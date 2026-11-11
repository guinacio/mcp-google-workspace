"""Golden cases for the forms_ response normalizers."""

from __future__ import annotations

from mcp_google_workspace.forms.presentation import (
    batch_update_form_envelope,
    created_form_envelope,
    form_envelope,
    form_responses_envelope,
    publish_settings_envelope,
    raw_form_view,
    question_titles,
    response_envelope,
)

from . import TIMEZONE, Case, load

FORM_ID = "1FAIpQLSdExampleFormIdentifier0123456789abcdefghijklmnop"
NEW_FORM_ID = "1FAIpQLSdNewFormIdentifier0123456789abcdefghijklmnopqrstuv"


def _titles() -> dict[str, str]:
    return question_titles(load("forms_get_form.json"))


CASES: list[Case] = [
    Case("forms_get_form", "forms_get_form.json", form_envelope, items_key="items"),
    Case("forms_get_form_legacy", "forms_get_form_legacy.json", form_envelope, items_key="items"),
    # format=raw; forms_get_form_quiz.json is hand-built from the Forms API reference
    # (grading, feedback, option and question images, file upload, video and image items).
    Case("forms_get_form_raw", "forms_get_form_quiz.json", raw_form_view, opaque_keys=("form",)),
    Case("forms_create_form", "forms_create_form.json", created_form_envelope),
    Case(
        "forms_batch_update_form",
        "forms_batch_update_form.json",
        lambda payload: batch_update_form_envelope(payload, form_id=NEW_FORM_ID),
        opaque_keys=("replies",),
    ),
    Case(
        "forms_batch_update_form_without_form",
        "forms_batch_update_form_without_form.json",
        lambda payload: batch_update_form_envelope(payload, form_id=NEW_FORM_ID),
        opaque_keys=("replies",),
    ),
    Case(
        "forms_set_form_publish_settings",
        "forms_set_form_publish_settings.json",
        lambda payload: publish_settings_envelope(payload, form_id=FORM_ID),
    ),
    Case(
        "forms_list_form_responses",
        "forms_list_form_responses.json",
        lambda payload: form_responses_envelope(payload, _titles(), form_id=FORM_ID, account_timezone=TIMEZONE),
        items_key="responses",
    ),
    Case(
        "forms_get_form_response",
        "forms_list_form_responses.json",
        lambda payload: response_envelope(payload["responses"][0], _titles(), account_timezone=TIMEZONE),
    ),
]
