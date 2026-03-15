from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_manifest_declares_uv_bundle_and_runtime_config() -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == "0.4"
    assert manifest["name"] == "mcp-google-workspace"
    assert manifest["icon"] == "icon.png"
    assert "https://policies.google.com/privacy" in manifest["privacy_policies"]
    assert manifest["server"]["type"] == "uv"
    assert (
        manifest["server"]["entry_point"] == "src/mcp_google_workspace/bundle_entry.py"
    )
    assert manifest["server"]["mcp_config"]["command"] == "uv"
    assert manifest["server"]["mcp_config"]["args"] == [
        "run",
        "src/mcp_google_workspace/bundle_entry.py",
    ]
    assert manifest["compatibility"]["runtimes"]["python"] == ">=3.12,<4.0"
    assert manifest["tools_generated"] is True


def test_manifest_maps_bundle_user_config_to_runtime_env() -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    env = manifest["server"]["mcp_config"]["env"]

    assert env["MCP_CREDENTIALS_DIR"] == "${user_config.credentials_dir}"
    assert env["ENABLE_CHAT"] == "${user_config.enable_chat}"
    assert env["ENABLE_GEMINI"] == "${user_config.enable_gemini}"
    assert env["GEMINI_API_KEY"] == "${user_config.gemini_api_key}"
    assert (
        env["GEMINI_IMAGE_GENERATE_MODEL"]
        == "${user_config.gemini_image_generate_model}"
    )
    assert (
        env["MCP_GOOGLE_HTTP_TIMEOUT_SECONDS"] == "${user_config.http_timeout_seconds}"
    )
    assert env["MCP_GOOGLE_LOG_LEVEL"] == "${user_config.log_level}"


def test_manifest_declares_gemini_bundle_config() -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    user_config = manifest["user_config"]

    assert user_config["enable_gemini"]["default"] is False
    assert user_config["gemini_image_generate_model"]["default"] == "gemini-3.1-flash-image-preview"
    assert user_config["gemini_video_understanding_model"]["default"] == "gemini-3-flash-preview"
    assert user_config["gemini_reasoning_model"]["default"] == "gemini-3.1-pro-preview"


def test_mcpbignore_excludes_secrets_and_build_noise() -> None:
    ignore_text = (ROOT / ".mcpbignore").read_text(encoding="utf-8")

    assert "src/credentials/credentials.json" in ignore_text
    assert "src/credentials/token.json" in ignore_text
    assert "src/mcp_google_workspace/apps/ui/node_modules/" in ignore_text


