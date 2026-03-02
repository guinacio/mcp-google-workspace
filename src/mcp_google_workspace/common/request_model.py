"""Shared request model behavior for MCP tools."""

from __future__ import annotations

import json
import re
from typing import Any, get_args, get_origin

from pydantic import BaseModel, ValidationInfo, field_validator, model_validator


def _camel_to_snake(key: str) -> str:
    if not key:
        return key
    key = key.replace("-", "_")
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


def _annotation_contains(annotation: Any, target: type[Any]) -> bool:
    origin = get_origin(annotation)
    if origin is None:
        return annotation is target
    if origin is target:
        return True
    return any(_annotation_contains(arg, target) for arg in get_args(annotation))


def _annotation_contains_basemodel(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin is None:
        return isinstance(annotation, type) and issubclass(annotation, BaseModel)
    return any(_annotation_contains_basemodel(arg) for arg in get_args(annotation))


class ToolRequestModel(BaseModel):
    """Base input model for MCP tools expecting object payloads."""

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "description": (
                "Pass this as a JSON object payload to the tool. "
                "Do not pass a raw string for the full request."
            )
        },
    }

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload_shape(cls, data: Any) -> Any:
        if isinstance(data, str):
            raw = data.strip()
            if not raw:
                raise ValueError("Tool request cannot be an empty string.")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "Tool request must be a JSON object (not a plain string)."
                ) from exc

        if isinstance(data, dict):
            normalized: dict[Any, Any] = {}
            for key, value in data.items():
                if isinstance(key, str):
                    normalized[_camel_to_snake(key)] = value
                else:
                    normalized[key] = value
            return normalized
        return data

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_json_text_for_collection_fields(cls, value: Any, info: ValidationInfo) -> Any:
        if not isinstance(value, str):
            return value

        field_name = info.field_name
        if not field_name:
            return value
        field = cls.model_fields.get(field_name)
        if field is None:
            return value

        expects_list = _annotation_contains(field.annotation, list)
        expects_dict = _annotation_contains(field.annotation, dict)
        expects_model = _annotation_contains_basemodel(field.annotation)
        if not (expects_list or expects_dict or expects_model):
            return value

        raw = value.strip()
        if not raw:
            return value

        if raw[0] in ("[", "{"):
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                expected = "JSON array" if expects_list else "JSON object"
                raise ValueError(
                    f"Field '{field_name}' expects {expected} text when passed as a string."
                ) from exc

        if expects_list:
            return [item.strip() for item in raw.split(",") if item.strip()]
        if expects_dict or expects_model:
            raise ValueError(
                f"Field '{field_name}' expects a JSON object; plain text is not supported."
            )
        return value
