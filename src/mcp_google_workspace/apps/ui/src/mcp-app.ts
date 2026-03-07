import { THEME_CSS, applyTheme } from "./theme";
import { RENDER_CSS, renderLoading, renderDashboard, setActionHandler } from "./render";
import type { UiAction, RenderOptions } from "./render";
import type {
  CalendarCatalogItem,
  DashboardData,
  EventEditorDraft,
  ParentMessage,
  UiToolCapabilities,
} from "./types";

type ToolOperation =
  | "getDashboard"
  | "getWeeklyCalendar"
  | "getEventDetail"
  | "getEmailDetail"
  | "getEmailAttachment"
  | "respondToEvent"
  | "rescheduleMeeting"
  | "cancelMeeting"
  | "patchState"
  | "nextRange"
  | "prevRange"
  | "today"
  | "listCalendars"
  | "createEvent"
  | "updateEvent"
  | "deleteEvent"
  | "markEmailRead"
  | "markEmailUnread"
  | "moveEmail"
  | "deleteEmail"
  | "untrashEmail"
  | "markEmailSpam"
  | "markEmailNotSpam";

type ToolRegistry = Partial<Record<ToolOperation, string>>;

type RequestCapable = {
  request: (request: { method: string; params?: Record<string, unknown> }) => Promise<unknown>;
};

type ServerToolCapable = {
  callServerTool: (args: { name: string; arguments?: Record<string, unknown> }) => Promise<unknown>;
};

