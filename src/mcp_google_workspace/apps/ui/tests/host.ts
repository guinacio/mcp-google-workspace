/**
 * Policy-enforcing MCP Apps test host (web-host architecture of the stable Apps spec).
 *
 * This page (origin http://127.0.0.1:4173) embeds the sandbox proxy served by
 * tests/sandbox-server.ts from a different origin (http://localhost:4174). The
 * proxy loads the view in a sandboxed inner iframe whose CSP response header is
 * built from the resource's declared `_meta.ui.csp` (see sandbox-server.ts).
 * The host side is the official `AppBridge` with a mock server behind it that
 * mirrors apps/tools.py and apps/operations.py (view handles, revisions and the
 * operation manifest), and it enforces the server's real tool visibility
 * (exported by scripts/export_apps_ui_fixtures.py): app calls to model-only
 * tools are rejected as the spec requires of hosts.
 *
 * Query parameters select host behavior; see each `params` read below.
 */
import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";
import type {
  McpUiHostCapabilities,
  McpUiHostContext,
  McpUiResourceCsp,
} from "@modelcontextprotocol/ext-apps/app-bridge";
import { ProtocolError } from "@modelcontextprotocol/client";
import type { CallToolResult } from "@modelcontextprotocol/client";
import * as adversarial from "./fixtures";

const SANDBOX_ORIGIN = "http://localhost:4174";
const VIEW_META_KEY = "mcp-google-workspace/view";
const OPERATIONS_META_KEY = "mcp-google-workspace/operations";

interface ServerFixture {
  tools: Record<string, string[]>;
  dashboard: { uri: string; mimeType: string; ui: { csp?: McpUiResourceCsp } };
  picker: {
    uri: string;
    mimeType: string;
    ui: { csp?: McpUiResourceCsp };
    html: string;
    toolResult: CallToolResult;
  };
}

const params = new URLSearchParams(location.search);
const appKind = params.get("app") ?? "dashboard";
const discovery = params.get("discovery");
const fixture = params.get("fixture");
/** Subserver-only composition: dashboard tools under their local names. */
const localNames = discovery === "supported" || discovery === "partial" || params.get("names") === "local";

// --- Recorded interactions (read by the tests) --------------------------------
const calls: string[] = [];
const callLog: Array<{ name: string; arguments: Record<string, unknown> }> = [];
const rejectedCalls: string[] = [];
const cursors: Array<string | undefined> = [];
const openedLinks: string[] = [];
const downloads: unknown[] = [];
const messages: unknown[] = [];
const modelContexts: unknown[] = [];
const sizeChanges: Array<{ width?: number; height?: number }> = [];
const displayModeRequests: string[] = [];
let hostMinted = 0;
let lateResultsSent = 0;

const serverFixture = (await (await fetch("/tests/generated/apps-server.json")).json()) as ServerFixture;

// --- Host capabilities and context ---------------------------------------------
function capabilities(): McpUiHostCapabilities {
  const caps = params.get("caps") ?? "full";
  if (caps === "none") return {};
  if (caps === "reduced") return { serverTools: {} };
  return {
    serverTools: {},
    openLinks: {},
    downloadFile: {},
    message: { text: {} },
    updateModelContext: { text: {} },
  };
}

function initialContext(): McpUiHostContext {
  const context: McpUiHostContext = {};
  if (params.has("styled")) {
    Object.assign(context, {
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
    });
  }
  if (params.get("theme") === "light" || params.get("theme") === "dark") {
    context.theme = params.get("theme") as "light" | "dark";
  }
  const sizing = params.get("sizing");
  if (sizing === "fixed") context.containerDimensions = { height: 420, width: Number(params.get("frameWidth") ?? 1200) };
  if (sizing === "flexible") context.containerDimensions = { maxHeight: 2000 };
  if (params.has("fullscreen")) {
    context.availableDisplayModes = ["inline", "fullscreen"];
    context.displayMode = "inline";
  }
  if (params.get("launch") === "weekly") {
    context.toolInfo = {
      tool: { name: "apps_get_weekly_calendar_view", inputSchema: { type: "object" } },
    };
  }
  return context;
}

const bridge = new AppBridge(null, { name: "Dashboard sandbox test host", version: "2.0.0" }, capabilities(), {
  hostContext: initialContext(),
});

