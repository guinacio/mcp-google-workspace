import { THEME_CSS, applyTheme } from "./theme";
import { RENDER_CSS, renderLoading, renderDashboard, setActionHandler } from "./render";
import type { UiAction } from "./render";
import type { DashboardData, ParentMessage } from "./types";

const style = document.createElement("style");
style.textContent = THEME_CSS + RENDER_CSS;
document.head.appendChild(style);

const root = document.getElementById("app")!;
const UI_SESSION_STORAGE_KEY = "mcp-dashboard-ui-session-id";

const params = new URLSearchParams(window.location.search);
const isStandalone =
  params.get("mode") === "standalone" ||
  document.documentElement.dataset.mcpMode === "standalone";

if (isStandalone) {
  initStandaloneMode();
} else {
  void initMcpMode();
}

function initStandaloneMode() {
  applyTheme("dark");
  renderLoading(root);

  setActionHandler((action: UiAction) => {
    if (action.type === "chat") {
      window.parent.postMessage({ type: "inject_chat_message", text: action.text }, "*");
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
    }

    window.parent.postMessage({ type: "inject_chat_message", text }, "*");
  });

  window.addEventListener("message", (e: MessageEvent<ParentMessage>) => {
    if (!e.data || typeof e.data !== "object") return;

    switch (e.data.type) {
      case "dashboard_data": {
        const data = e.data.data as DashboardData;
        if (data && (data.weekly_calendar || data.dashboard)) {
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

  window.parent.postMessage({ type: "request_dashboard_data" }, "*");
}

async function initMcpMode() {
  applyTheme("dark");
  renderLoading(root);
  let hasRenderedFromToolResult = false;
  let currentData: DashboardData = {};

  try {
    const {
      App,
      applyDocumentTheme,
      applyHostStyleVariables,
      applyHostFonts,
    } = await import("@modelcontextprotocol/ext-apps");

    const app = new App(
      { name: "Workspace Dashboard", version: "1.0.0" },
      {}
    );

    const uiSessionId = getOrCreateSessionId();
    const refreshFull = async () => {
      currentData = await fetchAndRenderDashboardData(app, uiSessionId, currentData, "full");
    };
    const refreshWeekly = async () => {
      currentData = await fetchAndRenderDashboardData(app, uiSessionId, currentData, "weekly");
    };

    const makeIdempotencyKey = (prefix: string) =>
      `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

    const shiftIsoMinutes = (iso: string, minutes: number): string => {
      const dt = new Date(iso);
      return new Date(dt.getTime() + minutes * 60_000).toISOString();
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

    setActionHandler((action: UiAction) => {
      if (action.type === "close_event_detail") {
        currentData = { ...currentData, event_detail: undefined };
        renderDashboard(root, currentData);
        return;
      }

      if (action.type === "close_email_detail") {
        currentData = { ...currentData, email_detail: undefined };
        renderDashboard(root, currentData);
        return;
      }

      if (action.type === "select_event") {
        void withUiPending(async () => {
          const result = await app.callServerTool({
            name: "apps_get_event_detail",
            arguments: {
              request: {
                session_id: uiSessionId,
                calendar_id: action.calendarId,
                event_id: action.eventId,
              },
            },
          });
          const parsed = extractDashboardData(result);
          if (parsed?.event_detail) {
            currentData = {
              ...currentData,
              event_detail: parsed.event_detail,
              email_detail: undefined,
            };
            renderDashboard(root, currentData);
          }
        }).catch((err) => {
          console.warn("Failed to load event details:", err);
        });
        return;
      }

      if (action.type === "select_email") {
        void withUiPending(async () => {
          const result = await app.callServerTool({
            name: "apps_get_email_detail",
            arguments: {
              request: {
                session_id: uiSessionId,
                message_id: action.messageId,
              },
            },
          });
          const parsed = extractDashboardData(result);
          if (parsed?.email_detail) {
            currentData = {
              ...currentData,
              email_detail: parsed.email_detail,
              event_detail: undefined,
            };
            renderDashboard(root, currentData);
          }
        }).catch((err) => {
          console.warn("Failed to load email details:", err);
        });
        return;
      }

      if (action.type === "calendar_rsvp") {
        const idempotencyKey = makeIdempotencyKey(`rsvp-${action.eventId}-${action.responseStatus}`);
        currentData = optimisticSetRsvp(
          currentData,
          action.calendarId,
          action.eventId,
          action.responseStatus
        );
        renderDashboard(root, currentData);
        void withUiPending(async () => {
          await app.callServerTool({
            name: "apps_respond_to_event",
            arguments: {
              request: {
                session_id: uiSessionId,
                calendar_id: action.calendarId,
                event_id: action.eventId,
                response_status: action.responseStatus,
                idempotency_key: idempotencyKey,
              },
            },
          });
          await refreshWeekly();
        }).catch((err) => {
          console.warn("Failed to update event RSVP:", err);
        });
        return;
      }

      if (action.type === "calendar_reschedule") {
        const nextStart = shiftIsoMinutes(action.start, action.shiftMinutes);
        const nextEnd = shiftIsoMinutes(action.end, action.shiftMinutes);
        const idempotencyKey = makeIdempotencyKey(`reschedule-${action.eventId}`);
        currentData = optimisticRescheduleEvent(
          currentData,
          action.calendarId,
          action.eventId,
          nextStart,
          nextEnd
        );
        renderDashboard(root, currentData);
        void withUiPending(async () => {
          await app.callServerTool({
            name: "apps_reschedule_meeting",
            arguments: {
              request: {
                session_id: uiSessionId,
                calendar_id: action.calendarId,
                event_id: action.eventId,
                start: nextStart,
                end: nextEnd,
                timezone: action.timezone,
                idempotency_key: idempotencyKey,
              },
            },
          });
          await refreshWeekly();
        }).catch((err) => {
          console.warn("Failed to reschedule event:", err);
        });
        return;
      }

      if (action.type === "calendar_cancel") {
        const idempotencyKey = makeIdempotencyKey(`cancel-${action.eventId}`);
        currentData = optimisticCancelEvent(currentData, action.calendarId, action.eventId);
        renderDashboard(root, currentData);
        void withUiPending(async () => {
          await app.callServerTool({
            name: "apps_cancel_meeting",
            arguments: {
              request: {
                session_id: uiSessionId,
                calendar_id: action.calendarId,
                event_id: action.eventId,
                confirm: true,
                idempotency_key: idempotencyKey,
              },
            },
          });
          await refreshWeekly();
        }).catch((err) => {
          console.warn("Failed to cancel event:", err);
        });
        return;
      }

      if (action.type === "week_nav") {
        const toolName =
          action.direction === "prev"
            ? "apps_prev_range"
            : action.direction === "next"
              ? "apps_next_range"
              : "apps_today";
        void withUiPending(async () => {
          await app.callServerTool({ name: toolName, arguments: { session_id: uiSessionId } });
          await refreshWeekly();
        }).catch((err) => {
          console.warn(`Failed to navigate week via ${toolName}:`, err);
        });
        return;
      }

      void refreshFull().catch((err) => {
        console.warn("Failed to refresh dashboard via apps_get_dashboard:", err);
      });
    });

    app.ontoolresult = (result) => {
      const data = extractDashboardData(result);
      if (data && (data.weekly_calendar || data.dashboard || data.event_detail || data.email_detail)) {
        hasRenderedFromToolResult = true;
        currentData = mergeDashboardData(currentData, data);
        renderDashboard(root, currentData);
      }
    };

    app.onhostcontextchanged = (ctx) => {
      if (ctx.theme) applyDocumentTheme(ctx.theme);
      if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
      if (ctx.styles?.css?.fonts) applyHostFonts(ctx.styles.css.fonts);
      if (ctx.safeAreaInsets) {
        const { top, right, bottom, left } = ctx.safeAreaInsets;
        document.body.style.padding = `${top}px ${right}px ${bottom}px ${left}px`;
      }
    };

    app.onteardown = async () => ({});

    await app.connect();

    window.setTimeout(async () => {
      if (hasRenderedFromToolResult) {
        return;
      }
      try {
        await refreshFull();
      } catch (err) {
        console.warn("Fallback apps_get_dashboard call failed:", err);
      }
    }, 800);
  } catch (err) {
    console.warn("MCP ext-apps not available:", err);
    root.innerHTML = `
      <div class="loading-state">
        <div>MCP app connection failed.</div>
      </div>
    `;
  }
}

async function fetchAndRenderDashboardData(
  app: {
    callServerTool: (args: { name: string; arguments: Record<string, unknown> }) => Promise<unknown>;
  },
  sessionId: string,
  current: DashboardData,
  mode: "full" | "weekly"
): Promise<DashboardData> {
  const merged: DashboardData = { ...current, event_detail: undefined, email_detail: undefined };
  if (mode === "weekly") {
    const weeklyResult = await app.callServerTool({
      name: "apps_get_weekly_calendar_view",
      arguments: { session_id: sessionId },
    });
    const parsed = extractDashboardData(weeklyResult);
    if (parsed?.weekly_calendar) {
      merged.weekly_calendar = parsed.weekly_calendar;
      renderDashboard(root, merged);
    }
    return merged;
  }

  // get_dashboard now includes weekly_calendar, so one call provides both.
  const dashboardResult = await app.callServerTool({
    name: "apps_get_dashboard",
    arguments: { session_id: sessionId },
  });
  const parsed = extractDashboardData(dashboardResult);
  if (parsed) {
    if (parsed.dashboard) merged.dashboard = parsed.dashboard;
    if (parsed.weekly_calendar) merged.weekly_calendar = parsed.weekly_calendar;
  }

  if (merged.dashboard || merged.weekly_calendar) {
    renderDashboard(root, merged);
  }
  return merged;
}

function mergeDashboardData(base: DashboardData, incoming: DashboardData): DashboardData {
  return {
    weekly_calendar: incoming.weekly_calendar ?? base.weekly_calendar,
    dashboard: incoming.dashboard ?? base.dashboard,
    event_detail: incoming.event_detail ?? base.event_detail,
    email_detail: incoming.email_detail ?? base.email_detail,
    generated_at: incoming.generated_at ?? base.generated_at,
  };
}

function optimisticSetRsvp(
  data: DashboardData,
  calendarId: string,
  eventId: string,
  responseStatus: "accepted" | "tentative" | "declined"
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

function getOrCreateSessionId(): string {
  try {
    const existing = window.localStorage.getItem(UI_SESSION_STORAGE_KEY);
    if (existing) return existing;
  } catch {
    // Ignore storage issues and generate ephemeral id below.
  }
  const generated = `ui-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  try {
    window.localStorage.setItem(UI_SESSION_STORAGE_KEY, generated);
  } catch {
    // Ignore storage issues.
  }
  return generated;
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

  return null;
}
