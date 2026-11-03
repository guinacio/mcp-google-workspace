/**
 * Workspace dashboard MCP App (MCP Apps SDK 2, stable Apps protocol 2026-01-26).
 *
 * Lifecycle (see docs/RICH_OUTPUTS.md, "Dashboard lifecycle"):
 * 1. Every host notification handler (tool input, tool result, cancellation,
 *    host-context change, teardown, channel error) is registered before
 *    `connect()` performs the `ui/initialize` handshake.
 * 2. The host's tool input/result are the authoritative invocation context. The
 *    view renders the launch result; it loads on its own only when the host never
 *    announced an invocation, or announced one that reopens an existing handle.
 *    It never mints a second view while the launch call is minting one.
 * 3. Host capabilities (`getHostCapabilities()`) gate every optional action; the
 *    server's operation manifest gates every server operation that writes.
 * 4. Loads carry generation tickets (latest wins) and abort signals; teardown
 *    cancels timers, requests and listeners and ignores anything that arrives later.
 */
import type { App, McpUiDownloadFileRequest, McpUiHostContext } from "@modelcontextprotocol/ext-apps";
import type { CallToolResult } from "@modelcontextprotocol/client";
import { THEME_CSS, applyTheme } from "./theme";
import {
  RENDER_CSS,
  detachDashboardHandlers,
  renderDashboard,
  renderLoading,
  setActionHandler,
} from "./render";
import { safeExternalUrl } from "./urls";
import type { LinkKind } from "./urls";
import { VIEW_HANDLE_INVALID, VIEW_STATE_CONFLICT, ViewSession } from "./view-handle";
import {
  ToolCallError,
  classifyRejection,
  describeFailure,
  hasEmbeddedError,
  isUnknownToolError,
  requireSuccess,
  toolResultErrorMessage,
} from "./tool-calls";
import { OperationRegistry, isReadOperation } from "./operations";
import type { Operation } from "./operations";
import {
  MAX_INLINE_DOWNLOAD_BYTES,
  NO_HOST_SUPPORT,
  base64DecodedLength,
  hostFeatures,
  readHostSupport,
} from "./host-support";
import type { HostSupport } from "./host-support";
import { ViewLifecycle } from "./lifecycle";
import type { UiAction, RenderOptions } from "./render";
import type {
  CalendarCatalogItem,
  DashboardData,
  EventEditorDraft,
  IframeMessage,
  ParentMessage,
  UiToolCapabilities,
} from "./types";

/** Dashboard callbacks that carry the server-issued view handle. */
type ViewOperation =
  | "getDashboard"
  | "getWeeklyCalendar"
  | "getEventDetail"
  | "getEmailDetail"
  | "getEmailAttachment"
  | "patchState"
  | "nextRange"
  | "prevRange"
  | "today";

/** Launch operations mint a new view when called without a handle. */
const LAUNCH_OPERATIONS: ReadonlySet<ViewOperation> = new Set(["getDashboard", "getWeeklyCalendar"]);
/** State writes are conditional on the last revision this view observed. */
const CONDITIONAL_OPERATIONS: ReadonlySet<ViewOperation> = new Set([
  "patchState",
  "nextRange",
  "prevRange",
  "today",
]);
/** Launch-tool arguments replayed when the view loads without a host result. */
const LAUNCH_ARGUMENTS = ["date_override", "include_weekend"] as const;

/** No tool input at all after the handshake: load a view of our own after this. */
const NO_INVOCATION_GRACE_MS = 750;
/** Input reopened an existing handle but no result arrived: load that handle after this. */
const INPUT_HANDLE_GRACE_MS = 1500;
/** Input without a handle and still no result: tell the user (never mint automatically). */
const RESULT_WAIT_NOTICE_MS = 30_000;
const MAX_DISCOVERY_PAGES = 5;

const NO_TOOL_CAPABILITIES: UiToolCapabilities = Object.freeze({
  can_create_event: false,
  can_edit_event: false,
  can_delete_event: false,
  can_rsvp: false,
  can_reschedule_event: false,
  can_navigate: false,
  can_toggle_weekend: false,
  can_select_calendars: false,
  can_mark_email_read: false,
  can_mark_email_unread: false,
  can_archive_email: false,
  can_trash_email: false,
  can_untrash_email: false,
  can_mark_email_spam: false,
  can_mark_email_not_spam: false,
  can_open_event_detail: false,
  can_open_email_detail: false,
  can_fetch_email_attachment: false,
});

type ViewCaller = (
  operation: ViewOperation,
  args?: Record<string, unknown>,
  signal?: AbortSignal,
) => Promise<CallToolResult>;

/** Raised after a stale state write; the view has already been refreshed. */
class ViewRefreshedAfterConflict extends Error {
  constructor() {
    super("This dashboard view changed elsewhere and was refreshed. Try again.");
    this.name = "ViewRefreshedAfterConflict";
  }
}

const style = document.createElement("style");
style.textContent = THEME_CSS + RENDER_CSS;
document.head.appendChild(style);

const root = document.getElementById("app")!;
const MIME_EXTENSION_MAP: Record<string, string> = {
  "application/json": ".json",
  "application/msword": ".doc",
  "application/octet-stream": ".bin",
  "application/pdf": ".pdf",
  "application/rtf": ".rtf",
  "application/vnd.ms-excel": ".xls",
  "application/vnd.ms-powerpoint": ".ppt",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
  "application/zip": ".zip",
  "audio/mpeg": ".mp3",
  "image/gif": ".gif",
  "image/jpeg": ".jpg",
  "image/png": ".png",
  "image/svg+xml": ".svg",
  "text/calendar": ".ics",
  "text/csv": ".csv",
  "text/html": ".html",
  "text/plain": ".txt",
};

const params = new URLSearchParams(window.location.search);
// The standalone postMessage bridge is a nonstandard development preview channel. It is
// compiled out of production builds (import.meta.env.DEV is false there), so the shipped
// artifact only speaks the official MCP Apps protocol.
const isStandalone =
  import.meta.env.DEV &&
  (params.get("mode") === "standalone" ||
    document.documentElement.dataset.mcpMode === "standalone");

if (isStandalone) {
  initStandaloneMode();
} else {
  void initMcpMode();
}

function renderStatusMessage(message: string, action?: { label: string; run: () => void }) {
  const status = document.createElement("div");
  status.className = "loading-state";
  status.setAttribute("role", "status");
  const text = document.createElement("div");
  text.textContent = message;
  status.append(text);
  if (action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "nav-btn";
    button.textContent = action.label;
    button.addEventListener("click", () => action.run(), { once: true });
    const wrapper = document.createElement("div");
    wrapper.append(text, button);
    wrapper.style.display = "grid";
    wrapper.style.gap = "10px";
    wrapper.style.justifyItems = "center";
    status.replaceChildren(wrapper);
  }
  root.replaceChildren(status);
}

/**
 * The only trusted peer is the embedding parent at the origin it had when this frame
 * loaded. ancestorOrigins is authoritative where supported; the referrer is a fallback.
 */
function resolveStandaloneParentOrigin(): string | null {
  if (window.parent === window) return null;
  let origin = window.location.ancestorOrigins?.[0] ?? "";
  if (!origin && document.referrer) {
    try {
      origin = new URL(document.referrer).origin;
    } catch {
      origin = "";
    }
  }
  return origin && origin !== "null" ? origin : null;
}