// --- Optional host actions ---------------------------------------------------------
bridge.onopenlink = async ({ url }) => {
  const mode = params.get("openLink");
  if (mode === "reject") throw new Error("Popups are blocked by this host.");
  if (mode === "decline") return { isError: true };
  openedLinks.push(url);
  return {};
};
bridge.ondownloadfile = async (request) => {
  const mode = params.get("download");
  if (mode === "reject") throw new Error("Downloads are blocked by this host.");
  downloads.push(request);
  return mode === "decline" ? { isError: true } : {};
};
bridge.onmessage = async (request) => {
  messages.push(request);
  return params.get("message") === "decline" ? { isError: true } : {};
};
bridge.onupdatemodelcontext = async (request) => {
  modelContexts.push(request);
  return {};
};
bridge.onrequestdisplaymode = async ({ mode }) => {
  displayModeRequests.push(mode);
  return { mode };
};
bridge.onsizechange = (size) => {
  sizeChanges.push(size);
};

// --- Tool catalog (tools/list), method-keyed SDK 2 handler ---------------------------
const toolName = (local: string, namespace = "apps") =>
  namespace === "apps" && localNames ? local : `${namespace}_${local}`;

if (discovery !== "unsupported") {
  bridge.setRequestHandler("tools/list", async (request) => {
    const cursor = request.params?.cursor;
    cursors.push(cursor);
    if (discovery === "malformed") return { tools: "invalid catalog" } as never;
    if (!cursor) {
      return {
        tools: [{ name: toolName("get_dashboard"), inputSchema: { type: "object" as const } }],
        nextCursor: "second-page",
      };
    }
    if (discovery === "partial") throw new Error("Second page unavailable");
    return {
      tools: [toolName("get_weekly_calendar_view"), "calendar_list_calendars", toolName("next_range")].map((name) => ({
        name,
        inputSchema: { type: "object" as const },
      })),
    };
  });
}

// --- Server emulation (mirrors apps/tools.py + apps/operations.py) -------------------
const scenario = params.get("view");
const views = new Map<string, { revision: number; state: Record<string, unknown> }>();
let minted = 0;
const mintHandle = () => `wsv_${String(++minted).padStart(43, "0")}`;
const descriptor = (handle: string) => ({
  handle,
  revision: views.get(handle)!.revision,
  expires_at: 1_790_000_000,
  ttl_seconds: 86_400,
});

const OPERATIONS: Record<string, { namespace: string; tool: string; mutates: boolean; capability?: string }> = {
  getDashboard: { namespace: "apps", tool: "get_dashboard", mutates: false },
  getWeeklyCalendar: { namespace: "apps", tool: "get_weekly_calendar_view", mutates: false, capability: "calendar" },
  getEventDetail: { namespace: "apps", tool: "get_event_detail", mutates: false, capability: "calendar" },
  getEmailDetail: { namespace: "apps", tool: "get_email_detail", mutates: false, capability: "gmail" },
  getEmailAttachment: { namespace: "apps", tool: "get_email_attachment", mutates: false, capability: "gmail" },
  listCalendars: { namespace: "calendar", tool: "list_calendars", mutates: false, capability: "calendar" },
  patchState: { namespace: "apps", tool: "patch_state", mutates: true },
  nextRange: { namespace: "apps", tool: "next_range", mutates: true },
  prevRange: { namespace: "apps", tool: "prev_range", mutates: true },
  today: { namespace: "apps", tool: "today", mutates: true },
  respondToEvent: { namespace: "calendar", tool: "respond_to_event", mutates: true, capability: "calendar" },
  createEvent: { namespace: "calendar", tool: "create_event", mutates: true, capability: "calendar" },
  updateEvent: { namespace: "calendar", tool: "update_event", mutates: true, capability: "calendar" },
  deleteEvent: { namespace: "calendar", tool: "delete_event", mutates: true, capability: "calendar" },
  markEmailRead: { namespace: "gmail", tool: "mark_as_read", mutates: true, capability: "gmail" },
  markEmailUnread: { namespace: "gmail", tool: "mark_as_unread", mutates: true, capability: "gmail" },
  moveEmail: { namespace: "gmail", tool: "move_email", mutates: true, capability: "gmail" },
  deleteEmail: { namespace: "gmail", tool: "delete_email", mutates: true, capability: "gmail" },
  untrashEmail: { namespace: "gmail", tool: "untrash_email", mutates: true, capability: "gmail" },
  markEmailSpam: { namespace: "gmail", tool: "mark_as_spam", mutates: true, capability: "gmail" },
  markEmailNotSpam: { namespace: "gmail", tool: "mark_as_not_spam", mutates: true, capability: "gmail" },
};

