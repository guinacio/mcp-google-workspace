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

bridge.oncalltool = async ({ name }) => {
  calls.push(name);
  if (fixture === "adversarial") {
    return { content: [], structuredContent: adversarialResult(name) };
  }
  const structuredContent = name.endsWith("list_calendars")
    ? { items: [{ id: "primary", summary: "Test calendar", primary: true }] }
    : name.endsWith("next_range")
      ? { anchor_date: "2026-09-28" }
      : { weekly_calendar: weekly };
  return { content: [], structuredContent };
};

if (params.has("pushResult")) {
  bridge.oninitialized = async () => {
    await bridge.sendToolInput({ arguments: {} });
    await bridge.sendToolResult({ content: [], structuredContent: { weekly_calendar: weekly } });
  };
}

const iframe = document.createElement("iframe");
iframe.id = "dashboard";
iframe.style.cssText = "width: 1200px; height: 900px; border: 0";
document.body.appendChild(iframe);
Object.assign(window, { bridge, calls, cursors, openedLinks });
await bridge.connect(new PostMessageTransport(iframe.contentWindow!, iframe.contentWindow!));
// Exercise the shipped single-file bundle, including its actual SDK and CSS.
iframe.src = "/dist/index.html";