function initStandaloneMode() {
  applyTheme("dark");
  const parentOrigin = resolveStandaloneParentOrigin();
  if (!parentOrigin) {
    renderStatusMessage("Standalone preview requires an embedding parent with a known origin.");
    return;
  }
  const postToParent = (message: IframeMessage) => {
    window.parent.postMessage(message, parentOrigin);
  };
  renderLoading(root);
  let standaloneData: DashboardData | undefined;

  const renderStandaloneData = () => {
    if (!standaloneData || (!standaloneData.weekly_calendar && !standaloneData.dashboard)) {
      renderLoading(root);
      return;
    }
    const state = readDashboardState(standaloneData);
    renderDashboard(root, standaloneData, {
      include_weekend: state.include_weekend,
      selected_calendar_ids: state.selected_calendar_ids,
      calendar_catalog: standaloneData.calendar_catalog?.items ?? [],
      tool_capabilities: standaloneData.tool_capabilities,
    });
  };

  setActionHandler((action: UiAction) => {
    if (action.type === "close_event_detail") {
      standaloneData = standaloneData ? { ...standaloneData, event_detail: undefined } : standaloneData;
      renderStandaloneData();
      return;
    }

    if (action.type === "close_email_detail") {
      standaloneData = standaloneData ? { ...standaloneData, email_detail: undefined } : standaloneData;
      renderStandaloneData();
      return;
    }

    if (action.type === "close_event_editor") {
      standaloneData = standaloneData ? { ...standaloneData, event_editor: undefined } : standaloneData;
      renderStandaloneData();
      return;
    }

    if (action.type === "chat") {
      postToParent({ type: "inject_chat_message", text: action.text });
      return;
    }

    let text = "Refresh dashboard";
    if (action.type === "calendar_rsvp") {
      text = `Set RSVP to ${action.responseStatus} for event ${action.eventId}.`;
    } else if (action.type === "calendar_cancel") {
      text = `Cancel event ${action.eventId}.`;
    } else if (action.type === "calendar_reschedule") {
      text = `Reschedule event ${action.eventId} by +${action.shiftMinutes} minutes.`;
    } else if (action.type === "week_nav") {
      text = `Navigate week: ${action.direction}`;
    } else if (action.type === "select_event") {
      text = `Open details for event ${action.eventId}.`;
    } else if (action.type === "select_email") {
      text = `Open details for email ${action.messageId}.`;
    } else if (action.type === "open_event_editor") {
      text = `Open ${action.mode} event editor.`;
    } else if (action.type === "save_event_editor") {
      text = `${action.draft.mode === "create" ? "Create" : "Update"} event ${action.draft.summary}.`;
    } else if (action.type === "toggle_weekend") {
      text = `Set include weekend to ${action.include_weekend}.`;
    } else if (action.type === "set_selected_calendars") {
      text = `Set selected calendars: ${action.selected_calendar_ids.join(", ")}`;
    } else if (action.type === "open_attachment") {
      text = `Open attachment: ${action.url}`;
    } else if (action.type === "open_link") {
      text = `Open link: ${action.url}`;
    } else if (action.type === "download_attachment") {
      text = `Download attachment: ${action.name}`;
    } else if (action.type === "email_mark_read") {
      text = `Mark email ${action.messageId} as read.`;
    } else if (action.type === "email_mark_unread") {
      text = `Mark email ${action.messageId} as unread.`;
    } else if (action.type === "email_archive") {
      text = `Archive email ${action.messageId}.`;
    } else if (action.type === "email_trash") {
      text = `Move email ${action.messageId} to trash.`;
    } else if (action.type === "email_untrash") {
      text = `Restore email ${action.messageId} from trash.`;
    } else if (action.type === "email_mark_spam") {
      text = `Mark email ${action.messageId} as spam.`;
    } else if (action.type === "email_mark_not_spam") {
      text = `Mark email ${action.messageId} as not spam.`;
    } else if (action.type === "email_download_attachment") {
      text = `Download attachment ${action.filename} from email ${action.messageId}.`;
    }

    postToParent({ type: "inject_chat_message", text });
  });

  window.addEventListener("message", (e: MessageEvent<ParentMessage>) => {
    // Ignore sibling/child frames, popups, and a parent that navigated to another origin.
    if (e.source !== window.parent || e.origin !== parentOrigin) return;
    if (!e.data || typeof e.data !== "object") return;

    switch (e.data.type) {
      case "dashboard_data": {
        const data = e.data.data;
        if (!data || typeof data !== "object") return;
        standaloneData = data as DashboardData;
        renderStandaloneData();
        break;
      }
      case "theme_changed": {
        if (e.data.theme === "dark" || e.data.theme === "light") {
          applyTheme(e.data.theme);
        }
        break;
      }
    }
  });

  postToParent({ type: "request_dashboard_data" });
}