/** Operation manifest as the server would issue it for this composition and grant. */
function manifest(): Record<string, unknown> | undefined {
  const mode = params.get("manifest") ?? "full";
  if (mode === "none") return undefined;
  const grants = new Set((params.get("grants") ?? "calendar,gmail").split(",").filter(Boolean));
  const operations: Record<string, { tool: string; mutates: boolean }> = {};
  for (const [operation, spec] of Object.entries(OPERATIONS)) {
    if (mode === "reads" && spec.mutates) continue;
    if (spec.capability && !grants.has(spec.capability)) continue;
    if (localNames && spec.namespace !== "apps") continue; // Not composed in a subserver-only server.
    operations[operation] = { tool: toolName(spec.tool, spec.namespace), mutates: spec.mutates };
  }
  return { version: 1, operations };
}

const launchResult = (handle: string, payload: Record<string, unknown>): CallToolResult => {
  const meta: Record<string, unknown> = { [VIEW_META_KEY]: descriptor(handle) };
  const operations = manifest();
  if (operations) meta[OPERATIONS_META_KEY] = operations;
  return {
    content: [{ type: "text", text: "Workspace dashboard" }],
    structuredContent: { ...payload, view: descriptor(handle) },
    _meta: meta,
  };
};
const stateResult = (handle: string, payload: Record<string, unknown>): CallToolResult => ({
  content: [],
  structuredContent: { ...payload, view: descriptor(handle) },
  _meta: { [VIEW_META_KEY]: descriptor(handle) },
});
const toolError = (code: string, message: string, extra: Record<string, unknown> = {}): CallToolResult => ({
  isError: true,
  content: [{ type: "text", text: message }],
  structuredContent: { code, message, ...extra },
});
const invalidHandle = () =>
  toolError("view_handle_invalid", "The dashboard view handle is unknown or has expired.", {
    details: { reason: "unknown_or_expired" },
  });
let expiredOnce = scenario === "expired";

const defaultWeekly = (title = "AppBridge regression meeting", weekStart = "2026-09-21") => ({
  week_start: weekStart,
  week_end: "2026-09-27",
  timezone: "UTC",
  total_events: 1,
  days: [{
    date: weekStart,
    day_label: "Mon",
    is_today: false,
    all_day_events: [],
    timed_events: [{
      event_id: "test-event",
      calendar_id: "primary",
      title,
      start: `${weekStart}T10:00:00Z`,
      end: `${weekStart}T11:00:00Z`,
      all_day: false,
      status: "confirmed",
    }],
  }],
  fallback_text: "One test event",
});
const weekly = fixture === "adversarial" ? adversarial.weekly : defaultWeekly();
const inbox = {
  title: "Workspace",
  generated_at_utc: "2026-09-21T09:00:00Z",
  state: { include_weekend: true, selected_calendars: ["primary"], timezone: "UTC" },
  sections: [{
    id: "communications",
    title: "Inbox",
    fallback_text: "Inbox",
    cards: [{
      id: "inbox",
      title: "Inbox",
      card_type: "inbox",
      summary: "1 unread",
      fallback_text: "1 unread",
      actions: [],
      data: {
        unread_count: 1,
        unread_message_ids: ["msg-a"],
        messages: [
          { id: "msg-a", subject: "Quarterly plan", from: "Alice <alice@example.com>", date: "2026-09-21", snippet: "Plan", label_ids: ["INBOX", "UNREAD"], is_unread: true },
          { id: "msg-b", subject: "Lunch", from: "Bob <bob@example.com>", date: "2026-09-21", snippet: "Lunch?", label_ids: ["INBOX"], is_unread: false },
        ],
      },
    }],
  }],
  warnings: [],
  section_errors: {},
};
const emailDetail = (id: string) => ({
  message_id: id,
  thread_id: `thread-${id}`,
  subject: id === "msg-a" ? "Quarterly plan" : "Lunch",
  from_value: id === "msg-a" ? "Alice <alice@example.com>" : "Bob <bob@example.com>",
  to: "me@example.com",
  date: "2026-09-21",
  text_body: `Body of ${id}`,
  html_body: null,
  attachments: [
    { filename: "plan.pdf", mime_type: "application/pdf", size: 12, attachment_id: "att-small" },
    { filename: "huge.zip", mime_type: "application/zip", size: 50 * 1024 * 1024, attachment_id: "att-huge" },
  ],
  labels: id === "msg-a" ? ["INBOX", "UNREAD"] : ["INBOX"],
  is_unread: id === "msg-a",
});
const withInbox = params.has("inbox");

