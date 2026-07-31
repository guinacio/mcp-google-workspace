"""FastMCP Forms tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from googleapiclient.errors import HttpError

from ..common.async_ops import run_blocking
from ..common.errors import tool_error_payload
from ..common.timezone import resolve_user_timezone
from .client import forms_service
from .presentation import question_titles, response_envelope
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


def register_tools(server: FastMCP) -> None:
    @server.tool(name="get_form")
    def get_form(form_id: str) -> dict[str, Any]:
        """Fetch a form's full structure: info, settings, and question items.

        form_id is the form's Drive file ID — there is no Forms-side lookup
        tool, so obtain it via drive_list_files or search_workspace first.
        Returns the Form resource, or {"error": ...} on API failure.
        """
        try:
            return get_form_payload(GetFormRequest(form_id=form_id))
        except HttpError as exc:
            return tool_error_payload(exc, form_id=form_id)

    @server.tool(name="create_form")
    def create_form(
        title: str,
        document_title: Annotated[
            str | None, "Name for the form's underlying Drive file; defaults to title when omitted."
        ] = None,
        unpublished: bool = False,
    ) -> dict[str, Any]:
        """Create a new (initially empty) form; add questions with batch_update_form.

        Set unpublished=true to create the form without publishing it. Returns
        the created Form resource (including its 'formId' and responderUri),
        or {"error": ...} on API failure.
        """
        try:
            return create_form_payload(
                CreateFormRequest(title=title, document_title=document_title, unpublished=unpublished)
            )
        except HttpError as exc:
            return tool_error_payload(exc, title=title)

    @server.tool(name="batch_update_form", task=True)
    async def batch_update_form(
        form_id: str,
        requests: Annotated[
            list[dict[str, Any]],
            (
                "Raw Forms API batchUpdate request objects, e.g. "
                '[{"createItem": {"item": {"title": "Q1", "questionItem": {"question": '
                '{"textQuestion": {}}}}, "location": {"index": 0}}}] or updateFormInfo/'
                "updateItem/deleteItem/moveItem requests."
            ),
        ],
        include_form_in_response: bool = False,
    ) -> dict[str, Any]:
        """Apply raw Forms API batchUpdate requests (add/edit/delete/move questions, update info).

        form_id must come from create_form or Drive discovery
        (drive_list_files / search_workspace). Returns the batchUpdate replies
        (plus the updated form when include_form_in_response=true), or
        {"error": ...} on API failure.
        """
        try:
            return await run_blocking(
                batch_update_form_payload,
                BatchUpdateFormRequest(
                    form_id=form_id,
                    requests=requests,
                    include_form_in_response=include_form_in_response,
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, form_id=form_id)

    @server.tool(name="set_form_publish_settings")
    def set_form_publish_settings(
        form_id: str,
        publish_settings: Annotated[
            dict[str, Any],
            (
                "Forms API publishSettings payload, e.g. {\"publishState\": "
                '{"isPublished": true, "isAcceptingResponses": true}}.'
            ),
        ],
        update_mask: Annotated[
            str,
            "Comma-separated field mask of publishSettings paths to update; default '*' updates all fields.",
        ] = "*",
    ) -> dict[str, Any]:
        """Publish/unpublish a form or toggle whether it accepts responses.

        form_id is the form's Drive file ID (from create_form or Drive
        discovery). Returns the applied publish settings, or {"error": ...} on
        API failure.
        """
        try:
            return set_form_publish_settings_payload(
                SetFormPublishSettingsRequest(
                    form_id=form_id,
                    publish_settings=publish_settings,
                    update_mask=update_mask,
                )
            )
        except HttpError as exc:
            return tool_error_payload(exc, form_id=form_id)

    @server.tool(name="list_form_responses")
    async def list_form_responses(
        form_id: str,
        page_size: int = 50,
        page_token: str | None = None,
        filter: Annotated[
            str | None,
            "Forms API response filter query, e.g. \"timestamp >= '2026-01-01T00:00:00Z'\".",
        ] = None,
        enrich_questions: bool = True,
    ) -> dict[str, Any]:
        """List submitted responses for a form, newest layout first.

        form_id is the form's Drive file ID (obtain via drive_list_files or
        search_workspace). When enrich_questions is true, each answer is
        labeled with its question title. Returns {"responses": [...],
        "next_page_token", "count"}, or {"error": ...} on API failure.
        """
        try:
            result = await run_blocking(
                list_form_responses_payload,
                ListFormResponsesRequest(
                    form_id=form_id,
                    page_size=page_size,
                    page_token=page_token,
                    filter=filter,
                )
            )
            account_timezone = await resolve_user_timezone()
            titles = (
                question_titles(await run_blocking(get_form_payload, GetFormRequest(form_id=form_id)))
                if enrich_questions
                else {}
            )
        except HttpError as exc:
            return tool_error_payload(exc, form_id=form_id)
        responses = result.get("responses", [])
        return {
            "form_id": form_id,
            "responses": [
                response_envelope(response, titles, account_timezone=account_timezone)
                for response in responses
            ],
            "next_page_token": result.get("nextPageToken"),
            "count": len(responses),
            "account_timezone": account_timezone,
        }

    @server.tool(name="get_form_response")
    async def get_form_response(form_id: str, response_id: str, enrich_questions: bool = True) -> dict[str, Any]:
        """Fetch one submitted form response by its response ID.

        response_id comes from list_form_responses. When enrich_questions is
        true, answers are labeled with question titles. Returns the response
        envelope, or {"error": ...} on API failure.
        """
        try:
            response = await run_blocking(
                get_form_response_payload,
                GetFormResponseRequest(form_id=form_id, response_id=response_id),
            )
            account_timezone = await resolve_user_timezone()
            titles = (
                question_titles(await run_blocking(get_form_payload, GetFormRequest(form_id=form_id)))
                if enrich_questions
                else {}
            )
        except HttpError as exc:
            return tool_error_payload(exc, form_id=form_id, response_id=response_id)
        return response_envelope(response, titles, account_timezone=account_timezone)
