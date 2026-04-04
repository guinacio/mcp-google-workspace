"""Gemini Developer API helpers for media workflows."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from ..runtime import RuntimeSettings, get_runtime_settings
from .schemas import GeminiCapability

LOGGER = logging.getLogger(__name__)

IMAGE_MODEL_FALLBACKS = {
    "gemini-3.1-flash-image-preview",
    "gemini-3-pro-image-preview",
    "gemini-2.5-flash-image",
}
UNDERSTANDING_MODEL_FALLBACKS = {
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
}


def _collect_text_from_response(response: types.GenerateContentResponse) -> str:
    if response.text and response.text.strip():
        return response.text.strip()
    for candidate in response.candidates or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                return text.strip()
    raise ValueError("Gemini did not return text content.")


def _collect_image_from_response(
    response: types.GenerateContentResponse,
) -> tuple[bytes, str]:
    for candidate in response.candidates or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            inline_data = getattr(part, "inline_data", None)
            if inline_data and getattr(inline_data, "data", None):
                mime_type = getattr(inline_data, "mime_type", None) or "image/png"
                data = inline_data.data
                if isinstance(data, str):
                    data = data.encode("utf-8")
                return data, mime_type
    raise ValueError("Gemini did not return image bytes.")


class GeminiMediaClient:
    """Thin wrapper around the Gemini Developer API."""

    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.settings = settings or get_runtime_settings()
        if not self.settings.gemini_api_key:
            raise ValueError(
                "Gemini is enabled but GEMINI_API_KEY is not configured."
            )
        self._client = genai.Client(
            api_key=self.settings.gemini_api_key,
            http_options=types.HttpOptions(
                timeout=int(self.settings.gemini_timeout_seconds * 1000)
            ),
        )

    def _wait_for_uploaded_file_ready(self, uploaded: Any) -> Any:
        deadline = time.monotonic() + self.settings.gemini_timeout_seconds
        current = uploaded
        while True:
            state = getattr(current, "state", None)
            state_name = getattr(state, "name", None) or str(state or "")
            normalized_state = state_name.upper()
            if normalized_state in {"", "ACTIVE"}:
                return current
            if normalized_state == "FAILED":
                raise ValueError(
                    f"Gemini file upload failed for {getattr(current, 'name', 'unknown file')}."
                )
            if normalized_state != "PROCESSING":
                raise ValueError(
                    f"Gemini file upload entered unexpected state {state_name!r} for {getattr(current, 'name', 'unknown file')}."
                )
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Gemini file {getattr(current, 'name', 'unknown file')} did not become ACTIVE before the configured timeout."
                )
            time.sleep(2)
            current = self._client.files.get(name=current.name)

    def resolve_model(
        self,
        capability: GeminiCapability,
        override: str | None = None,
    ) -> str:
        if capability == "image_generate":
            default_model = self.settings.gemini_image_generate_model
            allowed = set(IMAGE_MODEL_FALLBACKS) | {default_model}
        elif capability == "image_edit":
            default_model = self.settings.gemini_image_edit_model
            allowed = set(IMAGE_MODEL_FALLBACKS) | {default_model}
        elif capability == "video_understanding":
            default_model = self.settings.gemini_video_understanding_model
            allowed = set(UNDERSTANDING_MODEL_FALLBACKS) | {
                default_model,
                self.settings.gemini_reasoning_model,
            }
        elif capability == "audio_understanding":
            default_model = self.settings.gemini_audio_understanding_model
            allowed = set(UNDERSTANDING_MODEL_FALLBACKS) | {
                default_model,
                self.settings.gemini_reasoning_model,
            }
        else:  # pragma: no cover - defensive guard
            raise ValueError(f"Unsupported Gemini capability: {capability}")

        resolved = (override or default_model).strip()
        if resolved not in allowed:
            allowed_csv = ", ".join(sorted(allowed))
            raise ValueError(
                f"Model {resolved!r} is not allowed for capability {capability}. "
                f"Allowed models: {allowed_csv}."
            )
        return resolved

    def generate_image(
        self,
        *,
        prompt: str,
        model: str,
        aspect_ratio: str | None = None,
    ) -> dict[str, Any]:
        image_config_kwargs: dict[str, Any] = {}
        if aspect_ratio:
            image_config_kwargs["aspect_ratio"] = aspect_ratio
        response = self._client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=[types.Modality.IMAGE],
                image_config=types.ImageConfig(**image_config_kwargs),
            ),
        )
        image_bytes, mime_type = _collect_image_from_response(response)
        return {
            "image_bytes": image_bytes,
            "mime_type": mime_type,
            "model": model,
            "model_version": response.model_version or model,
        }

    def edit_image(
        self,
        *,
        prompt: str,
        image_bytes: bytes,
        image_mime_type: str,
        model: str,
    ) -> dict[str, Any]:
        response = self._client.models.generate_content(
            model=model,
            contents=[
                prompt,
                types.Part.from_bytes(data=image_bytes, mime_type=image_mime_type),
            ],
            config=types.GenerateContentConfig(
                response_modalities=[types.Modality.IMAGE],
                image_config=types.ImageConfig(),
            ),
        )
        output_bytes, mime_type = _collect_image_from_response(response)
        return {
            "image_bytes": output_bytes,
            "mime_type": mime_type,
            "model": model,
            "model_version": response.model_version or model,
        }

    def analyze_uploaded_media(
        self,
        *,
        prompt: str,
        local_path: Path,
        mime_type: str,
        model: str,
    ) -> dict[str, Any]:
        uploaded = self._client.files.upload(
            file=local_path,
            config=types.UploadFileConfig(
                mime_type=mime_type,
                display_name=local_path.name,
            ),
        )
        try:
            uploaded = self._wait_for_uploaded_file_ready(uploaded)
            response = self._client.models.generate_content(
                model=model,
                contents=[uploaded, prompt],
            )
        finally:
            try:
                uploaded_name = uploaded.name
                if uploaded_name is not None:
                    self._client.files.delete(name=uploaded_name)
            except Exception:  # pragma: no cover - cleanup best effort
                LOGGER.warning("Failed to delete uploaded Gemini file %s.", uploaded.name)
        return {
            "text": _collect_text_from_response(response),
            "mime_type": mime_type,
            "model": model,
            "model_version": response.model_version or model,
            "uploaded_file_name": uploaded.name,
            "uploaded_uri": uploaded.uri,
        }
