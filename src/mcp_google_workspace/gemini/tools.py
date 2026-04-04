"""FastMCP Gemini media tools."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastmcp import Context, FastMCP

from ..common.async_ops import run_blocking
from ..drive.client import download_media_to_bytes, drive_service
from ..runtime import get_runtime_settings
from .client import GeminiMediaClient
from .schemas import (
    AnalyzeAudioRequest,
    DescribeVideoRequest,
    EditImageRequest,
    GenerateImageRequest,
)
from .storage import (
    build_output_path,
    guess_mime_type,
    resolve_output_dir,
    stage_temp_file,
)

LOGGER = logging.getLogger(__name__)


def _load_drive_file_bytes(file_id: str) -> tuple[bytes, str, str]:
    service = drive_service()
    metadata = (
        service.files()
        .get(
            fileId=file_id,
            supportsAllDrives=True,
            fields="id,name,mimeType",
        )
        .execute()
    )
    mime_type = metadata.get("mimeType") or "application/octet-stream"
    if str(mime_type).startswith("application/vnd.google-apps"):
        raise ValueError(
            f"Drive file {file_id} uses Google Workspace native mime type {mime_type!r} and is not supported by Gemini media tools."
        )
    file_name = metadata.get("name") or file_id
    data = download_media_to_bytes(
        service.files().get_media(fileId=file_id, supportsAllDrives=True)
    )
    return data, mime_type, file_name


def _resolve_local_input(
    *,
    input_path: str | None,
    drive_file_id: str | None,
) -> tuple[Path, str, str, bool]:
    if input_path:
        local_path = Path(input_path).expanduser().resolve()
        if not local_path.exists():
            raise FileNotFoundError(f"Local file not found: {local_path}")
        return local_path, guess_mime_type(local_path), local_path.name, False

    if not drive_file_id:
        raise ValueError("Either input_path or drive_file_id must be provided.")

    data, mime_type, file_name = _load_drive_file_bytes(drive_file_id)
    staged_path = stage_temp_file(data, filename=file_name, mime_type=mime_type)
    return staged_path, mime_type, file_name, True


def generate_image_payload(
    request: GenerateImageRequest,
    *,
    client: GeminiMediaClient | None = None,
) -> dict[str, Any]:
    gemini_client = client or GeminiMediaClient()
    model = gemini_client.resolve_model("image_generate", request.model)
    generated = gemini_client.generate_image(
        prompt=request.prompt,
        model=model,
        aspect_ratio=request.aspect_ratio,
    )
    settings = get_runtime_settings()
    output_dir = resolve_output_dir(settings.gemini_output_dir, request.output_dir)
    output_path = build_output_path(
        output_dir,
        output_filename=request.output_filename,
        default_stem="gemini-image",
        mime_type=generated["mime_type"],
    )
    output_path.write_bytes(generated["image_bytes"])
    return {
        "status": "ok",
        "capability": "image_generate",
        "model": generated["model"],
        "model_version": generated["model_version"],
        "mime_type": generated["mime_type"],
        "output_path": str(output_path),
        "bytes_written": output_path.stat().st_size,
    }


def edit_image_payload(
    request: EditImageRequest,
    *,
    client: GeminiMediaClient | None = None,
) -> dict[str, Any]:
    gemini_client = client or GeminiMediaClient()
    model = gemini_client.resolve_model("image_edit", request.model)
    local_path, mime_type, file_name, is_temp = _resolve_local_input(
        input_path=request.input_path,
        drive_file_id=request.drive_file_id,
    )
    try:
        edited = gemini_client.edit_image(
            prompt=request.prompt,
            image_bytes=local_path.read_bytes(),
            image_mime_type=mime_type,
            model=model,
        )
    finally:
        if is_temp:
            local_path.unlink(missing_ok=True)
    settings = get_runtime_settings()
    output_dir = resolve_output_dir(settings.gemini_output_dir, request.output_dir)
    output_path = build_output_path(
        output_dir,
        output_filename=request.output_filename,
        default_stem=f"edited-{Path(file_name).stem or 'image'}",
        mime_type=edited["mime_type"],
    )
    output_path.write_bytes(edited["image_bytes"])
    return {
        "status": "ok",
        "capability": "image_edit",
        "model": edited["model"],
        "model_version": edited["model_version"],
        "mime_type": edited["mime_type"],
        "output_path": str(output_path),
        "bytes_written": output_path.stat().st_size,
        "source": {
            "type": "drive" if request.drive_file_id else "local",
            "file_name": file_name,
            "drive_file_id": request.drive_file_id,
            "input_path": request.input_path,
        },
    }


def describe_video_payload(
    request: DescribeVideoRequest,
    *,
    client: GeminiMediaClient | None = None,
) -> dict[str, Any]:
    gemini_client = client or GeminiMediaClient()
    model = gemini_client.resolve_model("video_understanding", request.model)
    local_path, mime_type, file_name, is_temp = _resolve_local_input(
        input_path=request.input_path,
        drive_file_id=request.drive_file_id,
    )
    prompt = request.prompt or "Describe the important events, scenes, and notable details in this video."
    try:
        result = gemini_client.analyze_uploaded_media(
            prompt=prompt,
            local_path=local_path,
            mime_type=mime_type,
            model=model,
        )
    finally:
        if is_temp:
            local_path.unlink(missing_ok=True)
    return {
        "status": "ok",
        "capability": "video_understanding",
        "model": result["model"],
        "model_version": result["model_version"],
        "mime_type": mime_type,
        "description": result["text"],
        "source": {
            "type": "drive" if request.drive_file_id else "local",
            "file_name": file_name,
            "drive_file_id": request.drive_file_id,
            "input_path": request.input_path,
        },
    }


def analyze_audio_payload(
    request: AnalyzeAudioRequest,
    *,
    client: GeminiMediaClient | None = None,
) -> dict[str, Any]:
    gemini_client = client or GeminiMediaClient()
    model = gemini_client.resolve_model("audio_understanding", request.model)
    local_path, mime_type, file_name, is_temp = _resolve_local_input(
        input_path=request.input_path,
        drive_file_id=request.drive_file_id,
    )
    prompt = request.prompt or "Analyze this audio and summarize the most important content, speakers, and notable moments."
    try:
        result = gemini_client.analyze_uploaded_media(
            prompt=prompt,
            local_path=local_path,
            mime_type=mime_type,
            model=model,
        )
    finally:
        if is_temp:
            local_path.unlink(missing_ok=True)
    return {
        "status": "ok",
        "capability": "audio_understanding",
        "model": result["model"],
        "model_version": result["model_version"],
        "mime_type": mime_type,
        "analysis": result["text"],
        "source": {
            "type": "drive" if request.drive_file_id else "local",
            "file_name": file_name,
            "drive_file_id": request.drive_file_id,
            "input_path": request.input_path,
        },
    }


async def _report_tool_start(ctx: Context | None, message: str) -> None:
    if ctx is not None:
        await ctx.info(message)


async def _report_tool_progress(ctx: Context | None, current: int, total: int, message: str) -> None:
    if ctx is not None:
        await ctx.report_progress(current, total, message)


def register_tools(server: FastMCP) -> None:
    @server.tool(name="generate_image")
    async def gemini_generate_image(
        prompt: str,
        aspect_ratio: str | None = None,
        model: str | None = None,
        output_filename: str | None = None,
        output_dir: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Generate an image locally using Gemini image-capable models."""
        request = GenerateImageRequest(
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            model=model,
            output_filename=output_filename,
            output_dir=output_dir,
        )
        await _report_tool_start(ctx, "Starting Gemini image generation.")
        await _report_tool_progress(ctx, 10, 100, "Generating image")
        payload = await run_blocking(generate_image_payload, request)
        LOGGER.info(
            "Gemini image generation completed model=%s output=%s",
            payload["model"],
            payload["output_path"],
        )
        await _report_tool_progress(ctx, 100, 100, "Image generated")
        return payload

    @server.tool(name="edit_image")
    async def gemini_edit_image(
        prompt: str,
        input_path: str | None = None,
        drive_file_id: str | None = None,
        model: str | None = None,
        output_filename: str | None = None,
        output_dir: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Edit an existing image from a local path or Drive file using Gemini image-capable models."""
        request = EditImageRequest(
            prompt=prompt,
            input_path=input_path,
            drive_file_id=drive_file_id,
            model=model,
            output_filename=output_filename,
            output_dir=output_dir,
        )
        await _report_tool_start(ctx, "Starting Gemini image edit.")
        await _report_tool_progress(ctx, 10, 100, "Preparing image input")
        payload = await run_blocking(edit_image_payload, request)
        LOGGER.info(
            "Gemini image edit completed model=%s source=%s output=%s",
            payload["model"],
            payload["source"]["type"],
            payload["output_path"],
        )
        await _report_tool_progress(ctx, 100, 100, "Image edit completed")
        return payload

    @server.tool(name="describe_video")
    async def gemini_describe_video(
        input_path: str | None = None,
        drive_file_id: str | None = None,
        prompt: str | None = None,
        model: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Describe or analyze an existing video file using Gemini multimodal models."""
        request = DescribeVideoRequest(
            input_path=input_path,
            drive_file_id=drive_file_id,
            prompt=prompt,
            model=model,
        )
        await _report_tool_start(ctx, "Starting Gemini video understanding.")
        await _report_tool_progress(ctx, 10, 100, "Preparing video input")
        payload = await run_blocking(describe_video_payload, request)
        LOGGER.info(
            "Gemini video understanding completed model=%s source=%s",
            payload["model"],
            payload["source"]["type"],
        )
        await _report_tool_progress(ctx, 100, 100, "Video analysis completed")
        return payload

    @server.tool(name="analyze_audio")
    async def gemini_analyze_audio(
        input_path: str | None = None,
        drive_file_id: str | None = None,
        prompt: str | None = None,
        model: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Analyze an existing audio file using Gemini multimodal models."""
        request = AnalyzeAudioRequest(
            input_path=input_path,
            drive_file_id=drive_file_id,
            prompt=prompt,
            model=model,
        )
        await _report_tool_start(ctx, "Starting Gemini audio understanding.")
        await _report_tool_progress(ctx, 10, 100, "Preparing audio input")
        payload = await run_blocking(analyze_audio_payload, request)
        LOGGER.info(
            "Gemini audio understanding completed model=%s source=%s",
            payload["model"],
            payload["source"]["type"],
        )
        await _report_tool_progress(ctx, 100, 100, "Audio analysis completed")
        return payload
