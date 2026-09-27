import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Export the real server's Apps metadata (tool visibility, UI resource CSP, the
 * Prefab picker renderer and its tool result) for the sandbox host. Requires the
 * repository's locked Python environment (`uv sync --frozen`); it fails loudly
 * rather than skipping when that is missing.
 */
export default function globalSetup() {
  const here = dirname(fileURLToPath(import.meta.url));
  const repoRoot = resolve(here, "../../../../..");
  const output = resolve(here, "generated/apps-server.json");
  execFileSync("uv", ["run", "--frozen", "python", "scripts/export_apps_ui_fixtures.py", output], {
    cwd: repoRoot,
    stdio: "inherit",
  });
}
