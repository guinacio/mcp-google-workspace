"""FastMCP Forms tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import run_blocking
from ..common.errors import provider_tool_error
from ..common.timezone import resolve_user_timezone
from .client import forms_service
from .presentation import (
    batch_update_form_envelope,
    created_form_envelope,
    form_envelope,
    form_responses_envelope,
    publish_settings_envelope,
    question_titles,
)
from .schemas import (
    BatchUpdateFormRequest,
    CreateFormRequest,
    GetFormRequest,
    GetFormResponseRequest,
    ListFormResponsesRequest,
    SetFormPublishSettingsRequest,
)


def build_text_question_item_request(title: str, *, index: int = 0, required: bool = False) -> dict[str, Any]:
    return {
        "createItem": {
            "item": {
                "title": title,
                "questionItem": {
                    "question": {
                        "required": required,
                        "textQuestion": {},
                    }
                },
            },
            "location": {"index": index},
        }
    }


def build_choice_question_item_request(
    title: str,
    options: list[str],
    *,
    index: int = 0,
    required: bool = False,
    question_type: Literal["RADIO", "DROP_DOWN", "CHECKBOX"] = "RADIO",
) -> dict[str, Any]:
    return {
        "createItem": {
            "item": {
                "title": title,
                "questionItem": {
                    "question": {
                        "required": required,
                        "choiceQuestion": {
                            "type": question_type,
                            "options": [{"value": option} for option in options],
                            "shuffle": False,
                        },
                    }
                },
            },
            "location": {"index": index},
        }
    }


def get_form_payload(request: GetFormRequest) -> dict[str, Any]:
    service = forms_service()
    return service.forms().get(formId=request.form_id).execute()


def create_form_payload(request: CreateFormRequest) -> dict[str, Any]:
    service = forms_service()
    body = {"info": {"title": request.title, "documentTitle": request.document_title or request.title}}
    return service.forms().create(unpublished=request.unpublished, body=body).execute()


def batch_update_form_payload(request: BatchUpdateFormRequest) -> dict[str, Any]:
    service = forms_service()
    return service.forms().batchUpdate(
        formId=request.form_id,
        body={
            "requests": request.requests,
            "includeFormInResponse": request.include_form_in_response,
        },
    ).execute()


def set_form_publish_settings_payload(request: SetFormPublishSettingsRequest) -> dict[str, Any]:
    service = forms_service()
    return service.forms().setPublishSettings(
        formId=request.form_id,
        body={"publishSettings": request.publish_settings, "updateMask": request.update_mask},
    ).execute()


def list_form_responses_payload(request: ListFormResponsesRequest) -> dict[str, Any]:
    service = forms_service()
    return service.forms().responses().list(
        formId=request.form_id,
        pageSize=request.page_size,
        pageToken=request.page_token,
        filter=request.filter,
    ).execute()


def get_form_response_payload(request: GetFormResponseRequest) -> dict[str, Any]:
    service = forms_service()
    return service.forms().responses().get(formId=request.form_id, responseId=request.response_id).execute()


_ENRICH_QUESTIONS = (
    "Label each answer with its question title, which costs one extra read of the form; when false, "
    "question repeats the question_id."
)


def register_tools(server: FastMCP) -> None:
    @server.tool(name="get_form")
    def get_form(form_id: str) -> dict[str, Any]:
        """Read a Google Form's structure: title, description, publish state and every item with its question. The form_id comes from drive_list_files, search_workspace or forms_create_form; for submitted answers use forms_list_form_responses. Returns form_id, title, document_title, description, responder_url, revision_id, linked_spreadsheet_id, is_quiz, published and accepting_responses (both null for legacy forms), count and items, each with item_id, title, description, kind, question_id (for a grid, a list of question_id and row_title), required, question_type and options (choices, or scale bounds and labels)."""
        try:
            return form_envelope(get_form_payload(GetFormRequest(form_id=form_id)))
        except HttpError as exc:
            raise provider_tool_error(exc, form_id=form_id) from exc

    @server.tool(name="create_form")
    def create_form(
        title: Annotated[str, "Form title shown to respondents at the top of the form."],
        document_title: Annotated[
            str | None, "Name for the form's underlying Drive file; defaults to title when omitted."
        ] = None,
        unpublished: Annotated[
            bool,
            (
                "Create the form unpublished, so it accepts no responses until it is published with "
                "forms_set_form_publish_settings. By default the new form is published and accepts responses."
            ),
        ] = False,
    ) -> dict[str, Any]:
        """Create a new Google Form with a title and no questions; add questions afterwards with forms_batch_update_form (extended tool tier). Returns status ok, form_id (the ID every other forms_ tool takes), title, document_title, responder_url (the link to share with respondents) and revision_id. Unless unpublished is true, the form is published and accepting responses as soon as it exists."""
        try:
            return created_form_envelope(
                create_form_payload(
                    CreateFormRequest(title=title, document_title=document_title, unpublished=unpublished)
                )
            )
        except HttpError as exc:
            raise provider_tool_error(exc, title=title) from exc

    @server.tool(name="batch_update_form", task=True)
    async def batch_update_form(
        form_id: str,
        requests: Annotated[
            list[dict[str, Any]],
            (
                "Forms API batchUpdate request objects, applied in order, e.g. "
                '[{"createItem": {"item": {"title": "Q1", "questionItem": {"question": '
                '{"textQuestion": {}}}}, "location": {"index": 0}}}] or updateFormInfo/'
                "updateSettings/updateItem/deleteItem/moveItem requests."
            ),
        ],
        include_form_in_response: Annotated[
            bool, "Also return the updated form, in the forms_get_form shape, in the form field of the result."
        ] = False,
    ) -> dict[str, Any]:
        """Apply raw Google Forms API batchUpdate requests to a form's questions and info: add, update, move or delete items, or change the title, description and quiz settings. Publishing and whether the form accepts responses are not set here; forms_set_form_publish_settings covers those. The form_id comes from drive_list_files, search_workspace or forms_create_form. Returns status ok, form_id, revision_id (the revision after the update), replies (Google's, one per request in order; createItem gives itemId and questionId), count and form (the forms_get_form shape with include_form_in_response, else null)."""
        try:
            return batch_update_form_envelope(
                await run_blocking(
                    batch_update_form_payload,
                    BatchUpdateFormRequest(
                        form_id=form_id,
                        requests=requests,
                        include_form_in_response=include_form_in_response,
                    ),
                ),
                form_id=form_id,
            )
        except HttpError as exc:
            raise provider_tool_error(exc, form_id=form_id) from exc

    @server.tool(name="set_form_publish_settings")
    def set_form_publish_settings(
        form_id: str,
        publish_settings: Annotated[
            dict[str, Any],
            (
                "Forms API publishSettings object, e.g. {\"publishState\": "
                '{"isPublished": true, "isAcceptingResponses": true}}. Set both fields of publishState; '
                "isAcceptingResponses true with isPublished false is rejected."
            ),
        ],
        update_mask: Annotated[
            str,
            "Which publishSettings fields to write: 'publishState' or '*' (all fields, the default).",
        ] = "*",
    ) -> dict[str, Any]:
        """Publish or unpublish a Google Form, or open and close it for responses. For questions and form info use forms_batch_update_form (extended tool tier); the form_id comes from drive_list_files, search_workspace or forms_create_form. Returns status ok, form_id, published and accepting_responses as they stand after the call. Legacy forms, whose forms_get_form result has null published, do not support this."""
        try:
            return publish_settings_envelope(
                set_form_publish_settings_payload(
                    SetFormPublishSettingsRequest(
                        form_id=form_id,
                        publish_settings=publish_settings,
                        update_mask=update_mask,
                    )
                ),
                form_id=form_id,
            )
        except HttpError as exc:
            raise provider_tool_error(exc, form_id=form_id) from exc

    @server.tool(name="list_form_responses")
    async def list_form_responses(
        form_id: str,
        response_id: Annotated[
            str | None,
            (
                "ID of one response to read, the id of a response in an earlier result. When given, "
                "responses holds just that response and page_size, page_token and filter are ignored."
            ),
        ] = None,
        page_size: Annotated[int, "Responses per page, 1 to 5000."] = 50,
        page_token: str | None = None,
        filter: Annotated[
            str | None,
            (
                "Only responses submitted after a time, in the only syntax the Forms API supports: "
                "'timestamp > 2026-01-01T00:00:00Z' or 'timestamp >= ...' with an RFC3339 time."
            ),
        ] = None,
        enrich_questions: Annotated[bool, _ENRICH_QUESTIONS] = True,
    ) -> dict[str, Any]:
        """List the submitted responses of a Google Form one page at a time, or read one of them by response_id. The form_id comes from drive_list_files, search_workspace or forms_create_form; for the questions themselves use forms_get_form. Returns form_id, count, next_page_token, account_timezone and responses, each with id (the response_id), created_at and submitted_at in the account time zone, respondent_email (only when the form collects emails) and answers as question_id, question and values (answer text, or Drive file IDs for uploads). With response_id, responses holds that one response and next_page_token is null."""
        try:
            if response_id is not None:
                response = await run_blocking(
                    get_form_response_payload,
                    GetFormResponseRequest(form_id=form_id, response_id=response_id),
                )
                result: dict[str, Any] = {"responses": [response]}
            else:
                result = await run_blocking(
                    list_form_responses_payload,
                    ListFormResponsesRequest(
                        form_id=form_id,
                        page_size=page_size,
                        page_token=page_token,
                        filter=filter,
                    ),
                )
            account_timezone = await resolve_user_timezone()
            titles = (
                question_titles(await run_blocking(get_form_payload, GetFormRequest(form_id=form_id)))
                if enrich_questions
                else {}
            )
        except HttpError as exc:
            context = {"form_id": form_id} if response_id is None else {"form_id": form_id, "response_id": response_id}
            raise provider_tool_error(exc, **context) from exc
        return form_responses_envelope(result, titles, form_id=form_id, account_timezone=account_timezone)
