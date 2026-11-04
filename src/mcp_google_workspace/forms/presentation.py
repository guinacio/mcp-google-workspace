"""Readable Google Forms representations.

Pure functions that turn Forms API payloads into the shapes the forms_ tools
return: a form's items with their question IDs, types and options, its publish
state, and submitted responses with answers labelled by question. The raw read
(``raw_form_view``) returns Google's Form resource unchanged instead.
"""

from __future__ import annotations

from typing import Any

from ..common.raw_paging import RAW_LIMIT_CHARS, raw_resource_page
from ..common.resources import clean_web_url
from ..common.timezone import in_account_timezone

_CHOICE_TYPES = {"RADIO": "multiple_choice", "CHECKBOX": "checkboxes", "DROP_DOWN": "dropdown"}
_GRID_TYPES = {"RADIO": "multiple_choice_grid", "CHECKBOX": "checkbox_grid"}
# Item content field -> item kind.
_ITEM_KINDS = {
    "questionItem": "question",
    "questionGroupItem": "question_group",
    "pageBreakItem": "page_break",
    "textItem": "text",
    "imageItem": "image",
    "videoItem": "video",
}
# The option a respondent fills in themselves (ChoiceQuestion option isOther).
OTHER_OPTION = "Other (free text)"


def question_titles(form: dict[str, Any]) -> dict[str, str]:
    titles: dict[str, str] = {}
    for item in form.get("items", []):
        title = item.get("title") or "Untitled question"
        question = item.get("questionItem", {}).get("question", {})
        if question.get("questionId"):
            titles[question["questionId"]] = title
        for grouped_question in item.get("questionGroupItem", {}).get("questions", []):
            if grouped_question.get("questionId"):
                titles[grouped_question["questionId"]] = f"{title}: {grouped_question.get('rowQuestion', {}).get('title') or 'response'}"
    return titles


def _answer_values(answer: dict[str, Any]) -> list[str]:
    text = answer.get("textAnswers", {}).get("answers", [])
    files = answer.get("fileUploadAnswers", {}).get("answers", [])
    return [str(value.get("value", "")) for value in text] + [str(value.get("fileId", "")) for value in files]


def response_envelope(
    response: dict[str, Any], titles: dict[str, str], *, account_timezone: str
) -> dict[str, Any]:
    answers = response.get("answers", {})
    return {
        "id": response.get("responseId"),
        "created_at": in_account_timezone(response.get("createTime"), account_timezone),
        "submitted_at": in_account_timezone(response.get("lastSubmittedTime"), account_timezone),
        "respondent_email": response.get("respondentEmail"),
        "answers": [
            {"question_id": question_id, "question": titles.get(question_id, question_id), "values": _answer_values(answer)}
            for question_id, answer in answers.items()
        ],
    }


def form_responses_envelope(
    payload: dict[str, Any], titles: dict[str, str], *, form_id: str, account_timezone: str
) -> dict[str, Any]:
    """One page of responses.list; every time is in ``account_timezone``."""
    responses = payload.get("responses", [])
    return {
        "form_id": form_id,
        "responses": [
            response_envelope(response, titles, account_timezone=account_timezone) for response in responses
        ],
        "next_page_token": payload.get("nextPageToken"),
        "count": len(responses),
        "account_timezone": account_timezone,
    }


def _choice_options(choice: dict[str, Any]) -> list[str]:
    return [
        OTHER_OPTION if option.get("isOther") else str(option.get("value", ""))
        for option in choice.get("options", [])
    ]


def _question_type_and_options(question: dict[str, Any]) -> tuple[str | None, Any]:
    """A question's type in plain words and its answer options (choices, or scale bounds)."""
    if "textQuestion" in question:
        return ("paragraph" if question["textQuestion"].get("paragraph") else "short_text"), None
    if "choiceQuestion" in question:
        choice = question["choiceQuestion"]
        return _CHOICE_TYPES.get(choice.get("type", ""), "choice"), _choice_options(choice)
    if "scaleQuestion" in question:
        scale = question["scaleQuestion"]
        return "scale", {
            "low": scale.get("low", 0),
            "high": scale.get("high"),
            "low_label": scale.get("lowLabel"),
            "high_label": scale.get("highLabel"),
        }
    if "ratingQuestion" in question:
        rating = question["ratingQuestion"]
        return "rating", {"low": 1, "high": rating.get("ratingScaleLevel"), "low_label": None, "high_label": None}
    if "dateQuestion" in question:
        return ("date_time" if question["dateQuestion"].get("includeTime") else "date"), None
    if "timeQuestion" in question:
        return ("duration" if question["timeQuestion"].get("duration") else "time"), None
    if "fileUploadQuestion" in question:
        return "file_upload", None
    return None, None


