# W6 — Catalog contract diff (MCP Apps frontend)

Scope: `tests/contracts/catalog_default.json` and `tests/contracts/catalog_all_optional.json`,
regenerated deliberately with `uv run python scripts/snapshot_catalog.py` after W6. Base: the
snapshots at `2964300` (W3/W4a).

## Counts

No tool was added, removed or renamed. Templates, prompts and both `discovery_view` lists are
byte-identical.

| Configuration | Tools | Resources | Templates | Prompts | `discovery_view` |
| --- | --- | --- | --- | --- | --- |
| Default | 140 (unchanged) | 7 (unchanged) | 2 | 3 | 15 |
| All optional | 192 (unchanged) | 14 → **12** | 9 | 7 | 16 |

Only `_meta` changed on tools. Every change is intended.

## 1. Dashboard UI addressing — aliases removed, URI normalized

Owner decision (plan §9.1): no application-level backward compatibility.

| Item | Before | After |
| --- | --- | --- |
| `apps_get_dashboard`, `apps_get_weekly_calendar_view` `_meta` | `{"ui": {"resourceUri": "ui://apps/dashboard-ui"}, "ui/resourceUri": "ui://apps/dashboard-ui"}` | `{"ui": {"resourceUri": "ui://apps/dashboard-ui", "visibility": ["model", "app"]}}` |
| Resource `ui://apps/dashboard-ui` | served by the *legacy* registration `ui://dashboard-ui` + namespace (`apps_dashboard_ui_mcp_legacy`) | served by the one canonical registration (`apps_dashboard_ui_mcp`) |
| Resource `ui://apps/apps/dashboard-ui` | listed (double namespace, never referenced) | **removed** |
| Resource `apps://apps/dashboard/ui` (`text/html` copy of the Apps HTML) | listed | **removed** |

The subserver now declares its UI once, at `ui://dashboard-ui`, and its launch tools point there.
`mount_apps_dashboard(root, server, namespace="apps")` (used by `server.py` instead of `mount`)
applies the same namespace rewrite to the launch tools' `_meta.ui.resourceUri` that FastMCP applies
to the resource URI, so both compositions resolve:

| Composition | Tool `_meta.ui.resourceUri` | Listed / readable resource |
| --- | --- | --- |
| Subserver only (`apps_mcp`) | `ui://dashboard-ui` | `ui://dashboard-ui` |
| Root-composed (`server.py`) | `ui://apps/dashboard-ui` | `ui://apps/dashboard-ui` |

The flat `ui/resourceUri` key is also removed from `files_file_manager` (its nested
`_meta.ui.resourceUri`, `ui://prefab/tool/872d2c9e20b2/renderer.html`, is unchanged).

## 2. Tool visibility review

| Tool | Before | After |
| --- | --- | --- |
| `apps_get_dashboard`, `apps_get_weekly_calendar_view` | default (model + app) | explicit `["model", "app"]` |
| `apps_get_state`, `apps_set_state`, `apps_patch_state`, `apps_next_range`, `apps_prev_range`, `apps_today` | default (model + app) | `["app"]` |
| `apps_get_event_detail`, `apps_get_email_detail` | default (model + app) | `["app"]` |
| `apps_get_email_attachment` | `["app"]` | unchanged |
| `files_list_files`, `files_list_files_page`, `files_read_file` | `["app", "model"]` | `["model"]` |
| `files_file_manager` | `["model"]` | unchanged |
| `files_store_files` | `["app"]` | unchanged |
| `files_delete_file` | `["app", "model"]` | unchanged |

Justification: `docs/RICH_OUTPUTS.md`, "Tool visibility". App-only tools stay in `tools/list`
(FastMCP lists them; hosts filter). Visibility is a host hint, not authorization: every call is
still authorized by the server (principal-bound view handle, Google grant, confirmation gates).

## 3. Prefab picker resource CSP

```diff
 ui://prefab/tool/872d2c9e20b2/renderer.html  _meta.ui:
-  {"csp": {"resourceDomains": ["https://cdn.jsdelivr.net"]}}
+  {}
```

The resource now serves prefab-ui 0.20.2's self-contained bundled renderer (exact locked version)
instead of a stub that loads the renderer from jsDelivr, so it declares no CSP domains and the
host's restrictive default CSP applies. `get_mcp_apps_diagnostics.renderer_mode` reports
`bundled`. The browser suite renders it under that CSP with all external network blocked.

## Not visible in the catalog

- Launch results (`apps_get_dashboard`, `apps_get_weekly_calendar_view`) now carry the operation
  manifest in `_meta["mcp-google-workspace/operations"]` (result metadata, not a definition
  change). Format: `docs/RICH_OUTPUTS.md`, "Operation manifest".
- No input or output schema changed.
