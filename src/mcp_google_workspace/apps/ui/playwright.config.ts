import { defineConfig } from "@playwright/test";

/**
 * Two origins: the test host page (Vite, 127.0.0.1:4173) and the MCP Apps
 * sandbox proxy (localhost:4174, tests/sandbox-server.ts). Every host test runs
 * the view inside the proxy's sandboxed, CSP-enforced inner iframe.
 */
export default defineConfig({
  testDir: "./tests",
  globalSetup: "./tests/global-setup.ts",
  use: { baseURL: "http://127.0.0.1:4173" },
  webServer: [
    {
      command: "npm run dev -- --host 127.0.0.1 --port 4173 --strictPort",
      url: "http://127.0.0.1:4173",
    },
    {
      command: "node tests/sandbox-server.ts",
      url: "http://localhost:4174/",
    },
  ],
});
