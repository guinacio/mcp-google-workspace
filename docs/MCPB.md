# MCP Google Workspace MCPB Bundle

This repository now includes a native `uv`-based MCP Bundle manifest for the existing FastMCP server.

## Bundle Layout

- `manifest.json`: MCPB metadata, compatibility requirements, runtime mapping, and user configuration.
- `.mcpbignore`: excludes secrets, virtual environments, caches, tests, and frontend `node_modules`.
- `pyproject.toml`: dependency source for `uv` hosts.
- `src/mcp_google_workspace/bundle_entry.py`: stdio entrypoint used by MCPB hosts.
- `scripts/build_mcpb.py`: creates a local `.mcpb` archive without requiring the external MCPB CLI.

## Runtime Behavior

The bundle runs the existing composed FastMCP server over stdio:

```powershell
uv run python -m mcp_google_workspace.bundle_entry
```

The entrypoint adds:

- stderr logging with configurable log level
- validated timeout and retry settings for Google API clients
- OAuth port/browser configuration for local desktop consent flows
- clearer startup errors when bundle configuration is invalid

## User Configuration Mapped By The Manifest

The MCPB manifest exposes these settings through the host UI and passes them into the local process as environment variables:

- `credentials_dir` -> `MCP_CREDENTIALS_DIR`
- `enable_apps_dashboard` -> `ENABLE_APPS_DASHBOARD`
- `enable_chat` -> `ENABLE_CHAT`
- `enable_gemini` -> `ENABLE_GEMINI`
- `enable_keep` -> `ENABLE_KEEP`
- `enable_meet` -> `ENABLE_MEET`
- `gemini_api_key` -> `GEMINI_API_KEY`
- `gemini_image_generate_model` -> `GEMINI_IMAGE_GENERATE_MODEL`
- `gemini_image_edit_model` -> `GEMINI_IMAGE_EDIT_MODEL`
- `gemini_video_understanding_model` -> `GEMINI_VIDEO_UNDERSTANDING_MODEL`
- `gemini_audio_understanding_model` -> `GEMINI_AUDIO_UNDERSTANDING_MODEL`
- `gemini_reasoning_model` -> `GEMINI_REASONING_MODEL`
- `gemini_output_dir` -> `GEMINI_OUTPUT_DIR`
- `gemini_timeout_seconds` -> `GEMINI_TIMEOUT_SECONDS`
- `http_timeout_seconds` -> `MCP_GOOGLE_HTTP_TIMEOUT_SECONDS`
- `http_retries` -> `MCP_GOOGLE_HTTP_RETRIES`
- `log_level` -> `MCP_GOOGLE_LOG_LEVEL`
- `oauth_port` -> `MCP_GOOGLE_OAUTH_PORT`
- `oauth_open_browser` -> `MCP_GOOGLE_OAUTH_OPEN_BROWSER`

## Packaging

Create a local bundle archive:

```powershell
uv run python scripts/build_mcpb.py
```

The command writes `dist/mcp-google-workspace-<version>.mcpb`.

## Manual Validation

1. Run `pytest tests/test_bundle_manifest.py tests/test_bundle_runtime.py tests/test_auth_scopes.py tests/test_composition.py`.
2. Run `uv run python scripts/build_mcpb.py`.
3. Inspect the archive and confirm it contains `manifest.json`, `pyproject.toml`, and `src/`, but not credentials or `node_modules`.
4. Start the bundle entrypoint locally with `uv run python -m mcp_google_workspace.bundle_entry`.
5. Install the resulting `.mcpb` in an MCPB-capable host and verify that the host reads the manifest settings and can list tools over stdio.

## Notes

- The current upstream MCPB docs are internally inconsistent: `MANIFEST.md` still declares manifest spec `0.3`, while the same document marks `uv` support as experimental for `v0.4+` and shows a `manifest_version: "0.4"` example for `uv`. This bundle uses `0.4` so the manifest matches the documented `uv` runtime examples.
- Keep/Chat/Meet remain opt-in because their OAuth scopes are more deployment-sensitive than the core Workspace APIs.
- Gemini remains opt-in because it uses a separate Gemini Developer API key and capability-specific model defaults.
- Claude Desktop rejects extra `server` metadata keys beyond the current MCPB schema, so this manifest intentionally omits fields like `package_manager`, `python_version`, and `working_dir` even though some earlier MCPB materials referenced them.

