"""Readable Google Forms response representations."""

from __future__ import annotations

from typing import Any

from ..common.timezone import in_account_timezone


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
        "timezone": account_timezone,
        "respondent_email": response.get("respondentEmail"),
        "answers": [
            {"question_id": question_id, "question": titles.get(question_id, question_id), "values": _answer_values(answer)}
            for question_id, answer in answers.items()
        ],
    }