def form_item_envelope(item: dict[str, Any]) -> dict[str, Any]:
    kind = next((name for key, name in _ITEM_KINDS.items() if key in item), None)
    question_id: Any = None
    required: bool | None = None
    question_type: str | None = None
    options: Any = None
    if "questionItem" in item:
        question = item["questionItem"].get("question", {})
        question_id = question.get("questionId")
        required = bool(question.get("required", False))
        question_type, options = _question_type_and_options(question)
    elif "questionGroupItem" in item:
        group = item["questionGroupItem"]
        rows = group.get("questions", [])
        question_id = [
            {"question_id": row.get("questionId"), "row_title": (row.get("rowQuestion") or {}).get("title")}
            for row in rows
        ]
        required = any(bool(row.get("required", False)) for row in rows)
        columns = (group.get("grid") or {}).get("columns") or {}
        question_type = _GRID_TYPES.get(columns.get("type", ""), "grid")
        options = _choice_options(columns)
    return {
        "item_id": item.get("itemId"),
        "title": item.get("title"),
        "description": item.get("description"),
        "kind": kind,
        "question_id": question_id,
        "required": required,
        "question_type": question_type,
        "options": options,
    }


def publish_state(payload: dict[str, Any]) -> tuple[bool | None, bool | None]:
    """(published, accepting_responses); both None for legacy forms without publishSettings."""
    settings = payload.get("publishSettings")
    if not isinstance(settings, dict):
        return None, None
    state = settings.get("publishState") or {}
    return bool(state.get("isPublished", False)), bool(state.get("isAcceptingResponses", False))


def form_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """A form's info, publish state and items."""
    info = payload.get("info", {})
    items = payload.get("items", [])
    published, accepting = publish_state(payload)
    quiz = (payload.get("settings") or {}).get("quizSettings") or {}
    return {
        "form_id": payload.get("formId"),
        "title": info.get("title"),
        "document_title": info.get("documentTitle"),
        "description": info.get("description"),
        "responder_url": clean_web_url(payload.get("responderUri")),
        "revision_id": payload.get("revisionId"),
        "linked_spreadsheet_id": payload.get("linkedSheetId"),
        "is_quiz": bool(quiz.get("isQuiz", False)),
        "published": published,
        "accepting_responses": accepting,
        "items": [form_item_envelope(item) for item in items],
        "count": len(items),
    }


def raw_form_view(payload: dict[str, Any], *, offset: int = 0, limit: int = RAW_LIMIT_CHARS) -> dict[str, Any]:
    """forms_get_form (raw): Google's Form resource under ``form``, paged by item."""
    paged = raw_resource_page(
        payload,
        id_key="formId",
        lists=("items",),
        offset=offset,
        limit=limit,
        narrow_hint="read its question with format normalized",
    )
    return {
        "form_id": payload.get("formId"),
        "title": payload.get("info", {}).get("title"),
        "revision_id": payload.get("revisionId"),
        "responder_url": clean_web_url(payload.get("responderUri")),
        "format": "raw",
        "form": dict(paged["page"]),
        # Typed conversions, so the published output schema reads each field's type.
        "truncated": bool(paged["next_offset"] is not None),
        "next_offset": paged["next_offset"],
        "element_count": int(paged["element_count"]),
        "raw_chars": int(paged["raw_chars"]),
        "raw_sha256": str(paged["raw_sha256"]),
    }


def created_form_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    info = payload.get("info", {})
    return {
        "status": "ok",
        "form_id": payload.get("formId"),
        "title": info.get("title"),
        "document_title": info.get("documentTitle"),
        "responder_url": clean_web_url(payload.get("responderUri")),
        "revision_id": payload.get("revisionId"),
    }


def batch_update_form_envelope(payload: dict[str, Any], *, form_id: str) -> dict[str, Any]:
    """A batchUpdate result (Google's response carries no formId); ``form`` only when asked for."""
    replies = payload.get("replies", [])
    form = payload.get("form")
    return {
        "status": "ok",
        "form_id": form_id,
        "revision_id": (payload.get("writeControl") or {}).get("requiredRevisionId"),
        "replies": replies,
        "count": len(replies),
        "form": form_envelope(form) if isinstance(form, dict) else None,
    }


def publish_settings_envelope(payload: dict[str, Any], *, form_id: str) -> dict[str, Any]:
    published, accepting = publish_state(payload)
    return {
        "status": "ok",
        "form_id": payload.get("formId") or form_id,
        "published": published,
        "accepting_responses": accepting,
    }
