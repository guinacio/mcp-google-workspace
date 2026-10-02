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
- encrypted, per-principal Google OAuth token storage
- a self-contained Prefab renderer with no runtime CDN dependency
- the complete tool catalog for Claude Desktop's current MCP Apps router
- clearer startup errors when bundle configuration is invalid

## Token encryption

The bundle asks for no encryption key. On first use it generates a Fernet key and stores it in the OS keychain (service `mcp-google-workspace`, account `token-encryption-key`) through `keyring`: Windows Credential Manager, macOS Keychain, or the Linux Secret Service. Encrypted Google tokens stay in `user_token_dir`; the key never touches disk. Without a secure keychain backend the server refuses to start and asks for `MCP_TOKEN_ENCRYPTION_KEY` in its environment. Deleting the keychain entry forces a one-time Google reconnect.

## User Configuration Mapped By The Manifest

The MCPB manifest exposes these settings through the host UI and passes them into the local process as environment variables:

- `credentials_dir` -> `MCP_CREDENTIALS_DIR`
- `user_token_dir` -> `MCP_USER_TOKEN_DIR`
- `local_principal` -> `MCP_LOCAL_PRINCIPAL` (the one trusted-local principal that owns this
  process's Google grant, picker uploads, and dashboard views; it is a single-user trust
  boundary, not multitenant isolation — see "Local stdio trust boundary" in the README)
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

The manifest also sets fixed host compatibility values:

- `MCP_CLIENT_MODEL=claude`, which disables FastMCP progressive Tool Search in auto mode
- `PREFAB_BUNDLED_RENDERER=1`, which serves the picker renderer as self-contained HTML

## Packaging

Create a local bundle archive:

```powershell
uv run python scripts/build_mcpb.py
```

The command first runs `npm ci` and `npm run build` for the Apps UI, then writes
`dist/mcp-google-workspace-<version>.mcpb`. Packaging fails when the locked UI
cannot be rebuilt.

## Automated Validation (CI)

The `mcpb-lifecycle` job in `.github/workflows/ci.yml` runs
`uv run python scripts/verify_mcpb_bundle.py` on every push/PR from a clean
checkout: it builds the `.mcpb`, extracts it into an isolated directory
(proving the packaged artifact is self-sufficient, not just the repo
checkout), and drives the extracted copy's stdio entrypoint through a real
MCP client — start, list tools, call a safe tool, the Workspace Files
picker's store/list/read/delete callbacks across independent MCP 2026-07-28
requests, close, and reconnect to the same kept-alive subprocess. Run it
locally the same way: `uv run python scripts/verify_mcpb_bundle.py`
(add `--bundle path/to/existing.mcpb` to reuse an already-built archive).

## Manual Validation

1. Run `pytest tests/test_bundle_manifest.py tests/test_bundle_runtime.py tests/test_auth_scopes.py tests/test_composition.py tests/test_prefab_render_cache.py`.
2. Run `uv run python scripts/build_mcpb.py`.
3. Inspect the archive and confirm it contains `manifest.json`, `pyproject.toml`, and `src/`, but not credentials or `node_modules`.
4. Start the bundle entrypoint locally with `uv run python -m mcp_google_workspace.bundle_entry`.
5. Install the resulting `.mcpb` in an MCPB-capable host and verify that the host reads the manifest settings and can list tools over stdio.
6. Call `get_mcp_apps_diagnostics` with `run_self_test=true`, then open `files_file_manager` and upload a real file.

Steps 2–5 are also covered automatically; see "Automated Validation" above. Manual validation with a real host remains necessary for anything the CI's mock host cannot exercise — see `docs/migration/W7_MANUAL_QUALIFICATION.md`.

## Notes

- The current upstream MCPB docs are internally inconsistent: `MANIFEST.md` still declares manifest spec `0.3`, while the same document marks `uv` support as experimental for `v0.4+` and shows a `manifest_version: "0.4"` example for `uv`. This bundle uses `0.4` so the manifest matches the documented `uv` runtime examples.
- Keep/Chat/Meet remain opt-in because their OAuth scopes are more deployment-sensitive than the core Workspace APIs.
- Gemini remains opt-in because it uses a separate Gemini Developer API key and capability-specific model defaults.
- Claude Desktop rejects extra `server` metadata keys beyond the current MCPB schema, so this manifest intentionally omits fields like `package_manager`, `python_version`, and `working_dir` even though some earlier MCPB materials referenced them.

