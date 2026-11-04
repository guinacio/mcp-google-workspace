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
        description=(
            "Path of the input file on the machine running the server; rejected over remote HTTP. "
            "Give exactly one of input_path, drive_file_id or uploaded_file."
        ),
    )
    drive_file_id: str | None = Field(
        default=None,
        description=(
            "Drive file ID of the input file; Google Docs, Sheets and Slides files are not supported. "
            "Give exactly one of input_path, drive_file_id or uploaded_file."
        ),
    )
    uploaded_file: str | None = Field(
        default=None,
        description=(
            "Upload handle (upl_...) returned by files_file_manager after the user picks a file. "
            "Give exactly one of input_path, drive_file_id or uploaded_file."
        ),
    )

    @model_validator(mode="after")
    def _validate_exactly_one_input_source(self) -> "_OneInputSourceModel":
        provided = [bool(self.input_path), bool(self.drive_file_id), bool(self.uploaded_file)]
        if sum(provided) != 1:
            raise ValueError("Exactly one of input_path, drive_file_id, or uploaded_file must be provided.")
        return self


class GenerateImageRequest(ToolRequestModel):
    prompt: str = Field(description="Text description of the image to generate: subject, style, composition.")
    aspect_ratio: str | None = Field(
        default=None,
        description="Width-to-height ratio, e.g. 1:1, 4:3, 3:4, 16:9 or 9:16, passed to Gemini unchanged; omit to let the model choose.",
    )
    model: str | None = Field(
        default=None,
        description="Gemini image model ID, e.g. gemini-2.5-flash-image; must be an allowed model, omit for the configured default.",
    )
    output_filename: str | None = Field(
        default=None,
        description=(
            "File name to save the image as; a missing extension is added from the image type and an "
            "existing file is never overwritten. Omit for a generated name."
        ),
    )
    output_dir: str | None = Field(
        default=None,
        description="Directory to save the image in, created if missing; omit to use the configured GEMINI_OUTPUT_DIR. Rejected over remote HTTP.",
    )


class EditImageRequest(_OneInputSourceModel):
    prompt: str = Field(description="Instruction describing the edit to make to the input image.")
    model: str | None = Field(
        default=None,
        description="Gemini image model ID, e.g. gemini-2.5-flash-image; must be an allowed model, omit for the configured default.",
    )
    output_filename: str | None = Field(
        default=None,
        description=(
            "File name to save the image as; a missing extension is added from the image type and an "
            "existing file is never overwritten. Omit for a generated name."
        ),
    )
    output_dir: str | None = Field(
        default=None,
        description="Directory to save the image in, created if missing; omit to use the configured GEMINI_OUTPUT_DIR. Rejected over remote HTTP.",
    )


class DescribeVideoRequest(_OneInputSourceModel):
    prompt: str | None = Field(
        default=None,
        description="Question or instruction about the video; omit for a general description of its events and scenes.",
    )
    model: str | None = Field(
        default=None,
        description="Gemini model ID, e.g. gemini-3-flash-preview; must be an allowed model, omit for the configured default.",
    )


class AnalyzeAudioRequest(_OneInputSourceModel):
    prompt: str | None = Field(
        default=None,
        description="Question or instruction about the audio; omit for a summary of its main content, speakers and notable moments.",
    )
    model: str | None = Field(
        default=None,
        description="Gemini model ID, e.g. gemini-3-flash-preview; must be an allowed model, omit for the configured default.",
    )
