import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";
import type { McpUiHostContext } from "@modelcontextprotocol/ext-apps";
import { ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import * as adversarial from "./fixtures";

const params = new URLSearchParams(location.search);
const discovery = params.get("discovery");
const fixture = params.get("fixture");
const calls: string[] = [];
const cursors: Array<string | undefined> = [];
const openedLinks: string[] = [];
const defaultWeekly = {
  week_start: "2026-09-21",
  week_end: "2026-09-27",
  timezone: "UTC",
  total_events: 1,
  days: [{
    date: "2026-09-21",
    day_label: "Mon",
    is_today: false,
    all_day_events: [],
    timed_events: [{
      event_id: "test-event",
      calendar_id: "primary",
      title: "AppBridge regression meeting",
      start: "2026-09-21T10:00:00Z",
      end: "2026-09-21T11:00:00Z",
      all_day: false,
      status: "confirmed",
    }],
  }],
  fallback_text: "One test event",
};
const weekly = fixture === "adversarial" ? adversarial.weekly : defaultWeekly;
const hostContext: McpUiHostContext = params.has("styled") ? {
  theme: "light",
  styles: {
    variables: {
      "--color-background-primary": "rgb(240, 230, 220)",
      "--color-background-secondary": "rgb(230, 220, 210)",
      "--color-text-primary": "rgb(10, 20, 30)",
      "--font-sans": '"Host Sans", sans-serif',
      "--font-mono": '"Host Mono", monospace',
    },
    css: { fonts: '@font-face { font-family: "Host Sans"; src: local("Arial"); }' },
  },
  safeAreaInsets: { top: 1, right: 2, bottom: 3, left: 4 },
} : {};
const bridge = new AppBridge(
  null,
  { name: "Dashboard test host", version: "1.0.0" },
  fixture === "adversarial" ? { serverTools: {}, openLinks: {} } : { serverTools: {} },
  { hostContext },
);
bridge.onopenlink = async ({ url }) => {
  openedLinks.push(url);
  return {};
};

if (discovery !== "unsupported") {
  bridge.setRequestHandler(ListToolsRequestSchema, async (request) => {
    cursors.push(request.params?.cursor);
    if (discovery === "malformed") return { tools: "invalid catalog" };
    if (!request.params?.cursor) {
      return {
        tools: [{ name: "get_dashboard", inputSchema: { type: "object" } }],
        nextCursor: "second-page",
      };
    }
    if (discovery === "partial") throw new Error("Second page unavailable");
    return {
      tools: ["get_weekly_calendar_view", "list_calendars", "next_range"].map((name) => ({
        name, inputSchema: { type: "object" },
      })),
    };
  });
}

function adversarialResult(name: string): Record<string, unknown> {
  if (name.endsWith("get_dashboard")) return { dashboard: adversarial.dashboard, weekly_calendar: weekly };
  if (name.endsWith("list_calendars")) return adversarial.calendarCatalog;
  if (name.endsWith("get_event_detail")) {
    return params.has("safeConference") ? adversarial.safeConferenceEventDetail : adversarial.eventDetail;
  }
  if (name.endsWith("get_email_detail")) return adversarial.emailDetail;
  return { weekly_calendar: weekly };
}

// --- Server view-handle contract (mirrors apps/tools.py) ---------------------
// Launch tools mint a handle when called without one and return the view
// descriptor in _meta["mcp-google-workspace/view"] and structuredContent.view.
// State tools require the handle and honor expected_revision (CAS).
const VIEW_META_KEY = "mcp-google-workspace/view";
const scenario = params.get("view");
const callLog: Array<{ name: string; arguments: Record<string, unknown> }> = [];
const views = new Map<string, { revision: number; state: Record<string, unknown> }>();
let minted = 0;
const mintHandle = () => `wsv_${String(++minted).padStart(43, "0")}`;
const descriptor = (handle: string) => ({
  handle,
  revision: views.get(handle)!.revision,
  expires_at: 1_790_000_000,
  ttl_seconds: 86_400,
});
const viewResult = (handle: string, payload: Record<string, unknown>) => ({
  content: [],
  structuredContent: { ...payload, view: descriptor(handle) },
  _meta: { [VIEW_META_KEY]: descriptor(handle) },
});
const toolError = (code: string, message: string, extra: Record<string, unknown> = {}) => ({
  isError: true,
  content: [{ type: "text" as const, text: `${message} [code: ${code}]` }],
  structuredContent: { code, message, ...extra },
});
const invalidHandle = () =>
  toolError("view_handle_invalid", "The dashboard view handle is unknown or has expired.", {
    details: { reason: "unknown_or_expired" },
  });
let expiredOnce = scenario === "expired";

function openView(args: Record<string, unknown>): string | null {
  const requested = args.view_handle;
  if (typeof requested === "string") return views.has(requested) ? requested : null;
  const handle = mintHandle();
  views.set(handle, { revision: 1, state: { include_weekend: true, selected_calendars: ["primary"] } });
  return handle;
}

function stateCall(name: string, args: Record<string, unknown>) {
  const handle = args.view_handle;
  if (typeof handle !== "string" || !views.has(handle)) return invalidHandle();
  if (scenario === "expired-always" || (expiredOnce && handle === "wsv_" + "1".padStart(43, "0"))) {
    expiredOnce = false;
    views.delete(handle);
    return invalidHandle();
  }
  const record = views.get(handle)!;
  if (scenario === "conflict" && name.endsWith("patch_state")) {
    record.revision = 5;
    record.state = { ...record.state, include_weekend: true };
  }
  if (typeof args.expected_revision === "number" && args.expected_revision !== record.revision) {
    return {
      ...toolError("view_state_conflict", "The dashboard view changed.", {
        state: record.state,
        view: descriptor(handle),
      }),
      _meta: { [VIEW_META_KEY]: descriptor(handle) },
    };
  }
  record.revision += 1;
  if (name.endsWith("next_range")) record.state = { ...record.state, anchor_date: "2026-09-28" };
  if (name.endsWith("patch_state")) {
    const { view_handle: _handle, expected_revision: _revision, ...patch } = args;
    record.state = { ...record.state, ...patch };
  }
  return viewResult(handle, { state: record.state });
}

bridge.oncalltool = async ({ name, arguments: rawArgs }) => {
  const args = (rawArgs ?? {}) as Record<string, unknown>;
  calls.push(name);
  callLog.push({ name, arguments: args });
  if (fixture === "adversarial") {
    return { content: [], structuredContent: adversarialResult(name) };
  }
  if (name.endsWith("list_calendars")) {
    return { content: [], structuredContent: { items: [{ id: "primary", summary: "Test calendar", primary: true }] } };
  }
  if (name.endsWith("get_dashboard") || name.endsWith("get_weekly_calendar_view")) {
    const handle = openView(args);
    if (!handle) return invalidHandle();
    return viewResult(handle, { weekly_calendar: weekly });
  }
  if (/(patch_state|next_range|prev_range|today)$/.test(name)) {
    return stateCall(name, args);
  }
  return { content: [], structuredContent: { weekly_calendar: weekly } };
};

if (params.has("inputHandle")) {
  // The model reopened an existing view: its handle arrives as tool input and
  // the host never pushes a result, so the view loads through its fallback.
  bridge.oninitialized = async () => {
    await bridge.sendToolInput({ arguments: { view_handle: openView({})! } });
  };
}

if (params.has("pushResult")) {
  bridge.oninitialized = async () => {
    await bridge.sendToolInput({ arguments: {} });
    if (params.has("pushHandle")) {
      const handle = openView({})!;
      await bridge.sendToolResult(viewResult(handle, { weekly_calendar: weekly }));
      return;
    }
    await bridge.sendToolResult({ content: [], structuredContent: { weekly_calendar: weekly } });
  };
}

const iframe = document.createElement("iframe");
iframe.id = "dashboard";
iframe.style.cssText = "width: 1200px; height: 900px; border: 0";
document.body.appendChild(iframe);
Object.assign(window, { bridge, calls, callLog, cursors, openedLinks });
await bridge.connect(new PostMessageTransport(iframe.contentWindow!, iframe.contentWindow!));
// Exercise the shipped single-file bundle, including its actual SDK and CSS.
iframe.src = "/dist/index.html";