function openView(args: Record<string, unknown>): string | null {
  const requested = args.view_handle;
  if (typeof requested === "string") return views.has(requested) ? requested : null;
  const handle = mintHandle();
  views.set(handle, { revision: 1, state: { include_weekend: true, selected_calendars: ["primary"] } });
  return handle;
}

function launchPayload(name: string): Record<string, unknown> {
  if (name.endsWith("get_weekly_calendar_view")) return { ...weekly };
  return withInbox ? { ...inbox, weekly_calendar: weekly } : { weekly_calendar: weekly };
}

function stateCall(name: string, args: Record<string, unknown>): CallToolResult {
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
  return stateResult(handle, { state: record.state });
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

/** `delay=<tool suffix>:<ms>[:<argument value>]`, repeatable. */
function delayFor(name: string, args: Record<string, unknown>): number {
  for (const spec of params.getAll("delay")) {
    const [suffix, ms, value] = spec.split(":");
    if (!name.endsWith(suffix)) continue;
    if (value && !Object.values(args).includes(value)) continue;
    return Number(ms);
  }
  return 0;
}

/** `fail=<tool suffix>:<iserror|reject|embedded>[:<code>]`. */
function failureFor(name: string): CallToolResult | null {
  for (const spec of params.getAll("fail")) {
    const [suffix, kind, code = "provider_error"] = spec.split(":");
    if (!name.endsWith(suffix)) continue;
    if (kind === "reject") {
      throw new ProtocolError(-32000, `Server rejected ${name}`, { code, message: `Server rejected ${name} [${code}]` });
    }
    if (kind === "embedded") {
      return { content: [], structuredContent: { error: { code: "PROVIDER_ERROR", message: "Provider exploded", retryable: false } } };
    }
    return toolError(code, `The server could not run ${name}.`);
  }
  return null;
}

/** The server's real visibility, keyed by composed tool name. */
function visibilityOf(name: string): string[] {
  // `modelOnly=<suffix>` simulates a tool that became model-only after the
  // manifest was issued (or a manifest the host does not trust).
  if (params.getAll("modelOnly").some((suffix) => name.endsWith(suffix))) return ["model"];
  // Local (subserver-only) dashboard names map onto the composed catalog entry.
  return serverFixture.tools[name] ?? serverFixture.tools[`apps_${name}`] ?? ["model", "app"];
}

bridge.oncalltool = async ({ name, arguments: rawArgs }) => {
  const args = (rawArgs ?? {}) as Record<string, unknown>;
  if (!visibilityOf(name).includes("app")) {
    rejectedCalls.push(name);
    throw new ProtocolError(-32602, `Tool ${name} is not available to apps (visibility).`);
  }
  calls.push(name);
  callLog.push({ name, arguments: args });
  const delay = delayFor(name, args);
  if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
  const failure = failureFor(name);
  if (failure) return failure;
  if (fixture === "adversarial") {
    const operations = manifest();
    const launch = name.endsWith("get_dashboard") || name.endsWith("get_weekly_calendar_view");
    return {
      content: [],
      structuredContent: adversarialResult(name),
      ...(launch && operations ? { _meta: { [OPERATIONS_META_KEY]: operations } } : {}),
    };
  }
  if (name.endsWith("list_calendars")) {
    return { content: [], structuredContent: { items: [{ id: "primary", summary: "Test calendar", primary: true }] } };
  }
  if (name.endsWith("get_dashboard") || name.endsWith("get_weekly_calendar_view")) {
    const handle = openView(args);
    if (!handle) return invalidHandle();
    return launchResult(handle, launchPayload(name));
  }
  if (/(patch_state|next_range|prev_range|today)$/.test(name)) {
    return stateCall(name, args);
  }
  if (name.endsWith("get_email_detail")) {
    return { content: [], structuredContent: emailDetail(String(args.message_id)) };
  }
  if (name.endsWith("get_email_attachment")) {
    return {
      content: [],
      structuredContent: { filename: "plan.pdf", mime_type: "application/pdf", size: 12, blob_base64: btoa("%PDF-1.4 test") },
    };
  }
  if (name.endsWith("get_event_detail")) {
    return {
      content: [],
      structuredContent: {
        event_id: String(args.event_id), calendar_id: String(args.calendar_id), title: `Detail of ${args.event_id}`,
        start: "2026-09-21T10:00:00Z", end: "2026-09-21T11:00:00Z", status: "confirmed", attendees: [], attachments: [],
      },
    };
  }
  return { content: [{ type: "text", text: "ok" }], structuredContent: { status: "ok" } };
};

// --- Invocation lifecycle driven by the host -------------------------------------------
/** The host's own launch call on behalf of the model (not a view callback). */
function hostLaunch(): CallToolResult {
  hostMinted += 1;
  const handle = openView({})!;
  return launchResult(handle, launchPayload(params.get("launch") === "weekly" ? "get_weekly_calendar_view" : "get_dashboard"));
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const invocation = params.get("invocation");

bridge.oninitialized = async () => {
  if (appKind === "prefab") {
    await bridge.sendToolInput({ arguments: {} });
    await bridge.sendToolResult(serverFixture.picker.toolResult);
    return;
  }
  if (params.has("inputHandle")) {
    // The model reopened an existing view: its handle arrives as tool input and
    // the host never pushes a result, so the view loads that handle itself.
    await bridge.sendToolInput({ arguments: { view_handle: openView({})! } });
    return;
  }
  if (params.has("pushResult")) {
    await bridge.sendToolInput({ arguments: {} });
    if (params.has("pushHandle")) {
      await bridge.sendToolResult(hostLaunch());
      return;
    }
    await bridge.sendToolResult({ content: [], structuredContent: { weekly_calendar: weekly } });
    return;
  }
  if (invocation === "input-first") {
    // Conforming host: input now, the launch result when the tool finishes.
    await bridge.sendToolInput({ arguments: {} });
    await sleep(Number(params.get("resultDelay") ?? 1500));
    await bridge.sendToolResult(hostLaunch());
    return;
  }
  if (invocation === "result-first") {
    await bridge.sendToolResult(hostLaunch());
    await bridge.sendToolInput({ arguments: {} });
    return;
  }
  if (invocation === "input-only") {
    await bridge.sendToolInput({ arguments: {} });
    return;
  }
  if (invocation === "cancelled") {
    await bridge.sendToolInput({ arguments: {} });
    await sleep(200);
    await bridge.sendToolCancelled({ reason: "User stopped the tool call." });
    return;
  }
  if (invocation === "error") {
    await bridge.sendToolInput({ arguments: {} });
    await bridge.sendToolResult(invalidHandle());
  }
};

/** Teardown then deliver a late result, as a racing host would. */
async function teardownAndSendLateResult() {
  await bridge.teardownResource({});
  lateResultsSent += 1;
  await bridge.sendToolResult(launchResult(openView({})!, { weekly_calendar: defaultWeekly("Late result after teardown") }));
}

async function pushToolResult(title: string) {
  await bridge.sendToolResult(launchResult(openView({})!, { weekly_calendar: defaultWeekly(title) }));
}

// --- Embed the sandbox proxy and hand it the UI resource --------------------------------
const frame = document.createElement("iframe");
frame.id = "dashboard";
frame.title = "MCP App sandbox";
frame.setAttribute("sandbox", "allow-scripts allow-same-origin");
const frameWidth = Number(params.get("frameWidth") ?? 1200);
frame.style.cssText = `width: ${frameWidth}px; height: ${params.get("sizing") === "fixed" ? 420 : 900}px; border: 0`;
document.body.appendChild(frame);

bridge.onsandboxready = async () => {
  if (appKind === "prefab") {
    await bridge.sendSandboxResourceReady({ html: serverFixture.picker.html, csp: serverFixture.picker.ui.csp });
    return;
  }
  // Exercise the shipped single-file bundle, including its actual SDK and CSS
  // (raw bytes: Vite must not transform it as a page of its own).
  const { default: html } = await import("../dist/index.html?raw");
  await bridge.sendSandboxResourceReady({ html, csp: serverFixture.dashboard.ui.csp });
};

Object.assign(window, {
  bridge,
  calls,
  callLog,
  rejectedCalls,
  cursors,
  openedLinks,
  downloads,
  messages,
  modelContexts,
  sizeChanges,
  displayModeRequests,
  hostMintCount: () => hostMinted,
  lateResultCount: () => lateResultsSent,
  mintedViews: () => minted,
  teardownAndSendLateResult,
  pushToolResult,
  serverFixture,
});
await bridge.connect(new PostMessageTransport(frame.contentWindow!, frame.contentWindow!));
frame.src = `${SANDBOX_ORIGIN}/sandbox.html`;