const TOOL_CANDIDATES: Record<ToolOperation, string[]> = {
  getDashboard: ["apps_get_dashboard", "get_dashboard"],
  getWeeklyCalendar: ["apps_get_weekly_calendar_view", "get_weekly_calendar_view"],
  getEventDetail: ["apps_get_event_detail", "get_event_detail"],
  getEmailDetail: ["apps_get_email_detail", "get_email_detail", "gmail_read_email", "read_email"],
  getEmailAttachment: ["apps_get_email_attachment", "get_email_attachment"],
  respondToEvent: ["apps_respond_to_event", "respond_to_event"],
  rescheduleMeeting: ["apps_reschedule_meeting", "reschedule_meeting"],
  cancelMeeting: ["apps_cancel_meeting", "cancel_meeting"],
  patchState: ["apps_patch_state", "patch_state"],
  nextRange: ["apps_next_range", "next_range"],
  prevRange: ["apps_prev_range", "prev_range"],
  today: ["apps_today", "today"],
  listCalendars: ["calendar_list_calendars", "list_calendars"],
  createEvent: [
    "calendar_create_event",
    "create_event",
    "apps_create_meeting_from_slot",
    "create_meeting_from_slot",
  ],
  updateEvent: ["calendar_update_event", "update_event", "apps_reschedule_meeting", "reschedule_meeting"],
  deleteEvent: ["calendar_delete_event", "delete_event"],
  markEmailRead: ["gmail_mark_as_read", "mark_as_read"],
  markEmailUnread: ["gmail_mark_as_unread", "mark_as_unread"],
  moveEmail: ["gmail_move_email", "move_email"],
  deleteEmail: ["gmail_delete_email", "delete_email"],
  untrashEmail: ["gmail_untrash_email", "untrash_email"],
  markEmailSpam: ["gmail_mark_as_spam", "mark_as_spam"],
  markEmailNotSpam: ["gmail_mark_as_not_spam", "mark_as_not_spam"],
};

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
    } else if (action.type === "open_event_editor") {
      text = `Open ${action.mode} event editor.`;
    } else if (action.type === "close_event_editor") {
      text = "Close event editor.";
    } else if (action.type === "save_event_editor") {
      text = `${action.draft.mode === "create" ? "Create" : "Update"} event ${action.draft.summary}.`;
    } else if (action.type === "toggle_weekend") {
      text = `Set include weekend to ${action.include_weekend}.`;
    } else if (action.type === "set_selected_calendars") {
      text = `Set selected calendars: ${action.selected_calendar_ids.join(", ")}`;
    } else if (action.type === "open_attachment") {
      text = `Open attachment: ${action.url}`;
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

    window.parent.postMessage({ type: "inject_chat_message", text }, "*");
  });

  window.addEventListener("message", (e: MessageEvent<ParentMessage>) => {
    if (!e.data || typeof e.data !== "object") return;

    switch (e.data.type) {
      case "dashboard_data": {
        const data = e.data.data as DashboardData;
        if (data && (data.weekly_calendar || data.dashboard)) {
          const state = readDashboardState(data);
          renderDashboard(root, data, {
            include_weekend: state.include_weekend,
            selected_calendar_ids: state.selected_calendar_ids,
            calendar_catalog: data.calendar_catalog?.items ?? [],
            tool_capabilities: data.tool_capabilities,
          });
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
  let toolRegistry: ToolRegistry = {};
  const renderOptions: RenderOptions = {
    include_weekend: true,
    selected_calendar_ids: [],
    calendar_catalog: [],
  };

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
    ) as unknown as RequestCapable &
      ServerToolCapable & {
        connect: () => Promise<void>;
        openLink: (params: { url: string }) => Promise<{ isError?: boolean; content?: unknown[] }>;
        downloadFile: (params: {
          contents: Array<
            | {
                type: "resource_link";
                name: string;
                uri: string;
                mimeType?: string;
              }
            | {
                type: "resource";
                resource: {
                  uri: string;
                  mimeType?: string;
                  text?: string;
                  blob?: string;
                };
              }
          >;
        }) => Promise<{ isError?: boolean; content?: unknown[] }>;
        ontoolresult: ((result: unknown) => void) | null;
        onhostcontextchanged:
          | ((ctx: {
              theme?: "dark" | "light";
              styles?: { variables?: Record<string, string>; css?: { fonts?: string } };
              safeAreaInsets?: { top: number; right: number; bottom: number; left: number };
            }) => void)
          | null;
        onteardown: (() => Promise<Record<string, unknown>>) | null;
      };

    const uiSessionId = getOrCreateSessionId();
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

    const setUiMessage = (message: string | undefined, kind: "notice" | "error") => {
      if (kind === "notice") {
        currentData = { ...currentData, ui_notice: message, ui_error: undefined };
      } else {
        currentData = { ...currentData, ui_error: message, ui_notice: undefined };
      }
    };

    const isToolNotFoundError = (err: unknown): boolean => {
      const message = String(err || "");
      return (
        message.includes("-32601") ||
        message.toLowerCase().includes("method not found") ||
        message.toLowerCase().includes("tool not found") ||
        message.toLowerCase().includes("unknown tool")
      );
    };

    const callToolForOperation = async (
      operation: ToolOperation,
      args: Record<string, unknown>
    ): Promise<unknown> => {
      const preferred = toolRegistry[operation];
      const candidates = preferred
        ? [preferred, ...TOOL_CANDIDATES[operation].filter((name) => name !== preferred)]
        : [...TOOL_CANDIDATES[operation]];
      let lastError: unknown;
      for (const toolName of candidates) {
        try {
          const result = await app.callServerTool({ name: toolName, arguments: args });
          toolRegistry[operation] = toolName;
          return result;
        } catch (err) {
          lastError = err;
          if (!isToolNotFoundError(err)) {
            throw err;
          }
        }
      }
      throw lastError || new Error(`No valid tool found for operation ${operation}.`);
    };

    const renderCurrent = () => {
      const state = readDashboardState(currentData);
      renderOptions.include_weekend = state.include_weekend;
      renderOptions.selected_calendar_ids = state.selected_calendar_ids;
      currentData.tool_capabilities = computeToolCapabilities(toolRegistry);
      renderOptions.tool_capabilities = currentData.tool_capabilities;
      renderDashboard(root, currentData, renderOptions);
    };

    const refreshFull = async () => {
      currentData = await fetchAndRenderDashboardData(app, uiSessionId, currentData, "full", toolRegistry);
      renderCurrent();
    };
    const refreshWeekly = async () => {
      currentData = await fetchAndRenderDashboardData(app, uiSessionId, currentData, "weekly", toolRegistry);
      renderCurrent();
    };

    const loadCalendars = async () => {
      const listTool = toolRegistry.listCalendars;
      if (!listTool) {
        return;
      }
      const result = await app.callServerTool({ name: listTool, arguments: {} });
      const calendars = extractCalendarCatalog(result);
      if (calendars.length) {
        renderOptions.calendar_catalog = calendars;
        currentData = {
          ...currentData,
          calendar_catalog: {
            items: calendars,
            fetched_at_utc: new Date().toISOString(),
          },
        };
      }
    };

    const updateStatePatch = async (patch: Record<string, unknown>) => {
      const toolName = resolveTool(toolRegistry, "patchState");
      if (!toolName) {
        throw new Error("State patch tool is unavailable.");
      }
      await app.callServerTool({
        name: toolName,
        arguments: {
          session_id: uiSessionId,
          ...patch,
        },
      });
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

      if (draft.mode === "create") {
        const createTool = resolveTool(toolRegistry, "createEvent");
        if (!createTool) {
          throw new Error("Create event tool is unavailable.");
        }
        const idempotencyKey = makeIdempotencyKey("create");
        if (createTool.includes("apps_create_meeting_from_slot")) {
          await app.callServerTool({
            name: createTool,
            arguments: {
              session_id: uiSessionId,
              calendar_id: draft.calendar_id,
              title: draft.summary,
              start: startIso,
              end: endIso,
              timezone: draft.timezone,
              description: draft.description || undefined,
              attendees,
              create_conference: draft.create_conference,
              idempotency_key: idempotencyKey,
            },
          });
        } else {
          await app.callServerTool({
            name: createTool,
            arguments: {
              calendar_id: draft.calendar_id,
              summary: draft.summary,
              start_datetime: startIso,
              end_datetime: endIso,
              timezone: draft.timezone,
              description: draft.description || undefined,
              location: draft.location || undefined,
              attendees: attendees.map((email) => ({ email })),
              conference_data: draft.create_conference
                ? {
                    createRequest: {
                      requestId: idempotencyKey,
                      conferenceSolutionKey: { type: "hangoutsMeet" },
                    },
                  }
                : undefined,
              send_updates: "all",
              on_conflict: "suggest_next_slot",
            },
          });
        }
        currentData = { ...currentData, event_editor: undefined };
        setUiMessage("Event created.", "notice");
        await refreshWeekly();
        return;
      }

      const eventId = draft.event_id;
      if (!eventId) {
        throw new Error("Missing event id for edit.");
      }
      const updateTool = resolveTool(toolRegistry, "updateEvent");
      if (!updateTool) {
        throw new Error("Update event tool is unavailable.");
      }
      await app.callServerTool({
        name: updateTool,
        arguments: {
          event_id: eventId,
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
        },
      });
      currentData = { ...currentData, event_editor: undefined };
      setUiMessage("Event updated.", "notice");
      await refreshWeekly();
    };

    const refreshEmailDetailIfOpen = async (messageId: string) => {
      if (currentData.email_detail?.message_id !== messageId) {
        return;
      }
      const result = await callToolForOperation("getEmailDetail", {
        session_id: uiSessionId,
        message_id: messageId,
      });
      const parsed = extractDashboardData(result);
      if (parsed?.email_detail) {
        currentData = syncInboxMessageFromEmailDetail({
          ...currentData,
          email_detail: parsed.email_detail,
        }, parsed.email_detail);
      }
    };

    const runEmailMutation = async (params: {
      toolOperation: ToolOperation;
      messageId: string;
      argumentsBuilder: () => Record<string, unknown>;
      successNotice: string;
      patch?: {
        addLabels?: string[];
        removeLabels?: string[];
        isUnread?: boolean;
      };
    }) => {
      const patch = params.patch || {};
      currentData = optimisticPatchEmail(currentData, params.messageId, patch);
      renderCurrent();

      await callToolForOperation(params.toolOperation, params.argumentsBuilder());
      await refreshFull();
      try {
        await refreshEmailDetailIfOpen(params.messageId);
      } catch {
        // Keep optimistic state when immediate post-mutation detail fetch is stale/unavailable.
      }
      currentData = optimisticPatchEmailDetail(currentData, params.messageId, patch);
      setUiMessage(params.successNotice, "notice");
      renderCurrent();
    };

    setActionHandler((action: UiAction) => {
      if (action.type === "close_event_detail") {
        currentData = { ...currentData, event_detail: undefined };
        renderCurrent();
        return;
      }

      if (action.type === "close_email_detail") {
        currentData = { ...currentData, email_detail: undefined };
        renderCurrent();
        return;
      }

      if (action.type === "open_attachment") {
        void withUiPending(async () => {
          const result = await app.openLink({ url: action.url });
          if (result?.isError) {
            throw new Error("Host could not open attachment link.");
          }
        }).catch((err: unknown) => {
          setUiMessage(`Failed to open attachment: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      if (action.type === "download_attachment") {
        void withUiPending(async () => {
          const resourceLink: {
            type: "resource_link";
            name: string;
            uri: string;
            mimeType?: string;
          } = {
            type: "resource_link",
            name: action.name || "attachment",
            uri: action.url,
          };
          if (action.mimeType) {
            resourceLink.mimeType = action.mimeType;
          }
          const result = await app.downloadFile({
            contents: [resourceLink],
          });
          if (result?.isError) {
            const openResult = await app.openLink({ url: action.url });
            if (openResult?.isError) {
              throw new Error("Host could not download or open attachment.");
            }
            setUiMessage(`Host denied direct download. Opened link for ${action.name}.`, "notice");
            renderCurrent();
            return;
          }
          setUiMessage(`Download started: ${action.name}`, "notice");
          renderCurrent();
        }).catch((err: unknown) => {
          setUiMessage(`Failed to download attachment: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      if (action.type === "email_download_attachment") {
        void withUiPending(async () => {
          const payload = await callToolForOperation("getEmailAttachment", {
            message_id: action.messageId,
            attachment_id: action.attachmentId,
          });
          const data = extractObjectPayload(payload);
          if (!data || typeof data.blob_base64 !== "string") {
            throw new Error("Attachment content is unavailable.");
          }
          const fileName =
            (typeof data.filename === "string" && data.filename) || action.filename || "attachment";
          const mimeType =
            (typeof data.mime_type === "string" && data.mime_type) || action.mimeType || "application/octet-stream";
          const safeName = fileName.replace(/[\\/:*?"<>|]/g, "_");
          const dataUri = `data:${mimeType};base64,${data.blob_base64}`;
          let result = await app.downloadFile({
            contents: [
              {
                type: "resource_link",
                name: safeName,
                uri: dataUri,
                mimeType,
              },
            ],
          });
          if (result?.isError) {
            result = await app.downloadFile({
              contents: [
                {
                  type: "resource",
                  resource: {
                    uri: `attachment://${encodeURIComponent(safeName)}`,
                    mimeType,
                    blob: data.blob_base64,
                  },
                },
              ],
            });
          }
          if (result?.isError) {
            throw new Error("Host could not download email attachment.");
          }
          setUiMessage(`Download started: ${fileName}`, "notice");
          renderCurrent();
        }).catch((err: unknown) => {
          setUiMessage(`Failed to download email attachment: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      if (action.type === "open_event_editor") {
        startEditor(action.mode, action.seed_date);
        return;
      }

      if (action.type === "close_event_editor") {
        currentData = { ...currentData, event_editor: undefined };
        renderCurrent();
        return;
      }

      if (action.type === "save_event_editor") {
        void withUiPending(async () => {
          await saveEventEditor(action.draft);
        }).catch((err: unknown) => {
          setUiMessage(`Failed to save event: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      if (action.type === "toggle_weekend") {
        const previousData = currentData;
        currentData = patchDashboardState(currentData, {
          include_weekend: action.include_weekend,
        });
        renderCurrent();
        void withUiPending(async () => {
          await updateStatePatch({ include_weekend: action.include_weekend });
          await refreshWeekly();
        }).catch((err: unknown) => {
          currentData = previousData;
          setUiMessage(`Failed to update weekend preference: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      if (action.type === "set_selected_calendars") {
        const previousData = currentData;
        currentData = patchDashboardState(currentData, {
          selected_calendars: action.selected_calendar_ids,
        });
        renderCurrent();
        void withUiPending(async () => {
          await updateStatePatch({ selected_calendars: action.selected_calendar_ids });
          await refreshFull();
        }).catch((err: unknown) => {
          currentData = previousData;
          setUiMessage(`Failed to update selected calendars: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      if (action.type === "select_event") {
        const toolName = resolveTool(toolRegistry, "getEventDetail");
        if (!toolName) {
          setUiMessage("Event detail tool is unavailable.", "error");
          renderCurrent();
          return;
        }
        void withUiPending(async () => {
          const result = await app.callServerTool({
            name: toolName,
            arguments: {
              session_id: uiSessionId,
              calendar_id: action.calendarId,
              event_id: action.eventId,
            },
          });
          const parsed = extractDashboardData(result);
          if (parsed?.event_detail) {
            currentData = {
              ...currentData,
              event_detail: parsed.event_detail,
              email_detail: undefined,
            };
            renderCurrent();
          }
        }).catch((err) => {
          setUiMessage(`Failed to load event details: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      if (action.type === "select_email") {
        void withUiPending(async () => {
          const result = await callToolForOperation("getEmailDetail", {
            session_id: uiSessionId,
            message_id: action.messageId,
          });
          const parsed = extractDashboardData(result);
          if (parsed?.email_detail) {
            currentData = syncInboxMessageFromEmailDetail({
              ...currentData,
              email_detail: parsed.email_detail,
              event_detail: undefined,
            }, parsed.email_detail);
            renderCurrent();
          }
        }).catch((err) => {
          setUiMessage(`Failed to load email details: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      if (action.type === "email_mark_read") {
        void withUiPending(async () => {
          await runEmailMutation({
            toolOperation: "markEmailRead",
            messageId: action.messageId,
            argumentsBuilder: () => ({ message_id: action.messageId }),
            successNotice: "Email marked as read.",
            patch: { removeLabels: ["UNREAD"], isUnread: false },
          });
        }).catch((err: unknown) => {
          setUiMessage(`Failed to mark as read: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      if (action.type === "email_mark_unread") {
        void withUiPending(async () => {
          await runEmailMutation({
            toolOperation: "markEmailUnread",
            messageId: action.messageId,
            argumentsBuilder: () => ({ message_id: action.messageId }),
            successNotice: "Email marked as unread.",
            patch: { addLabels: ["UNREAD"], isUnread: true },
          });
        }).catch((err: unknown) => {
          setUiMessage(`Failed to mark as unread: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      if (action.type === "email_archive") {
        void withUiPending(async () => {
          await runEmailMutation({
            toolOperation: "moveEmail",
            messageId: action.messageId,
            argumentsBuilder: () => ({
              message_id: action.messageId,
              remove_label_ids: ["INBOX"],
            }),
            successNotice: "Email archived.",
            patch: { removeLabels: ["INBOX"] },
          });
        }).catch((err: unknown) => {
          setUiMessage(`Failed to archive email: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      if (action.type === "email_trash") {
        void withUiPending(async () => {
          await runEmailMutation({
            toolOperation: "deleteEmail",
            messageId: action.messageId,
            argumentsBuilder: () => ({
              message_id: action.messageId,
              permanent: false,
            }),
            successNotice: "Email moved to trash.",
            patch: { addLabels: ["TRASH"], removeLabels: ["INBOX"] },
          });
        }).catch((err: unknown) => {
          setUiMessage(`Failed to move email to trash: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      if (action.type === "email_untrash") {
        void withUiPending(async () => {
          await runEmailMutation({
            toolOperation: "untrashEmail",
            messageId: action.messageId,
            argumentsBuilder: () => ({ message_id: action.messageId }),
            successNotice: "Email restored from trash.",
            patch: { removeLabels: ["TRASH"], addLabels: ["INBOX"] },
          });
        }).catch((err: unknown) => {
          setUiMessage(`Failed to restore email: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      if (action.type === "email_mark_spam") {
        void withUiPending(async () => {
          await runEmailMutation({
            toolOperation: "markEmailSpam",
            messageId: action.messageId,
            argumentsBuilder: () => ({ message_id: action.messageId }),
            successNotice: "Email marked as spam.",
            patch: { addLabels: ["SPAM"], removeLabels: ["INBOX"] },
          });
        }).catch((err: unknown) => {
          setUiMessage(`Failed to mark email as spam: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      if (action.type === "email_mark_not_spam") {
        void withUiPending(async () => {
          await runEmailMutation({
            toolOperation: "markEmailNotSpam",
            messageId: action.messageId,
            argumentsBuilder: () => ({
              message_id: action.messageId,
              add_to_inbox: true,
            }),
            successNotice: "Email marked as not spam.",
            patch: { removeLabels: ["SPAM"], addLabels: ["INBOX"] },
          });
        }).catch((err: unknown) => {
          setUiMessage(`Failed to mark email as not spam: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      if (action.type === "calendar_rsvp") {
        const toolName = resolveTool(toolRegistry, "respondToEvent");
        if (!toolName) {
          setUiMessage("RSVP tool is unavailable.", "error");
          renderCurrent();
          return;
        }
        const idempotencyKey = makeIdempotencyKey(`rsvp-${action.eventId}-${action.responseStatus}`);
        currentData = optimisticSetRsvp(
          currentData,
          action.calendarId,
          action.eventId,
          action.responseStatus
        );
        renderCurrent();
        void withUiPending(async () => {
          await app.callServerTool({
            name: toolName,
            arguments: {
              session_id: uiSessionId,
              calendar_id: action.calendarId,
              event_id: action.eventId,
              response_status: action.responseStatus,
              idempotency_key: idempotencyKey,
            },
          });
          await refreshWeekly();
        }).catch((err) => {
          setUiMessage(`Failed to update RSVP: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      if (action.type === "calendar_reschedule") {
        const rescheduleTool = resolveTool(toolRegistry, "rescheduleMeeting");
        const updateTool = resolveTool(toolRegistry, "updateEvent");
        if (!rescheduleTool && !updateTool) {
          setUiMessage("Reschedule tool is unavailable.", "error");
          renderCurrent();
          return;
        }
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
        renderCurrent();
        void withUiPending(async () => {
          if (rescheduleTool) {
            await app.callServerTool({
              name: rescheduleTool,
              arguments: {
                session_id: uiSessionId,
                calendar_id: action.calendarId,
                event_id: action.eventId,
                start: nextStart,
                end: nextEnd,
                timezone: action.timezone,
                idempotency_key: idempotencyKey,
              },
            });
          } else if (updateTool) {
            await app.callServerTool({
              name: updateTool,
              arguments: {
                event_id: action.eventId,
                calendar_id: action.calendarId,
                start_datetime: nextStart,
                end_datetime: nextEnd,
                timezone: action.timezone,
                send_updates: "all",
                on_conflict: "suggest_next_slot",
              },
            });
          }
          await refreshWeekly();
        }).catch((err) => {
          setUiMessage(`Failed to reschedule event: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      if (action.type === "calendar_cancel") {
        const cancelTool = resolveTool(toolRegistry, "cancelMeeting");
        const deleteTool = resolveTool(toolRegistry, "deleteEvent");
        if (!cancelTool && !deleteTool) {
          setUiMessage("Cancel/delete tool is unavailable.", "error");
          renderCurrent();
          return;
        }
        const idempotencyKey = makeIdempotencyKey(`cancel-${action.eventId}`);
        currentData = optimisticCancelEvent(currentData, action.calendarId, action.eventId);
        renderCurrent();
        void withUiPending(async () => {
          if (cancelTool) {
            await app.callServerTool({
              name: cancelTool,
              arguments: {
                session_id: uiSessionId,
                calendar_id: action.calendarId,
                event_id: action.eventId,
                confirm: true,
                idempotency_key: idempotencyKey,
              },
            });
          } else if (deleteTool) {
            await app.callServerTool({
              name: deleteTool,
              arguments: {
                calendar_id: action.calendarId,
                event_id: action.eventId,
                force: true,
                send_updates: "all",
              },
            });
          }
          await refreshWeekly();
        }).catch((err) => {
          setUiMessage(`Failed to cancel event: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      if (action.type === "week_nav") {
        const toolName =
          action.direction === "prev"
            ? resolveTool(toolRegistry, "prevRange")
            : action.direction === "next"
              ? resolveTool(toolRegistry, "nextRange")
              : resolveTool(toolRegistry, "today");
        if (!toolName) {
          setUiMessage("Navigation tool is unavailable.", "error");
          renderCurrent();
          return;
        }
        const previousData = currentData;
        void withUiPending(async () => {
          const result = await app.callServerTool({
            name: toolName,
            arguments: { session_id: uiSessionId },
          });
          const nextState = extractObjectPayload(result);
          if (nextState) {
            currentData = replaceDashboardState(currentData, {
              ...(currentData.dashboard?.state || {}),
              ...nextState,
            });
            renderCurrent();
          }
          await refreshWeekly();
        }).catch((err) => {
          currentData = previousData;
          setUiMessage(`Failed to navigate week: ${String(err)}`, "error");
          renderCurrent();
        });
        return;
      }

      void refreshFull().catch((err) => {
        setUiMessage(`Failed to refresh dashboard: ${String(err)}`, "error");
        renderCurrent();
      });
    });

    app.ontoolresult = (result) => {
      const data = extractDashboardData(result);
      if (data && (data.weekly_calendar || data.dashboard || data.event_detail || data.email_detail)) {
        hasRenderedFromToolResult = true;
        currentData = mergeDashboardData(currentData, data);
        renderCurrent();
      }
    };

    app.onhostcontextchanged = (ctx) => {
      if (ctx.theme) applyDocumentTheme(ctx.theme);
      if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables as any);
      if (ctx.styles?.css?.fonts) applyHostFonts(ctx.styles.css.fonts);
      if (ctx.safeAreaInsets) {
        const { top, right, bottom, left } = ctx.safeAreaInsets;
        document.body.style.padding = `${top}px ${right}px ${bottom}px ${left}px`;
      }
    };

    app.onteardown = async () => ({});

    await app.connect();
    toolRegistry = await discoverToolRegistry(app);
    currentData.tool_capabilities = computeToolCapabilities(toolRegistry);
    renderOptions.tool_capabilities = currentData.tool_capabilities;

    window.setTimeout(async () => {
      if (hasRenderedFromToolResult) {
        return;
      }
      try {
        await Promise.all([refreshFull(), loadCalendars()]);
        renderCurrent();
      } catch (err) {
        setUiMessage(`Initial load failed: ${String(err)}`, "error");
        renderCurrent();
      }
    }, 300);
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
  app: ServerToolCapable,
  sessionId: string,
  current: DashboardData,
  mode: "full" | "weekly",
  registry: ToolRegistry
): Promise<DashboardData> {
  const merged: DashboardData = {
    ...current,
    ui_error: undefined,
    ui_notice: undefined,
  };
  const weeklyTool = resolveTool(registry, "getWeeklyCalendar");
  const dashboardTool = resolveTool(registry, "getDashboard");
  if (mode === "weekly") {
    if (weeklyTool) {
      const weeklyResult = await app.callServerTool({
        name: weeklyTool,
        arguments: { session_id: sessionId },
      });
      const parsed = extractDashboardData(weeklyResult);
      if (parsed?.weekly_calendar) {
        merged.weekly_calendar = parsed.weekly_calendar;
      }
      return merged;
    }
    if (!dashboardTool) {
      throw new Error("No weekly/full dashboard tool available.");
    }
  }

  if (!dashboardTool) {
    throw new Error("Dashboard tool is unavailable.");
  }

  const dashboardResult = await app.callServerTool({
    name: dashboardTool,
    arguments: { session_id: sessionId },
  });
  const parsed = extractDashboardData(dashboardResult);
  if (parsed) {
    if (parsed.dashboard) merged.dashboard = parsed.dashboard;
    if (parsed.weekly_calendar) merged.weekly_calendar = parsed.weekly_calendar;
  }
  return merged;
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

async function discoverToolRegistry(app: RequestCapable): Promise<ToolRegistry> {
  const names = new Set<string>();
  let cursor: string | undefined;

  for (let page = 0; page < 5; page += 1) {
    const params: Record<string, unknown> = {};
    if (cursor) {
      params.cursor = cursor;
    }
    const result = await app.request({
      method: "tools/list",
      params,
    });
    const payload = extractObjectPayload(result);
    const tools = Array.isArray(payload?.tools) ? payload.tools : [];
    for (const tool of tools) {
      if (tool && typeof tool === "object" && typeof (tool as { name?: unknown }).name === "string") {
        names.add((tool as { name: string }).name);
      }
    }
    const nextCursor =
      typeof payload?.nextCursor === "string"
        ? payload.nextCursor
        : typeof payload?.next_cursor === "string"
          ? payload.next_cursor
          : undefined;
    if (!nextCursor) {
      break;
    }
    cursor = nextCursor;
  }

  const registry: ToolRegistry = {};
  for (const [operation, candidates] of Object.entries(TOOL_CANDIDATES) as Array<
    [ToolOperation, string[]]
  >) {
    registry[operation] = candidates.find((candidate) => names.has(candidate));
  }
  return registry;
}

function resolveTool(registry: ToolRegistry, operation: ToolOperation): string | undefined {
  return registry[operation] ?? TOOL_CANDIDATES[operation]?.[0];
}

function computeToolCapabilities(registry: ToolRegistry): UiToolCapabilities {
  const has = (operation: ToolOperation) => !!resolveTool(registry, operation);
  return {
    can_create_event: has("createEvent"),
    can_edit_event: has("updateEvent"),
    can_delete_event: has("cancelMeeting") || has("deleteEvent"),
    can_rsvp: has("respondToEvent"),
    can_reschedule_event: has("rescheduleMeeting") || has("updateEvent"),
    can_toggle_weekend: has("patchState"),
    can_select_calendars: has("patchState") && has("listCalendars"),
    can_mark_email_read: has("markEmailRead"),
    can_mark_email_unread: has("markEmailUnread"),
    can_archive_email: has("moveEmail"),
    can_trash_email: has("deleteEmail"),
    can_untrash_email: has("untrashEmail"),
    can_mark_email_spam: has("markEmailSpam"),
    can_mark_email_not_spam: has("markEmailNotSpam"),
  };
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