async function initMcpMode() {
  applyTheme("dark");
  renderLoading(root);

  const lifecycle = new ViewLifecycle();
  // The view handle is issued by the server in the launch tool result; the
  // view keeps it only in memory and never mints or persists an identity.
  const view = new ViewSession();
  const registry = new OperationRegistry();
  let support: HostSupport = NO_HOST_SUPPORT;
  let hostContext: McpUiHostContext = {};
  let currentData: DashboardData = {};
  let hasData = false;
  let calendarsLoaded = false;
  let pendingFocus: string | undefined;
  /** Where keyboard focus goes back to when the open detail panel closes. */
  let returnFocus: string | undefined;
  let eventSaveInFlight = false;
  let navigationInFlight = false;
  const renderOptions: RenderOptions = {
    include_weekend: true,
    selected_calendar_ids: [],
    calendar_catalog: [],
  };
  /** The tool invocation that opened this view, as told by the host. */
  const invocation = {
    inputSeen: false,
    resultSeen: false,
    cancelled: false,
    args: {} as Record<string, unknown>,
  };
  let cancelPendingLoad: () => void = () => {};

  let ext: typeof import("@modelcontextprotocol/ext-apps");
  try {
    ext = await import("@modelcontextprotocol/ext-apps");
  } catch (err) {
    console.warn("MCP ext-apps not available:", err);
    renderStatusMessage("MCP app connection failed.");
    return;
  }

  const app = new ext.App(
    { name: "Workspace Dashboard", version: "2.0.0" },
    { availableDisplayModes: ["inline", "fullscreen"] },
    { autoResize: true },
  );

  // --- Rendering ---------------------------------------------------------------

  const setUiMessage = (message: string | undefined, kind: "notice" | "error") => {
    currentData =
      kind === "notice"
        ? { ...currentData, ui_notice: message, ui_error: undefined, ui_fallback_link: undefined }
        : { ...currentData, ui_error: message, ui_notice: undefined, ui_fallback_link: undefined };
  };

  const showFallbackLink = (message: string, url: string, offerOpen: boolean) => {
    currentData = {
      ...currentData,
      ui_notice: undefined,
      ui_error: undefined,
      ui_fallback_link: { message, url, offer_open: offerOpen },
    };
    renderCurrent();
  };

  const renderCurrent = () => {
    if (lifecycle.disposed) return;
    if (!hasData) {
      if (currentData.ui_error) renderStatusMessage(currentData.ui_error);
      return;
    }
    const state = readDashboardState(currentData);
    renderOptions.include_weekend = state.include_weekend;
    renderOptions.selected_calendar_ids = state.selected_calendar_ids;
    // No server-tools capability: nothing can be called, whatever the manifest says.
    currentData.tool_capabilities = support.serverTools ? registry.capabilities() : NO_TOOL_CAPABILITIES;
    renderOptions.tool_capabilities = currentData.tool_capabilities;
    renderOptions.host_features = hostFeatures(support, hostContext);
    renderOptions.focus_selector = pendingFocus;
    pendingFocus = undefined;
    renderDashboard(root, currentData, renderOptions);
  };

  const renderStatus = (message: string, action?: { label: string; run: () => void }) => {
    if (lifecycle.disposed) return;
    renderStatusMessage(message, action);
  };

  const withUiPending = async (operation: () => Promise<void>) => {
    const previousCursor = document.body.style.cursor;
    root.style.opacity = "0.92";
    document.body.style.cursor = "progress";
    try {
      await operation();
    } finally {
      root.style.opacity = "";
      document.body.style.cursor = previousCursor;
    }
  };

  // --- Server tool calls -------------------------------------------------------

  /** Call the tool implementing `operation`; resolves with the raw result. */
  const invokeTool = async (
    operation: Operation,
    args: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<CallToolResult> => {
    if (!support.serverTools) {
      throw new ToolCallError("This host does not let the dashboard call server tools.", "transport", "server_tools_unsupported");
    }
    const candidates = registry.candidates(operation);
    if (!candidates.length) {
      throw new ToolCallError("This action is not available for your account here.", "tool", "operation_unavailable");
    }
    let firstFailure: ToolCallError | undefined;
    for (const name of candidates) {
      try {
        const result = await app.callServerTool({ name, arguments: args }, { signal });
        if (!registry.hasManifest && isReadOperation(operation)) registry.learn(operation, name);
        return result;
      } catch (err) {
        const failure = classifyRejection(err, signal);
        if (registry.hasManifest || !isUnknownToolError(failure)) throw failure;
        firstFailure ??= failure;
      }
    }
    throw firstFailure!;
  };

  /** Call a non-view tool and require success (isError and embedded errors throw). */
  const callTool = async (operation: Operation, args: Record<string, unknown>, signal?: AbortSignal) =>
    requireSuccess(await invokeTool(operation, args, signal));

  const invokeView = async (
    operation: ViewOperation,
    args: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<CallToolResult> => {
    const withHandle: Record<string, unknown> = { ...args };
    if (view.handle) {
      withHandle.view_handle = view.handle;
      if (CONDITIONAL_OPERATIONS.has(operation) && view.revision !== undefined) {
        withHandle.expected_revision = view.revision;
      }
    } else if (CONDITIONAL_OPERATIONS.has(operation)) {
      throw new ToolCallError("The dashboard view is not open yet.", "tool", VIEW_HANDLE_INVALID);
    }
    const result = await invokeTool(operation, withHandle, signal);
    if (signal?.aborted || lifecycle.disposed) {
      throw new ToolCallError("The request was cancelled.", "cancelled", "cancelled");
    }
    registry.adoptResult(result);
    // A conflict still tells us the current revision of this view.
    view.adoptResult(result);
    return requireSuccess(result);
  };

  /**
   * Call a dashboard tool with this view's handle. An unknown or expired handle
   * is answered by re-requesting a fresh view exactly once, then retrying the
   * call once; a second failure is reported, never looped.
   */
  const callView: ViewCaller = async (operation, args = {}, signal) => {
    try {
      return await invokeView(operation, args, signal);
    } catch (err) {
      if (!(err instanceof ToolCallError) || err.code !== VIEW_HANDLE_INVALID) throw err;
    }
    view.forget();
    if (!LAUNCH_OPERATIONS.has(operation)) {
      const fresh = await invokeView("getDashboard", {}, signal);
      const parsed = extractDashboardData(fresh);
      if (parsed) currentData = mergeDashboardData(currentData, parsed);
    }
    return invokeView(operation, args, signal);
  };

  /**
   * Recover from a failed view action. A stale-revision conflict never retries
   * the write: the view refetches the current server state instead.
   */
  const recoverFromViewError = async (err: unknown): Promise<never> => {
    if (err instanceof ToolCallError && err.code === VIEW_STATE_CONFLICT) {
      const conflictState = (err.result as { structuredContent?: { state?: unknown } } | undefined)
        ?.structuredContent?.state;
      if (conflictState && typeof conflictState === "object") {
        currentData = replaceDashboardState(currentData, conflictState as Record<string, unknown>);
      }
      await refresh("full");
      throw new ViewRefreshedAfterConflict();
    }
    throw err;
  };

  const reportFailure = (err: unknown, restore: DashboardData | undefined, label: string) => {
    if (lifecycle.disposed || (err instanceof ToolCallError && err.kind === "cancelled")) return;
    if (err instanceof ViewRefreshedAfterConflict) {
      setUiMessage(err.message, "notice");
    } else {
      if (restore) currentData = restore;
      setUiMessage(`${label}: ${describeFailure(err)}`, "error");
    }
    renderCurrent();
  };

  // --- Loading -------------------------------------------------------------------

  /**
   * Load dashboard data. Latest wins: a newer load (or an authoritative host
   * result) supersedes this one, whose response is then dropped.
   */
  const refresh = async (mode: "full" | "weekly", launchArgs: Record<string, unknown> = {}): Promise<boolean> => {
    const ticket = lifecycle.begin("data");
    try {
      const weekly = mode === "weekly" && registry.available("getWeeklyCalendar");
      const result = await callView(weekly ? "getWeeklyCalendar" : "getDashboard", launchArgs, ticket.signal);
      if (!ticket.current) return false;
      const parsed = extractDashboardData(result);
      const next: DashboardData = { ...currentData, ui_error: undefined, ui_notice: undefined };
      if (parsed?.dashboard) next.dashboard = parsed.dashboard;
      if (parsed?.weekly_calendar) next.weekly_calendar = parsed.weekly_calendar;
      currentData = next;
      hasData = hasData || !!(parsed?.dashboard || parsed?.weekly_calendar);
      renderCurrent();
      void loadCalendarsOnce();
      return true;
    } catch (err) {
      if (!ticket.current) return false;
      throw err;
    } finally {
      ticket.done();
    }
  };

  const loadCalendarsOnce = async () => {
    if (calendarsLoaded || !registry.available("listCalendars") || lifecycle.disposed) return;
    calendarsLoaded = true;
    const ticket = lifecycle.begin("calendars");
    try {
      const result = await callTool("listCalendars", {}, ticket.signal);
      if (!ticket.current) return;
      const calendars = extractCalendarCatalog(result);
      if (calendars.length) {
        renderOptions.calendar_catalog = calendars;
        currentData = {
          ...currentData,
          calendar_catalog: { items: calendars, fetched_at_utc: new Date().toISOString() },
        };
        renderCurrent();
      }
    } catch (err) {
      if (ticket.current) console.warn("Calendar list unavailable:", err);
    } finally {
      ticket.done();
    }
  };

  /** The launch operation this view was opened by (host context tool info). */
  const launchOperation = (): "getDashboard" | "getWeeklyCalendar" => {
    const name = hostContext.toolInfo?.tool?.name ?? "";
    return name.endsWith("get_weekly_calendar_view") ? "getWeeklyCalendar" : "getDashboard";
  };

  /**
   * Load without a host result. Replays the invocation the host announced: the
   * same launch tool with the input's arguments (and so the input's handle).
   */
  const loadWithoutHostResult = async (reason: "no-invocation" | "input-handle" | "user") => {
    if (lifecycle.disposed || !support.serverTools) return;
    if (reason !== "user" && (invocation.resultSeen || invocation.cancelled)) return;
    const launchArgs: Record<string, unknown> = {};
    for (const key of LAUNCH_ARGUMENTS) {
      if (invocation.args[key] !== undefined) launchArgs[key] = invocation.args[key];
    }
    try {
      await refresh(launchOperation() === "getWeeklyCalendar" ? "weekly" : "full", launchArgs);
    } catch (err) {
      if (lifecycle.disposed || (err instanceof ToolCallError && err.kind === "cancelled")) return;
      if (hasData) {
        setUiMessage(`Initial load failed: ${describeFailure(err)}`, "error");
        renderCurrent();
      } else {
        renderStatus(`Initial load failed: ${describeFailure(err)}`, {
          label: "Try again",
          run: () => void loadWithoutHostResult("user"),
        });
      }
    }
  };

  // --- Host lifecycle handlers (registered before connect) ----------------------

  app.ontoolinput = (params) => {
    if (lifecycle.disposed || invocation.resultSeen || invocation.cancelled) return;
    invocation.inputSeen = true;
    invocation.args = params.arguments && typeof params.arguments === "object" ? { ...params.arguments } : {};
    cancelPendingLoad();
    if (view.adoptInputHandle(invocation.args.view_handle)) {
      // Reopening an existing view: loading it can never mint a new one, so a
      // host that never delivers the result is covered after a short grace.
      cancelPendingLoad = lifecycle.schedule(() => void loadWithoutHostResult("input-handle"), INPUT_HANDLE_GRACE_MS);
    } else {
      // The launch call itself is minting this view's handle; its result is the
      // only source of it. Never mint a second (orphan) view automatically.
      cancelPendingLoad = lifecycle.schedule(() => {
        if (hasData) return;
        renderStatus("Still waiting for the dashboard result from the host.", support.serverTools
          ? { label: "Load a new view now", run: () => void loadWithoutHostResult("user") }
          : undefined);
      }, RESULT_WAIT_NOTICE_MS);
    }
  };

  app.ontoolresult = (result) => {
    if (lifecycle.disposed) return;
    invocation.resultSeen = true;
    cancelPendingLoad();
    // The host's result is authoritative: drop any fallback load still in flight.
    lifecycle.supersede("data");
    registry.adoptResult(result);
    view.adoptResult(result);
    if (result.isError === true || hasEmbeddedError(result)) {
      const message = `The dashboard could not be opened: ${toolResultErrorMessage(result)}`;
      if (hasData) {
        setUiMessage(message, "error");
        renderCurrent();
      } else {
        renderStatus(message, support.serverTools
          ? { label: "Open a new view", run: () => { view.forget(); void loadWithoutHostResult("user"); } }
          : undefined);
      }
      return;
    }
    const data = extractDashboardData(result);
    if (data && (data.weekly_calendar || data.dashboard || data.event_detail || data.email_detail)) {
      currentData = mergeDashboardData(currentData, data);
      hasData = true;
    }
    if (hasData) {
      renderCurrent();
      void loadCalendarsOnce();
    } else {
      renderStatus("The host delivered a dashboard result without dashboard data.");
    }
  };

  app.ontoolcancelled = () => {
    if (lifecycle.disposed || invocation.resultSeen) return;
    invocation.cancelled = true;
    cancelPendingLoad();
    lifecycle.supersede("data");
    if (hasData) {
      setUiMessage("The dashboard request was cancelled; showing the last loaded data.", "notice");
      renderCurrent();
      return;
    }
    renderStatus("The dashboard request was cancelled.", support.serverTools
      ? { label: "Load dashboard", run: () => void loadWithoutHostResult("user") }
      : undefined);
  };

  const applyHostContext = (ctx: McpUiHostContext | undefined) => {
    if (!ctx || lifecycle.disposed) return;
    hostContext = { ...hostContext, ...ctx };
    if (ctx.theme) ext.applyDocumentTheme(ctx.theme);
    if (ctx.styles?.variables) ext.applyHostStyleVariables(ctx.styles.variables);
    if (ctx.styles?.css?.fonts) ext.applyHostFonts(ctx.styles.css.fonts);
    if (ctx.safeAreaInsets) {
      const { top, right, bottom, left } = ctx.safeAreaInsets;
      document.body.style.padding = `${top}px ${right}px ${bottom}px ${left}px`;
    }
    if (ctx.containerDimensions) applyContainerDimensions(ctx.containerDimensions);
    if (hasData && (ctx.displayMode || ctx.availableDisplayModes)) renderCurrent();
  };
  app.onhostcontextchanged = applyHostContext;

  app.onteardown = async () => {
    teardown();
    return {};
  };

  app.onerror = (error) => {
    if (!lifecycle.disposed) console.warn("MCP Apps channel error:", error);
  };

  const teardown = () => {
    if (lifecycle.disposed) return;
    cancelPendingLoad();
    lifecycle.dispose();
    detachDashboardHandlers(root);
    setActionHandler(() => {});
  };

  // --- User actions ------------------------------------------------------------------

  const openExternal = async (rawUrl: string, kind: LinkKind) => {
    const noun = kind === "attachment" ? "attachment" : "link";
    // Re-validate at the adapter boundary; only the host navigates.
    const url = safeExternalUrl(rawUrl, kind);
    if (!url) {
      setUiMessage(`Blocked an unsupported ${noun} URL.`, "error");
      renderCurrent();
      return;
    }
    if (!support.openLinks) {
      showFallbackLink(`This host does not open links from the dashboard. Copy the ${noun} address instead.`, url, false);
      return;
    }
    const request = lifecycle.request();
    try {
      const result = await app.openLink({ url }, { signal: request.signal });
      if (lifecycle.disposed) return;
      if (result?.isError) {
        showFallbackLink(`The host declined to open the ${noun}. Copy the address instead.`, url, false);
        return;
      }
      if (currentData.ui_fallback_link) {
        currentData = { ...currentData, ui_fallback_link: undefined };
        renderCurrent();
      }
    } catch (err) {
      if (lifecycle.disposed) return;
      showFallbackLink(`The host could not open the ${noun} (${describeFailure(classifyRejection(err))}). Copy the address instead.`, url, false);
    } finally {
      request.done();
    }
  };

  const hostDownload = async (params: McpUiDownloadFileRequest["params"]): Promise<"started" | "declined"> => {
    const request = lifecycle.request();
    try {
      const result = await app.downloadFile(params, { signal: request.signal });
      return result?.isError ? "declined" : "started";
    } finally {
      request.done();
    }
  };

  const downloadLinkedAttachment = async (rawUrl: string, name: string, mimeType?: string) => {
    const url = safeExternalUrl(rawUrl, "attachment");
    if (!url) {
      setUiMessage("Blocked an unsupported attachment URL.", "error");
      renderCurrent();
      return;
    }
    if (!support.downloadFile) {
      showFallbackLink("This host does not support downloads.", url, true);
      return;
    }
    try {
      const link: { type: "resource_link"; name: string; uri: string; mimeType?: string } = {
        type: "resource_link",
        name: name || "attachment",
        uri: url,
      };
      if (mimeType) link.mimeType = mimeType;
      const outcome = await hostDownload({ contents: [link] });
      if (lifecycle.disposed) return;
      if (outcome === "declined") {
        // A denial is final: offer the user an explicit alternative, never force one.
        showFallbackLink(`The host declined the download of ${name}.`, url, true);
        return;
      }
      setUiMessage(`Download started: ${name}`, "notice");
      renderCurrent();
    } catch (err) {
      reportFailure(classifyRejection(err), undefined, "Failed to download attachment");
    }
  };

  const downloadEmailAttachment = async (action: Extract<UiAction, { type: "email_download_attachment" }>) => {
    if (!support.downloadFile) {
      setUiMessage("This host does not support downloads. Ask the assistant to save the attachment to Drive.", "notice");
      renderCurrent();
      return;
    }
    if (action.size !== undefined && action.size > MAX_INLINE_DOWNLOAD_BYTES) {
      setUiMessage(
        `${action.filename} is too large to download here (${formatBytes(action.size)}; limit ${formatBytes(MAX_INLINE_DOWNLOAD_BYTES)}). Ask the assistant to save it to Drive.`,
        "error",
      );
      renderCurrent();
      return;
    }
    const request = lifecycle.request();
    try {
      const payload = await callView(
        "getEmailAttachment",
        { message_id: action.messageId, attachment_id: action.attachmentId },
        request.signal,
      );
      if (lifecycle.disposed) return;
      const data = extractObjectPayload(payload);
      if (!data || typeof data.blob_base64 !== "string") throw new Error("Attachment content is unavailable.");
      if (base64DecodedLength(data.blob_base64) > MAX_INLINE_DOWNLOAD_BYTES) {
        throw new Error(`the attachment exceeds the ${formatBytes(MAX_INLINE_DOWNLOAD_BYTES)} inline download limit`);
      }
      const fileName = action.filename || (typeof data.filename === "string" && data.filename) || "attachment";
      const mimeType =
        (typeof data.mime_type === "string" && data.mime_type) || action.mimeType || "application/octet-stream";
      const safeName = ensureDownloadFilename(fileName, mimeType);
      const outcome = await hostDownload({
        contents: [
          {
            type: "resource",
            resource: { uri: `file:///${encodeURIComponent(safeName)}`, mimeType, blob: data.blob_base64 },
          },
        ],
      });
      if (lifecycle.disposed) return;
      if (outcome === "declined") {
        setUiMessage(`The host declined the download of ${fileName}.`, "notice");
      } else {
        setUiMessage(`Download started: ${fileName}`, "notice");
      }
      renderCurrent();
    } catch (err) {
      reportFailure(err instanceof ToolCallError ? err : classifyRejection(err), undefined, "Failed to download email attachment");
    } finally {
      request.done();
    }
  };

  const sendChatMessage = async (text: string) => {
    if (!support.message || !text) return;
    const request = lifecycle.request();
    try {
      const result = await app.sendMessage({ role: "user", content: [{ type: "text", text }] }, { signal: request.signal });
      if (lifecycle.disposed) return;
      setUiMessage(result?.isError ? "The host declined the chat message." : "Sent to the chat.", result?.isError ? "error" : "notice");
      renderCurrent();
    } catch (err) {
      reportFailure(classifyRejection(err), undefined, "Failed to send the chat message");
    } finally {
      request.done();
    }
  };

  /** Tell the model what the user is looking at (only when the host accepts it). */
  const shareModelContext = (text: string) => {
    if (!support.updateModelContext || lifecycle.disposed) return;
    const request = lifecycle.request();
    app
      .updateModelContext({ content: [{ type: "text", text }] }, { signal: request.signal })
      .catch((err: unknown) => console.warn("Model context update failed:", err))
      .finally(request.done);
  };

  const toggleDisplayMode = async () => {
    const modes = hostContext.availableDisplayModes ?? [];
    const target = hostContext.displayMode === "fullscreen" ? "inline" : "fullscreen";
    if (!modes.includes(target)) return;
    const request = lifecycle.request();
    try {
      const result = await app.requestDisplayMode({ mode: target }, { signal: request.signal });
      if (lifecycle.disposed) return;
      hostContext = { ...hostContext, displayMode: result.mode };
      pendingFocus = "[data-toggle-display-mode]";
      renderCurrent();
    } catch (err) {
      reportFailure(classifyRejection(err), undefined, "Failed to change the display mode");
    } finally {
      request.done();
    }
  };

  const updateStatePatch = async (patch: Record<string, unknown>) => {
    try {
      await callView("patchState", patch);
    } catch (err) {
      await recoverFromViewError(err);
    }
  };

  /**
   * Run a Google mutation. Optimistic state is applied first and restored on
   * any failure (isError result, embedded error, or rejected call); the success
   * notice is shown only after the server confirmed the action.
   */
  const runMutation = async (params: {
    operation: Operation;
    args: Record<string, unknown>;
    optimistic?: (data: DashboardData) => DashboardData;
    failureLabel: string;
    successNotice: string;
    after?: () => Promise<void>;
  }) => {
    if (!registry.available(params.operation)) {
      setUiMessage(`${params.failureLabel}: this action is not available for your account here.`, "error");
      renderCurrent();
      return;
    }
    const previous = currentData;
    if (params.optimistic) {
      currentData = params.optimistic(currentData);
      renderCurrent();
    }
    const request = lifecycle.request();
    try {
      await callTool(params.operation, params.args, request.signal);
    } catch (err) {
      reportFailure(err, previous, params.failureLabel);
      return;
    } finally {
      request.done();
    }
    if (lifecycle.disposed) return;
    try {
      await params.after?.();
    } catch (err) {
      console.warn("Refresh after a completed action failed:", err);
    }
    setUiMessage(params.successNotice, "notice");
    renderCurrent();
  };

  const startEditor = (mode: "create" | "edit", seedDate?: string) => {
    const state = readDashboardState(currentData);
    if (mode === "edit") {
      const detail = currentData.event_detail;
      if (!detail) {
        setUiMessage("Open an event first to edit it.", "error");
        renderCurrent();
        return;
      }
      currentData = {
        ...currentData,
        event_editor: {
          mode: "edit",
          event_id: detail.event_id,
          calendar_id: detail.calendar_id,
          summary: detail.title,
          start_local: toLocalInputValue(detail.start),
          end_local: toLocalInputValue(detail.end),
          timezone: detail.timezone || state.timezone,
          location: detail.location || "",
          description: detail.description || "",
          attendees_csv: detail.attendees.map((item) => item.email).join(", "),
          create_conference: !!detail.conference_link,
        },
        ui_notice: undefined,
        ui_error: undefined,
      };
      renderCurrent();
      return;
    }

    const start = defaultStartLocal(seedDate);
    const end = new Date(start.getTime() + 60 * 60_000);
    const selectedCalendar = state.selected_calendar_ids[0] || "primary";
    currentData = {
      ...currentData,
      event_editor: {
        mode: "create",
        calendar_id: selectedCalendar,
        summary: "",
        start_local: toInputLocalString(start),
        end_local: toInputLocalString(end),
        timezone: state.timezone,
        location: "",
        description: "",
        attendees_csv: "",
        create_conference: true,
      },
      ui_notice: undefined,
      ui_error: undefined,
    };
    renderCurrent();
  };

  const saveEventEditor = async (draft: EventEditorDraft) => {
    const startIso = localInputToIso(draft.start_local);
    const endIso = localInputToIso(draft.end_local);
    const attendees = parseAttendeesCsv(draft.attendees_csv || "");
    const create = draft.mode === "create";
    if (!create && !draft.event_id) {
      setUiMessage("Failed to save event: missing event id for edit.", "error");
      renderCurrent();
      return;
    }
    const idempotencyKey = makeIdempotencyKey("create");
    const args: Record<string, unknown> = create
      ? {
          calendar_id: draft.calendar_id,
          idempotency_key: idempotencyKey,
          summary: draft.summary,
          start_datetime: startIso,
          end_datetime: endIso,
          timezone: draft.timezone,
          description: draft.description || undefined,
          location: draft.location || undefined,
          attendees: attendees.map((email) => ({ email })),
          conference_data: draft.create_conference
            ? { createRequest: { requestId: idempotencyKey, conferenceSolutionKey: { type: "hangoutsMeet" } } }
            : undefined,
          send_updates: "all",
          on_conflict: "suggest_next_slot",
        }
      : {
          event_id: draft.event_id,
          calendar_id: draft.calendar_id,
          summary: draft.summary,
          start_datetime: startIso,
          end_datetime: endIso,
          timezone: draft.timezone,
          description: draft.description || undefined,
          location: draft.location || undefined,
          attendees: attendees.map((email) => ({ email })),
          send_updates: "all",
          on_conflict: "suggest_next_slot",
        };
    // Keep the typed draft: a failure re-renders the open editor with it.
    currentData = { ...currentData, event_editor: draft };
    await runMutation({
      operation: create ? "createEvent" : "updateEvent",
      args,
      failureLabel: "Failed to save event",
      successNotice: create ? "Event created." : "Event updated.",
      after: async () => {
        currentData = { ...currentData, event_editor: undefined };
        await refresh("weekly");
      },
    });
  };

  const refreshEmailDetailIfOpen = async (messageId: string) => {
    if (currentData.email_detail?.message_id !== messageId) return;
    const ticket = lifecycle.begin("detail");
    try {
      const result = await callView("getEmailDetail", { message_id: messageId }, ticket.signal);
      if (!ticket.current) return;
      const parsed = extractDashboardData(result);
      if (parsed?.email_detail) {
        currentData = syncInboxMessageFromEmailDetail(
          { ...currentData, email_detail: parsed.email_detail },
          parsed.email_detail,
        );
      }
    } finally {
      ticket.done();
    }
  };

  const runEmailMutation = (params: {
    operation: Operation;
    messageId: string;
    args: Record<string, unknown>;
    successNotice: string;
    failureLabel: string;
    patch: { addLabels?: string[]; removeLabels?: string[]; isUnread?: boolean };
  }) =>
    runMutation({
      operation: params.operation,
      args: params.args,
      optimistic: (data) => optimisticPatchEmail(data, params.messageId, params.patch),
      failureLabel: params.failureLabel,
      successNotice: params.successNotice,
      after: async () => {
        await refresh("full");
        try {
          await refreshEmailDetailIfOpen(params.messageId);
        } catch {
          // Keep optimistic state when the immediate post-mutation detail fetch is stale/unavailable.
        }
        currentData = optimisticPatchEmailDetail(currentData, params.messageId, params.patch);
      },
    });

  const openDetail = async (
    operation: "getEventDetail" | "getEmailDetail",
    args: Record<string, unknown>,
    focusSelector: string,
    failureLabel: string,
  ) => {
    const ticket = lifecycle.begin("detail");
    try {
      const result = await callView(operation, args, ticket.signal);
      if (!ticket.current) return; // A newer selection won.
      const parsed = extractDashboardData(result);
      if (operation === "getEventDetail" && parsed?.event_detail) {
        currentData = { ...currentData, event_detail: parsed.event_detail, email_detail: undefined };
        returnFocus = focusSelector;
        pendingFocus = ".event-panel [data-close-event]";
        renderCurrent();
        shareModelContext(
          `The user opened calendar event "${parsed.event_detail.title}" (${parsed.event_detail.start} to ${parsed.event_detail.end}; event_id ${parsed.event_detail.event_id}, calendar_id ${parsed.event_detail.calendar_id}) in the Workspace dashboard.`,
        );
      } else if (operation === "getEmailDetail" && parsed?.email_detail) {
        currentData = syncInboxMessageFromEmailDetail(
          { ...currentData, email_detail: parsed.email_detail, event_detail: undefined },
          parsed.email_detail,
        );
        returnFocus = focusSelector;
        pendingFocus = ".email-panel [data-close-email]";
        renderCurrent();
        shareModelContext(
          `The user opened the email "${parsed.email_detail.subject}" from ${parsed.email_detail.from_value} (message_id ${parsed.email_detail.message_id}) in the Workspace dashboard.`,
        );
      }
    } catch (err) {
      if (ticket.current) reportFailure(err, undefined, failureLabel);
    } finally {
      ticket.done();
    }
  };

  const closePanel = (patch: Partial<DashboardData>) => {
    currentData = { ...currentData, ...patch };
    pendingFocus = returnFocus;
    returnFocus = undefined;
    renderCurrent();
  };

  const emailMutations: Partial<Record<UiAction["type"], {
    operation: Operation;
    args: (messageId: string) => Record<string, unknown>;
    successNotice: string;
    failureLabel: string;
    patch: { addLabels?: string[]; removeLabels?: string[]; isUnread?: boolean };
  }>> = {
    email_mark_read: {
      operation: "markEmailRead",
      args: (id) => ({ message_id: id }),
      successNotice: "Email marked as read.",
      failureLabel: "Failed to mark as read",
      patch: { removeLabels: ["UNREAD"], isUnread: false },
    },
    email_mark_unread: {
      operation: "markEmailUnread",
      args: (id) => ({ message_id: id }),
      successNotice: "Email marked as unread.",
      failureLabel: "Failed to mark as unread",
      patch: { addLabels: ["UNREAD"], isUnread: true },
    },
    email_archive: {
      operation: "moveEmail",
      args: (id) => ({ message_id: id, remove_label_ids: ["INBOX"] }),
      successNotice: "Email archived.",
      failureLabel: "Failed to archive email",
      patch: { removeLabels: ["INBOX"] },
    },
    email_trash: {
      operation: "deleteEmail",
      args: (id) => ({ message_id: id, permanent: false }),
      successNotice: "Email moved to trash.",
      failureLabel: "Failed to move email to trash",
      patch: { addLabels: ["TRASH"], removeLabels: ["INBOX"] },
    },
    email_untrash: {
      operation: "untrashEmail",
      args: (id) => ({ message_id: id }),
      successNotice: "Email restored from trash.",
      failureLabel: "Failed to restore email",
      patch: { removeLabels: ["TRASH"], addLabels: ["INBOX"] },
    },
    email_mark_spam: {
      operation: "markEmailSpam",
      args: (id) => ({ message_id: id }),
      successNotice: "Email marked as spam.",
      failureLabel: "Failed to mark email as spam",
      patch: { addLabels: ["SPAM"], removeLabels: ["INBOX"] },
    },
    email_mark_not_spam: {
      operation: "markEmailNotSpam",
      args: (id) => ({ message_id: id, add_to_inbox: true }),
      successNotice: "Email marked as not spam.",
      failureLabel: "Failed to mark email as not spam",
      patch: { removeLabels: ["SPAM"], addLabels: ["INBOX"] },
    },
  };

  setActionHandler((action: UiAction) => {
    if (lifecycle.disposed) return;

    if (action.type === "close_event_detail") {
      closePanel({ event_detail: undefined });
      return;
    }
    if (action.type === "close_email_detail") {
      closePanel({ email_detail: undefined });
      return;
    }
    if (action.type === "close_event_editor") {
      closePanel({ event_editor: undefined });
      return;
    }

    if (action.type === "open_attachment") {
      void withUiPending(() => openExternal(action.url, "attachment"));
      return;
    }
    if (action.type === "open_link") {
      void withUiPending(() => openExternal(action.url, action.kind));
      return;
    }
    if (action.type === "download_attachment") {
      void withUiPending(() => downloadLinkedAttachment(action.url, action.name, action.mimeType));
      return;
    }
    if (action.type === "email_download_attachment") {
      void withUiPending(() => downloadEmailAttachment(action));
      return;
    }

    if (action.type === "chat") {
      void sendChatMessage(action.text);
      return;
    }
    if (action.type === "toggle_display_mode") {
      void toggleDisplayMode();
      return;
    }

    if (action.type === "open_event_editor") {
      startEditor(action.mode, action.seed_date);
      return;
    }

    if (action.type === "save_event_editor") {
      if (eventSaveInFlight) return;
      eventSaveInFlight = true;
      void withUiPending(() => saveEventEditor(action.draft)).finally(() => {
        eventSaveInFlight = false;
      });
      return;
    }

    if (action.type === "toggle_weekend") {
      const previousData = currentData;
      currentData = patchDashboardState(currentData, { include_weekend: action.include_weekend });
      renderCurrent();
      void withUiPending(async () => {
        await updateStatePatch({ include_weekend: action.include_weekend });
        await refresh("weekly");
      }).catch((err: unknown) => reportFailure(err, previousData, "Failed to update weekend preference"));
      return;
    }

    if (action.type === "set_selected_calendars") {
      const previousData = currentData;
      currentData = patchDashboardState(currentData, { selected_calendars: action.selected_calendar_ids });
      renderCurrent();
      void withUiPending(async () => {
        await updateStatePatch({ selected_calendars: action.selected_calendar_ids });
        await refresh("full");
      }).catch((err: unknown) => reportFailure(err, previousData, "Failed to update selected calendars"));
      return;
    }

    if (action.type === "select_event") {
      const selector = `[data-open-event][data-calendar-id="${CSS.escape(action.calendarId)}"][data-event-id="${CSS.escape(action.eventId)}"]`;
      void withUiPending(() =>
        openDetail("getEventDetail", { calendar_id: action.calendarId, event_id: action.eventId }, selector, "Failed to load event details"),
      );
      return;
    }

    if (action.type === "select_email") {
      const selector = `[data-open-email][data-message-id="${CSS.escape(action.messageId)}"]`;
      void withUiPending(() =>
        openDetail("getEmailDetail", { message_id: action.messageId }, selector, "Failed to load email details"),
      );
      return;
    }

    const emailMutation = emailMutations[action.type];
    if (emailMutation && "messageId" in action) {
      const messageId = action.messageId;
      void withUiPending(() =>
        runEmailMutation({
          operation: emailMutation.operation,
          messageId,
          args: emailMutation.args(messageId),
          successNotice: emailMutation.successNotice,
          failureLabel: emailMutation.failureLabel,
          patch: emailMutation.patch,
        }),
      );
      return;
    }

    if (action.type === "calendar_rsvp") {
      void withUiPending(() =>
        runMutation({
          operation: "respondToEvent",
          args: {
            calendar_id: action.calendarId,
            event_id: action.eventId,
            response_status: action.responseStatus,
            send_updates: "all",
          },
          optimistic: (data) => optimisticSetRsvp(data, action.calendarId, action.eventId, action.responseStatus),
          failureLabel: "Failed to update RSVP",
          successNotice: "Response sent.",
          after: () => refresh("weekly").then(() => undefined),
        }),
      );
      return;
    }

    if (action.type === "calendar_reschedule") {
      const nextStart = shiftIsoMinutes(action.start, action.shiftMinutes);
      const nextEnd = shiftIsoMinutes(action.end, action.shiftMinutes);
      void withUiPending(() =>
        runMutation({
          operation: "updateEvent",
          args: {
            event_id: action.eventId,
            calendar_id: action.calendarId,
            start_datetime: nextStart,
            end_datetime: nextEnd,
            timezone: action.timezone,
            send_updates: "all",
            on_conflict: "suggest_next_slot",
          },
          optimistic: (data) => optimisticRescheduleEvent(data, action.calendarId, action.eventId, nextStart, nextEnd),
          failureLabel: "Failed to reschedule event",
          successNotice: "Event rescheduled.",
          after: () => refresh("weekly").then(() => undefined),
        }),
      );
      return;
    }

    if (action.type === "calendar_cancel") {
      void withUiPending(() =>
        runMutation({
          operation: "deleteEvent",
          args: { calendar_id: action.calendarId, event_id: action.eventId, force: true, send_updates: "all" },
          optimistic: (data) => optimisticCancelEvent(data, action.calendarId, action.eventId),
          failureLabel: "Failed to cancel event",
          successNotice: "Event cancelled.",
          after: () => refresh("weekly").then(() => undefined),
        }),
      );
      return;
    }

    if (action.type === "week_nav") {
      const operation: ViewOperation =
        action.direction === "prev" ? "prevRange" : action.direction === "next" ? "nextRange" : "today";
      if (!registry.available(operation)) {
        setUiMessage("Navigation is not available here.", "error");
        renderCurrent();
        return;
      }
      // One state write at a time: a second click would carry the same
      // expected_revision and could only conflict.
      if (navigationInFlight) return;
      navigationInFlight = true;
      const previousData = currentData;
      void withUiPending(async () => {
        let result: unknown;
        try {
          result = await callView(operation);
        } catch (err) {
          await recoverFromViewError(err);
        }
        if (lifecycle.disposed) return;
        const nextState = extractObjectPayload(result)?.state;
        if (nextState && typeof nextState === "object") {
          currentData = replaceDashboardState(currentData, {
            ...(currentData.dashboard?.state || {}),
            ...(nextState as Record<string, unknown>),
          });
          renderCurrent();
        }
        await refresh("weekly");
      })
        .catch((err: unknown) => reportFailure(err, previousData, "Failed to navigate week"))
        .finally(() => {
          navigationInFlight = false;
        });
      return;
    }

    void refresh("full").catch((err: unknown) => reportFailure(err, undefined, "Failed to refresh dashboard"));
  });

  // --- Connect ---------------------------------------------------------------------

  try {
    await app.connect();
  } catch (err) {
    console.warn("MCP Apps connection failed:", err);
    teardown();
    renderStatusMessage("MCP app connection failed.");
    return;
  }
  if (lifecycle.disposed) return;

  support = readHostSupport(app.getHostCapabilities());
  applyHostContext(app.getHostContext());
  if (!support.serverTools) {
    // Render only what the host pushes; no operation can be offered.
    if (!hasData && !invocation.cancelled) renderStatus("Waiting for dashboard data from the host.");
    return;
  }

  await discoverTools(app, registry, lifecycle);
  if (lifecycle.disposed) return;
  if (hasData) renderCurrent(); // Capabilities may have changed with discovery.

  if (!invocation.inputSeen && !invocation.resultSeen && !invocation.cancelled) {
    // The host announced no invocation at all (a conforming host sends
    // ui/notifications/tool-input right after initialization). Load a view of
    // our own after a grace period, unless the host catches up meanwhile.
    cancelPendingLoad = lifecycle.schedule(() => {
      if (!invocation.inputSeen && !invocation.resultSeen && !invocation.cancelled) {
        void loadWithoutHostResult("no-invocation");
      }
    }, NO_INVOCATION_GRACE_MS);
  }
}

/** Best-effort, bounded `tools/list` walk; partial catalogs are kept. */
async function discoverTools(app: App, registry: OperationRegistry, lifecycle: ViewLifecycle) {
  const names = new Set<string>();
  let cursor: string | undefined;
  for (let page = 0; page < MAX_DISCOVERY_PAGES && !lifecycle.disposed; page += 1) {
    const request = lifecycle.request();
    try {
      const result = await app.request(
        { method: "tools/list", params: cursor ? { cursor } : {} },
        { signal: request.signal },
      );
      for (const tool of result.tools) names.add(tool.name);
      if (!result.nextCursor) break;
      cursor = result.nextCursor;
    } catch (err) {
      // Discovery is optional: hosts may only forward tools/call.
      console.warn("Tool discovery unavailable or partial; using known read tool names:", err);
      break;
    } finally {
      request.done();
    }
  }
  if (names.size) registry.setDiscovered(names);
}

function mergeDashboardData(base: DashboardData, incoming: DashboardData): DashboardData {
  return {
    weekly_calendar: incoming.weekly_calendar ?? base.weekly_calendar,
    dashboard: incoming.dashboard ?? base.dashboard,
    event_detail: incoming.event_detail ?? base.event_detail,
    email_detail: incoming.email_detail ?? base.email_detail,
    calendar_catalog: incoming.calendar_catalog ?? base.calendar_catalog,
    event_editor: incoming.event_editor ?? base.event_editor,
    ui_notice: incoming.ui_notice ?? base.ui_notice,
    ui_error: incoming.ui_error ?? base.ui_error,
    tool_capabilities: incoming.tool_capabilities ?? base.tool_capabilities,
    ui_fallback_link: incoming.ui_fallback_link ?? base.ui_fallback_link,
    generated_at: incoming.generated_at ?? base.generated_at,
  };
}

function patchDashboardState(
  data: DashboardData,
  patch: Record<string, unknown>
): DashboardData {
  if (!data.dashboard) {
    return data;
  }
  return {
    ...data,
    dashboard: {
      ...data.dashboard,
      state: {
        ...(data.dashboard.state || {}),
        ...patch,
      },
    },
  };
}

function replaceDashboardState(
  data: DashboardData,
  nextState: Record<string, unknown>
): DashboardData {
  if (!data.dashboard) {
    return data;
  }
  return {
    ...data,
    dashboard: {
      ...data.dashboard,
      state: nextState,
    },
  };
}

function optimisticSetRsvp(
  data: DashboardData,
  calendarId: string,
  eventId: string,
  responseStatus: "accepted" | "tentative" | "declined"
): DashboardData {
  const weekly = data.weekly_calendar;
  const detail = data.event_detail;
  const nextDetail =
    detail && detail.calendar_id === calendarId && detail.event_id === eventId
      ? {
          ...detail,
          self_response_status: responseStatus,
          attendees: detail.attendees.map((attendee) =>
            attendee.self ? { ...attendee, response_status: responseStatus } : attendee
          ),
        }
      : detail;
  if (!weekly) {
    return { ...data, event_detail: nextDetail };
  }
  return {
    ...data,
    event_detail: nextDetail,
    weekly_calendar: {
      ...weekly,
      days: weekly.days.map((day) => ({
        ...day,
        timed_events: day.timed_events.map((ev) =>
          ev.calendar_id === calendarId && ev.event_id === eventId
            ? { ...ev, attendee_response_status: responseStatus }
            : ev
        ),
      })),
    },
  };
}

function optimisticRescheduleEvent(
  data: DashboardData,
  calendarId: string,
  eventId: string,
  start: string,
  end: string
): DashboardData {
  const weekly = data.weekly_calendar;
  if (!weekly) return data;
  return {
    ...data,
    weekly_calendar: {
      ...weekly,
      days: weekly.days.map((day) => ({
        ...day,
        timed_events: day.timed_events.map((ev) =>
          ev.calendar_id === calendarId && ev.event_id === eventId ? { ...ev, start, end } : ev
        ),
      })),
    },
  };
}

function optimisticCancelEvent(data: DashboardData, calendarId: string, eventId: string): DashboardData {
  const weekly = data.weekly_calendar;
  if (!weekly) return data;
  return {
    ...data,
    event_detail: data.event_detail?.event_id === eventId ? undefined : data.event_detail,
    weekly_calendar: {
      ...weekly,
      days: weekly.days.map((day) => ({
        ...day,
        timed_events: day.timed_events.filter(
          (ev) => !(ev.calendar_id === calendarId && ev.event_id === eventId)
        ),
        all_day_events: day.all_day_events.filter(
          (ev) => !(ev.calendar_id === calendarId && ev.event_id === eventId)
        ),
      })),
    },
  };
}

function optimisticPatchEmail(
  data: DashboardData,
  messageId: string,
  patch: {
    addLabels?: string[];
    removeLabels?: string[];
    isUnread?: boolean;
  }
): DashboardData {
  const add = new Set((patch.addLabels || []).filter(Boolean));
  const remove = new Set((patch.removeLabels || []).filter(Boolean));
  const applyLabels = (labels: string[]): string[] => {
    const merged = new Set(labels || []);
    for (const label of add) merged.add(label);
    for (const label of remove) merged.delete(label);
    return Array.from(merged);
  };

  let nextEmailDetail = data.email_detail;
  if (nextEmailDetail?.message_id === messageId) {
    const updatedLabels = applyLabels(nextEmailDetail.labels || []);
    const isUnread =
      patch.isUnread !== undefined ? patch.isUnread : updatedLabels.includes("UNREAD");
    nextEmailDetail = {
      ...nextEmailDetail,
      labels: updatedLabels,
      is_unread: isUnread,
    };
  }

  let nextDashboard = data.dashboard;
  if (nextDashboard) {
    nextDashboard = {
      ...nextDashboard,
      sections: nextDashboard.sections.map((section) => {
        if (section.id !== "communications") {
          return section;
        }
        return {
          ...section,
          cards: section.cards.map((card) => {
            if (card.card_type !== "inbox") {
              return card;
            }
            const dataObj = (card.data || {}) as Record<string, unknown>;
            const messages = Array.isArray(dataObj.messages)
              ? (dataObj.messages as Array<Record<string, unknown>>)
              : [];
            const updatedMessages = messages.map((message) => {
              if (message.id !== messageId) {
                return message;
              }
              const labels = Array.isArray(message.label_ids)
                ? (message.label_ids as string[])
                : [];
              const updatedLabels = applyLabels(labels);
              const isUnread =
                patch.isUnread !== undefined ? patch.isUnread : updatedLabels.includes("UNREAD");
              return {
                ...message,
                label_ids: updatedLabels,
                is_unread: isUnread,
              };
            });
            const unreadIdsRaw = Array.isArray(dataObj.unread_message_ids)
              ? (dataObj.unread_message_ids as unknown[])
              : Array.isArray(dataObj.unreadMessageIds)
                ? (dataObj.unreadMessageIds as unknown[])
                : [];
            const unreadIdSet = new Set(
              unreadIdsRaw.filter((id): id is string => typeof id === "string" && id.length > 0)
            );
            const targetMessage = updatedMessages.find((message) => message.id === messageId);
            if (targetMessage?.is_unread) {
              unreadIdSet.add(messageId);
            } else {
              unreadIdSet.delete(messageId);
            }
            const unreadCount = updatedMessages.filter((message) => !!message.is_unread).length;
            return {
              ...card,
              data: {
                ...dataObj,
                messages: updatedMessages,
                unread_count: unreadCount,
                unread_message_ids: Array.from(unreadIdSet),
              },
            };
          }),
        };
      }),
    };
  }

  return {
    ...data,
    dashboard: nextDashboard,
    email_detail: nextEmailDetail,
  };
}

function optimisticPatchEmailDetail(
  data: DashboardData,
  messageId: string,
  patch: {
    addLabels?: string[];
    removeLabels?: string[];
    isUnread?: boolean;
  }
): DashboardData {
  const current = data.email_detail;
  if (!current || current.message_id !== messageId) {
    return data;
  }
  const add = new Set((patch.addLabels || []).filter(Boolean));
  const remove = new Set((patch.removeLabels || []).filter(Boolean));
  const merged = new Set(current.labels || []);
  for (const label of add) merged.add(label);
  for (const label of remove) merged.delete(label);
  const labels = Array.from(merged);
  const isUnread = patch.isUnread !== undefined ? patch.isUnread : labels.includes("UNREAD");
  return {
    ...data,
    email_detail: {
      ...current,
      labels,
      is_unread: isUnread,
    },
  };
}

function syncInboxMessageFromEmailDetail(
  data: DashboardData,
  detail: NonNullable<DashboardData["email_detail"]>
): DashboardData {
  const dashboard = data.dashboard;
  if (!dashboard) {
    return data;
  }

  let updated = false;
  const nextDashboard = {
    ...dashboard,
    sections: dashboard.sections.map((section) => {
      if (section.id !== "communications") {
        return section;
      }
      return {
        ...section,
        cards: section.cards.map((card) => {
          if (card.card_type !== "inbox") {
            return card;
          }
          const dataObj = (card.data || {}) as Record<string, unknown>;
          const messages = Array.isArray(dataObj.messages)
            ? (dataObj.messages as Array<Record<string, unknown>>)
            : [];
          const nextMessages = messages.map((message) => {
            if (message.id !== detail.message_id) {
              return message;
            }
            updated = true;
            return {
              ...message,
              label_ids: detail.labels || [],
              is_unread: !!detail.is_unread,
            };
          });
          if (!updated) {
            return card;
          }
          const unreadIdsRaw = Array.isArray(dataObj.unread_message_ids)
            ? (dataObj.unread_message_ids as unknown[])
            : Array.isArray(dataObj.unreadMessageIds)
              ? (dataObj.unreadMessageIds as unknown[])
              : [];
          const unreadIdSet = new Set(
            unreadIdsRaw.filter((id): id is string => typeof id === "string" && id.length > 0)
          );
          if (detail.is_unread) {
            unreadIdSet.add(detail.message_id);
          } else {
            unreadIdSet.delete(detail.message_id);
          }
          const unreadCount = nextMessages.filter((message) => !!message.is_unread).length;
          return {
            ...card,
            data: {
              ...dataObj,
              messages: nextMessages,
              unread_count: unreadCount,
              unread_message_ids: Array.from(unreadIdSet),
            },
          };
        }),
      };
    }),
  };

  if (!updated) {
    return data;
  }
  return {
    ...data,
    dashboard: nextDashboard,
  };
}

function extractDashboardData(result: unknown): DashboardData | null {
  const payload = extractObjectPayload(result);
  if (!payload) {
    return null;
  }
  return normalizeDashboardData(payload);
}

function normalizeDashboardData(raw: unknown): DashboardData | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }

  const obj = raw as Record<string, unknown>;

  // Handle dashboard payload: has sections+state, and may include weekly_calendar.
  if ("sections" in obj && "state" in obj) {
    const result: DashboardData = {
      dashboard: obj as unknown as DashboardData["dashboard"],
    };
    if (obj.weekly_calendar && typeof obj.weekly_calendar === "object") {
      result.weekly_calendar = obj.weekly_calendar as DashboardData["weekly_calendar"];
    }
    return result;
  }

  if ("week_start" in obj && "week_end" in obj && "days" in obj) {
    return { weekly_calendar: obj as unknown as DashboardData["weekly_calendar"] };
  }

  if ("weekly_calendar" in obj || "dashboard" in obj || "event_detail" in obj || "email_detail" in obj) {
    return obj as unknown as DashboardData;
  }

  if ("event_id" in obj && "calendar_id" in obj && "attendees" in obj) {
    return { event_detail: obj as unknown as DashboardData["event_detail"] };
  }

  if ("message_id" in obj && "from_value" in obj && "subject" in obj) {
    return { email_detail: obj as unknown as DashboardData["email_detail"] };
  }

  if ("id" in obj && "from" in obj && "subject" in obj) {
    const labelIds = Array.isArray(obj.label_ids) ? (obj.label_ids as string[]) : [];
    const attachments = Array.isArray(obj.attachments) ? obj.attachments : [];
    return {
      email_detail: {
        message_id: String(obj.id || ""),
        thread_id: typeof obj.thread_id === "string" ? obj.thread_id : null,
        subject: String(obj.subject || "(No subject)"),
        from_value: String(obj.from || "(Unknown sender)"),
        to: typeof obj.to === "string" ? obj.to : null,
        cc: null,
        bcc: null,
        date: typeof obj.date === "string" ? obj.date : null,
        snippet: typeof obj.snippet === "string" ? obj.snippet : null,
        text_body: typeof obj.text_body === "string" ? obj.text_body : null,
        html_body: typeof obj.html_body === "string" ? obj.html_body : null,
        attachments: attachments
          .map((attachment) => {
            if (!attachment || typeof attachment !== "object") {
              return null;
            }
            const item = attachment as Record<string, unknown>;
            const attachmentId =
              (typeof item.attachment_id === "string" && item.attachment_id) ||
              (typeof item.download_id === "string" && item.download_id) ||
              "";
            if (!attachmentId) {
              return null;
            }
            return {
              filename:
                (typeof item.filename === "string" && item.filename) || "attachment",
              mime_type: typeof item.mime_type === "string" ? item.mime_type : null,
              size: typeof item.size === "number" ? item.size : null,
              attachment_id: attachmentId,
            };
          })
          .filter((item): item is {
            filename: string;
            mime_type: string | null;
            size: number | null;
            attachment_id: string;
          } => item !== null),
        labels: labelIds,
        is_unread: labelIds.includes("UNREAD"),
      },
    };
  }

  return null;
}

