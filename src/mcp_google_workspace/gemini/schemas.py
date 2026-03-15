"""Pydantic schemas for Gemini media tools."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ..common.request_model import ToolRequestModel

GeminiCapability = Literal[
    "image_generate",
    "image_edit",
    "video_understanding",
    "audio_understanding",
]


class _OneInputSourceModel(ToolRequestModel):
    input_path: str | None = Field(
        default=None,
        description="Local filesystem path to the input media file.",
    )
    drive_file_id: str | None = Field(
        default=None,
        description="Google Drive file ID for the input media file.",
    )

    @model_validator(mode="after")
    def _validate_exactly_one_input_source(self) -> "_OneInputSourceModel":
        provided = [bool(self.input_path), bool(self.drive_file_id)]
        if sum(provided) != 1:
            raise ValueError("Exactly one of input_path or drive_file_id must be provided.")
        return self


class GenerateImageRequest(ToolRequestModel):
    prompt: str = Field(description="Prompt describing the image to generate.")
    aspect_ratio: str | None = Field(
        default=None,
        description="Optional image aspect ratio such as 1:1, 4:3, 3:4, 16:9, or 9:16.",
    )
    model: str | None = Field(
        default=None,
        description="Optional image-generation model override.",
    )
    output_filename: str | None = Field(
        default=None,
        description="Optional output filename. If no extension is provided, one is inferred from the output MIME type.",
    )
    output_dir: str | None = Field(
        default=None,
        description="Optional output directory override. Defaults to GEMINI_OUTPUT_DIR.",
    )


class EditImageRequest(_OneInputSourceModel):
    prompt: str = Field(description="Prompt describing the requested image edit.")
    model: str | None = Field(
        default=None,
        description="Optional image-edit model override.",
    )
    output_filename: str | None = Field(
        default=None,
        description="Optional output filename. If no extension is provided, one is inferred from the output MIME type.",
    )
    output_dir: str | None = Field(
        default=None,
        description="Optional output directory override. Defaults to GEMINI_OUTPUT_DIR.",
    )


class DescribeVideoRequest(_OneInputSourceModel):
    prompt: str | None = Field(
        default=None,
        description="Optional prompt steering the video description or analysis.",
    )
    model: str | None = Field(
        default=None,
        description="Optional video-understanding model override.",
    )


class AnalyzeAudioRequest(_OneInputSourceModel):
    prompt: str | None = Field(
        default=None,
        description="Optional prompt steering the audio analysis.",
    )
    model: str | None = Field(
        default=None,
        description="Optional audio-understanding model override.",
    )
