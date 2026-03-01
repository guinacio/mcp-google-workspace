import { THEME_CSS, applyTheme } from "./theme";
import { RENDER_CSS, renderLoading, renderDashboard, setActionHandler } from "./render";
import type { DashboardData, ParentMessage } from "./types";

// ── Inject styles ───────────────────────────────────────────────────────────

const style = document.createElement("style");
style.textContent = THEME_CSS + RENDER_CSS;
document.head.appendChild(style);

// ── Root element ────────────────────────────────────────────────────────────

const root = document.getElementById("app")!;

// ── Mode detection ──────────────────────────────────────────────────────────

const params = new URLSearchParams(window.location.search);
const isStandalone =
  params.get("mode") === "standalone" ||
  document.documentElement.dataset.mcpMode === "standalone";

if (isStandalone) {
  initStandaloneMode();
} else {
  void initMcpMode();
}

// ── Standalone mode (Sentinel iframe) ───────────────────────────────────────

function initStandaloneMode() {
  applyTheme("dark");
  renderLoading(root);

  // Action handler: postMessage to parent
  setActionHandler((text: string) => {
    window.parent.postMessage({ type: "inject_chat_message", text }, "*");
  });

  // Listen for messages from parent
  window.addEventListener("message", (e: MessageEvent<ParentMessage>) => {
    if (!e.data || typeof e.data !== "object") return;

    switch (e.data.type) {
      case "dashboard_data": {
        const data = e.data.data as DashboardData;
        if (data && (data.briefing || data.weekly_calendar || data.dashboard)) {
          renderDashboard(root, data);
        } else {
          renderLoading(root);
        }
        break;
      }
      case "theme_changed": {
        applyTheme(e.data.theme);
        break;
      }
    }
  });

  // Request data from parent
  window.parent.postMessage({ type: "request_dashboard_data" }, "*");
}

// ── MCP mode (Claude Desktop / ext-apps host) ──────────────────────────────

async function initMcpMode() {
  applyTheme("dark");
  renderLoading(root);
  let hasRenderedFromToolResult = false;

  try {
    const {
      App,
      applyDocumentTheme,
      applyHostStyleVariables,
      applyHostFonts,
    } = await import("@modelcontextprotocol/ext-apps");

    const app = new App(
      { name: "Workspace Dashboard", version: "1.0.0" },
      {} // capabilities
    );

    // Action handler: call server tool to refresh
    setActionHandler((_text: string) => {
      void app.callServerTool({ name: "apps_get_dashboard", arguments: {} }).catch((err) => {
        console.warn("Failed to refresh dashboard via apps_get_dashboard:", err);
      });
    });

    // Handle tool results
    app.ontoolresult = (result) => {
      const data = extractDashboardData(result);
      if (data && (data.briefing || data.weekly_calendar || data.dashboard)) {
        hasRenderedFromToolResult = true;
        renderDashboard(root, data);
      }
    };

    // Handle host context (theme, styles, safe area) changes
    app.onhostcontextchanged = (ctx) => {
      if (ctx.theme) applyDocumentTheme(ctx.theme);
      if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
      if (ctx.styles?.css?.fonts) applyHostFonts(ctx.styles.css.fonts);
      if (ctx.safeAreaInsets) {
        const { top, right, bottom, left } = ctx.safeAreaInsets;
        document.body.style.padding = `${top}px ${right}px ${bottom}px ${left}px`;
      }
    };

    // Teardown handler
    app.onteardown = async () => ({});

    // Connect using host-provided MCP Apps transport.
    await app.connect();

    // Fallback: some hosts don't push initial tool results reliably.
    // Only fetch explicitly if nothing was rendered shortly after connect.
    window.setTimeout(async () => {
      if (hasRenderedFromToolResult) {
        return;
      }
      try {
        const initial = await app.callServerTool({ name: "apps_get_dashboard", arguments: {} });
        const initialData = extractDashboardData(initial);
        if (initialData && (initialData.briefing || initialData.weekly_calendar || initialData.dashboard)) {
          renderDashboard(root, initialData);
        }
      } catch (err) {
        console.warn("Fallback apps_get_dashboard call failed:", err);
      }
    }, 800);
  } catch (err) {
    // ext-apps host may be unavailable in local/standalone contexts.
    console.warn("MCP ext-apps not available:", err);
    root.innerHTML = `
      <div class="loading-state">
        <div class="loading-text">MCP app connection failed.</div>
        <div class="loading-hint">Open with ?mode=standalone for iframe parent integration.</div>
      </div>
    `;
  }
}

function extractDashboardData(result: unknown): DashboardData | null {
  if (!result || typeof result !== "object") {
    return null;
  }

  const candidate = result as {
    structuredContent?: unknown;
    data?: unknown;
    content?: Array<{ type?: string; text?: string }>;
  };

  if (candidate.structuredContent && typeof candidate.structuredContent === "object") {
    return normalizeDashboardData(candidate.structuredContent);
  }

  if (candidate.data && typeof candidate.data === "object") {
    return normalizeDashboardData(candidate.data);
  }

  const textContent = (candidate.content || []).find(
    (c) => c.type === "text" && typeof c.text === "string"
  );
  if (!textContent?.text) {
    return null;
  }
  try {
    return normalizeDashboardData(JSON.parse(textContent.text));
  } catch {
    return null;
  }
}

function normalizeDashboardData(raw: unknown): DashboardData | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }

  const obj = raw as Record<string, unknown>;

  if ("briefing" in obj || "weekly_calendar" in obj || "dashboard" in obj) {
    return obj as unknown as DashboardData;
  }

  // Direct morning briefing payload.
  if ("priorities" in obj && "conflicts" in obj && "prep_actions" in obj) {
    return { briefing: obj as unknown as DashboardData["briefing"] };
  }

  // Direct weekly view payload.
  if ("week_start" in obj && "week_end" in obj && "days" in obj) {
    return { weekly_calendar: obj as unknown as DashboardData["weekly_calendar"] };
  }

  // Direct dashboard view-model payload.
  if ("sections" in obj && "state" in obj) {
    return { dashboard: obj as unknown as DashboardData["dashboard"] };
  }

  return null;
}