function extractObjectPayload(result: unknown): Record<string, unknown> | null {
  if (!result || typeof result !== "object") {
    return null;
  }

  const candidate = result as {
    structuredContent?: unknown;
    data?: unknown;
    content?: Array<{ type?: string; text?: string }>;
  };

  if (candidate.structuredContent && typeof candidate.structuredContent === "object") {
    return candidate.structuredContent as Record<string, unknown>;
  }

  if (candidate.data && typeof candidate.data === "object") {
    return candidate.data as Record<string, unknown>;
  }

  const textContent = (candidate.content || []).find(
    (item) => item.type === "text" && typeof item.text === "string"
  );
  if (!textContent?.text) {
    return null;
  }
  try {
    const parsed = JSON.parse(textContent.text) as unknown;
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function extractCalendarCatalog(result: unknown): CalendarCatalogItem[] {
  const payload = extractObjectPayload(result);
  if (!payload) {
    return [];
  }
  const itemsRaw = payload.items;
  if (!Array.isArray(itemsRaw)) {
    return [];
  }
  const normalized: CalendarCatalogItem[] = [];
  for (const entry of itemsRaw) {
    if (!entry || typeof entry !== "object") {
      continue;
    }
    const item = entry as Record<string, unknown>;
    if (typeof item.id !== "string" || typeof item.summary !== "string") {
      continue;
    }
    normalized.push({
      id: item.id,
      summary: item.summary,
      primary: item.primary === true,
      access_role: typeof item.accessRole === "string" ? item.accessRole : undefined,
      background_color: typeof item.backgroundColor === "string" ? item.backgroundColor : undefined,
      foreground_color: typeof item.foregroundColor === "string" ? item.foregroundColor : undefined,
    });
  }
  return normalized;
}

function ensureDownloadFilename(fileName: string, mimeType?: string): string {
  const trimmedName = (fileName || "").trim();
  const sanitizedName = (trimmedName || "attachment").replace(/[\\/:*?"<>|]/g, "_");

  if (/\.[A-Za-z0-9]{1,12}$/.test(sanitizedName)) {
    return sanitizedName;
  }

  const normalizedMimeType = String(mimeType || "")
    .split(";")[0]
    .trim()
    .toLowerCase();
  const inferredExtension = MIME_EXTENSION_MAP[normalizedMimeType];

  return inferredExtension ? `${sanitizedName}${inferredExtension}` : sanitizedName;
}

function readDashboardState(data: DashboardData): {
  selected_calendar_ids: string[];
  include_weekend: boolean;
  timezone: string;
} {
  const state = (data.dashboard?.state || {}) as Record<string, unknown>;
  const selectedRaw = state.selected_calendars;
  const includeWeekendRaw = state.include_weekend;
  const timezoneRaw = state.timezone;
  return {
    selected_calendar_ids: Array.isArray(selectedRaw)
      ? selectedRaw.filter((item): item is string => typeof item === "string")
      : ["primary"],
    include_weekend: typeof includeWeekendRaw === "boolean" ? includeWeekendRaw : true,
    timezone: typeof timezoneRaw === "string" ? timezoneRaw : "UTC",
  };
}

function toLocalInputValue(iso: string): string {
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) {
    return "";
  }
  return toInputLocalString(dt);
}

function toInputLocalString(date: Date): string {
  const pad = (value: number) => value.toString().padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function localInputToIso(value: string): string {
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) {
    return value;
  }
  return dt.toISOString();
}

function defaultStartLocal(seedDate?: string): Date {
  if (!seedDate) {
    return roundUpToNextHalfHour(new Date());
  }
  const parsed = new Date(`${seedDate}T09:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return roundUpToNextHalfHour(new Date());
  }
  return parsed;
}

function roundUpToNextHalfHour(value: Date): Date {
  const result = new Date(value.getTime());
  result.setSeconds(0, 0);
  const minutes = result.getMinutes();
  if (minutes === 0 || minutes === 30) {
    return result;
  }
  result.setMinutes(minutes < 30 ? 30 : 60, 0, 0);
  return result;
}

function parseAttendeesCsv(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function makeIdempotencyKey(prefix: string): string {
  const random = new Uint8Array(12);
  crypto.getRandomValues(random);
  const suffix = Array.from(random, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${prefix}-${Date.now()}-${suffix}`;
}

function shiftIsoMinutes(iso: string, minutes: number): string {
  const dt = new Date(iso);
  return new Date(dt.getTime() + minutes * 60_000).toISOString();
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Honor the host's container sizing. Fixed dimensions: fill exactly that box and
 * scroll inside it. Flexible (max*) or unbounded: size to content; the SDK's
 * auto-resize reports it through ui/notifications/size-changed.
 */
function applyContainerDimensions(dimensions: NonNullable<McpUiHostContext["containerDimensions"]>) {
  const html = document.documentElement;
  const fixedHeight = "height" in dimensions && typeof dimensions.height === "number" ? dimensions.height : undefined;
  const fixedWidth = "width" in dimensions && typeof dimensions.width === "number" ? dimensions.width : undefined;
  const maxHeight = "maxHeight" in dimensions && typeof dimensions.maxHeight === "number" ? dimensions.maxHeight : undefined;
  const maxWidth = "maxWidth" in dimensions && typeof dimensions.maxWidth === "number" ? dimensions.maxWidth : undefined;
  html.dataset.sizing = fixedHeight !== undefined ? "fixed" : maxHeight !== undefined || maxWidth !== undefined ? "flexible" : "unbounded";
  html.style.height = fixedHeight !== undefined ? `${fixedHeight}px` : "";
  html.style.width = fixedWidth !== undefined ? `${fixedWidth}px` : "";
  html.style.maxWidth = maxWidth !== undefined ? `${maxWidth}px` : "";
}
