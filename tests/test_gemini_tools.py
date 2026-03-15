from __future__ import annotations

from pathlib import Path

import anyio

from mcp_google_workspace.gemini.server import gemini_mcp
from mcp_google_workspace.gemini.tools import (
    AnalyzeAudioRequest,
    DescribeVideoRequest,
    EditImageRequest,
    GenerateImageRequest,
    analyze_audio_payload,
    describe_video_payload,
    edit_image_payload,
    generate_image_payload,
)


async def _list_tool_names(server):
    tools = await server.list_tools(run_middleware=False)
    return [tool.name for tool in tools]


class _FakeGeminiClient:
    def resolve_model(self, capability: str, override: str | None = None) -> str:
        return override or f"default-{capability}"

    def generate_image(self, *, prompt: str, model: str, aspect_ratio: str | None = None):
        assert prompt
        return {
            "image_bytes": b"png-bytes",
            "mime_type": "image/png",
            "model": model,
            "model_version": model,
        }

    def edit_image(
        self,
        *,
        prompt: str,
        image_bytes: bytes,
        image_mime_type: str,
        model: str,
    ):
        assert prompt
        assert image_bytes
        assert image_mime_type
        return {
            "image_bytes": b"edited-bytes",
            "mime_type": "image/png",
            "model": model,
            "model_version": model,
        }

    def analyze_uploaded_media(
        self,
        *,
        prompt: str,
        local_path: Path,
        mime_type: str,
        model: str,
    ):
        assert prompt
        assert local_path.exists()
        assert mime_type
        return {
            "text": f"analysis for {local_path.name}",
            "mime_type": mime_type,
            "model": model,
            "model_version": model,
            "uploaded_file_name": "files/123",
            "uploaded_uri": "https://example.invalid/files/123",
        }


def test_gemini_server_registers_expected_tools():
    tool_names = anyio.run(_list_tool_names, gemini_mcp)

    assert "generate_image" in tool_names
    assert "edit_image" in tool_names
    assert "describe_video" in tool_names
    assert "analyze_audio" in tool_names


def test_generate_image_payload_writes_local_output(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_OUTPUT_DIR", str(tmp_path))

    payload = generate_image_payload(
        GenerateImageRequest(prompt="draw a mountain", output_filename="scene"),
        client=_FakeGeminiClient(),
    )

    assert payload["status"] == "ok"
    assert payload["output_path"].endswith(".png")
    assert Path(payload["output_path"]).read_bytes() == b"png-bytes"


def test_edit_image_payload_supports_local_input(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_OUTPUT_DIR", str(tmp_path))
    source = tmp_path / "source.png"
    source.write_bytes(b"source-bytes")

    payload = edit_image_payload(
        EditImageRequest(
            prompt="remove the background",
            input_path=str(source),
            output_filename="edited",
        ),
        client=_FakeGeminiClient(),
    )

    assert payload["status"] == "ok"
    assert payload["source"]["type"] == "local"
    assert Path(payload["output_path"]).read_bytes() == b"edited-bytes"


def test_describe_video_payload_supports_drive_input(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "mcp_google_workspace.gemini.tools._load_drive_file_bytes",
        lambda file_id: (b"video-bytes", "video/mp4", "clip.mp4"),
    )

    payload = describe_video_payload(
        DescribeVideoRequest(drive_file_id="drive-video-1"),
        client=_FakeGeminiClient(),
    )

    assert payload["status"] == "ok"
    assert payload["source"]["type"] == "drive"
    assert payload["source"]["file_name"] == "clip.mp4"
    assert payload["mime_type"] == "video/mp4"
    assert payload["description"]


def test_analyze_audio_payload_supports_drive_input(monkeypatch):
    monkeypatch.setattr(
        "mcp_google_workspace.gemini.tools._load_drive_file_bytes",
        lambda file_id: (b"audio-bytes", "audio/mpeg", "sample.mp3"),
    )

    payload = analyze_audio_payload(
        AnalyzeAudioRequest(drive_file_id="drive-audio-1"),
        client=_FakeGeminiClient(),
    )

    assert payload["status"] == "ok"
    assert payload["source"]["type"] == "drive"
    assert payload["source"]["file_name"] == "sample.mp3"
    assert payload["mime_type"] == "audio/mpeg"
    assert payload["analysis"]
