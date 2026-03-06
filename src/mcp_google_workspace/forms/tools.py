"""FastMCP Forms tools."""

from __future__ import annotations

from typing import Any, Literal

from fastmcp import FastMCP

from .client import forms_service
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
    async def get_form(form_id: str) -> dict[str, Any]:
        return get_form_payload(GetFormRequest(form_id=form_id))

    @server.tool(name="create_form")
    async def create_form(title: str, document_title: str | None = None, unpublished: bool = False) -> dict[str, Any]:
        return create_form_payload(
            CreateFormRequest(title=title, document_title=document_title, unpublished=unpublished)
        )

    @server.tool(name="batch_update_form")
    async def batch_update_form(
        form_id: str,
        requests: list[dict[str, Any]],
        include_form_in_response: bool = False,
    ) -> dict[str, Any]:
        return batch_update_form_payload(
            BatchUpdateFormRequest(
                form_id=form_id,
                requests=requests,
                include_form_in_response=include_form_in_response,
            )
        )

    @server.tool(name="set_form_publish_settings")
    async def set_form_publish_settings(
        form_id: str,
        publish_settings: dict[str, Any],
        update_mask: str = "*",
    ) -> dict[str, Any]:
        return set_form_publish_settings_payload(
            SetFormPublishSettingsRequest(
                form_id=form_id,
                publish_settings=publish_settings,
                update_mask=update_mask,
            )
        )

    @server.tool(name="list_form_responses")
    async def list_form_responses(
        form_id: str,
        page_size: int = 50,
        page_token: str | None = None,
        filter: str | None = None,
    ) -> dict[str, Any]:
        return list_form_responses_payload(
            ListFormResponsesRequest(
                form_id=form_id,
                page_size=page_size,
                page_token=page_token,
                filter=filter,
            )
        )

    @server.tool(name="get_form_response")
    async def get_form_response(form_id: str, response_id: str) -> dict[str, Any]:
        return get_form_response_payload(GetFormResponseRequest(form_id=form_id, response_id=response_id))
