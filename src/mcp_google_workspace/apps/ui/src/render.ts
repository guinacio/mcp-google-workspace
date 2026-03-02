import type {
  DashboardData,
  WeeklyCalendar,
  DashboardViewModel,
  WeeklyCalendarDay,
  WeeklyCalendarEvent,
  EventDetail,
  EmailDetail,
  EventEditorDraft,
  UiToolCapabilities,
  CalendarCatalogItem,
} from "./types";

function esc(text: string): string {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

function fmtDayDate(value: string): string {
  try {
    const d = new Date(`${value}T00:00:00`);
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
  } catch {
    return value;
  }
}

function fmtTopDate(): string {
  return new Date().toLocaleDateString([], {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
}

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

function relDate(value: string | null | undefined): string {
  if (!value) return "";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  const diffMs = Date.now() - dt.getTime();
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 60) return `${Math.max(minutes, 1)}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  return dt.toLocaleDateString([], { month: "short", day: "numeric" });
}

function initials(fromValue: string): string {
  const plain = fromValue.replace(/<.*?>/g, "").trim();
  const parts = plain.split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 1).toUpperCase();
  return `${parts[0][0] ?? ""}${parts[1][0] ?? ""}`.toUpperCase();
}

function colorVar(event: WeeklyCalendarEvent): string {
  const palette = [
    "--event-blueberry",
    "--event-tomato",
    "--event-sage",
    "--event-peacock",
    "--event-tangerine",
    "--event-grape",
    "--event-lavender",
    "--event-basil",
    "--event-flamingo",
    "--event-graphite",
  ];
  const raw = event.color_id ?? "0";
  const idx = Number.parseInt(raw, 10);
  if (Number.isFinite(idx) && idx >= 1) {
    return palette[(idx - 1) % palette.length];
  }
  return palette[Math.abs(hash(event.title)) % palette.length];
}

function hash(text: string): number {
  let h = 0;
  for (let i = 0; i < text.length; i += 1) {
    h = (h << 5) - h + text.charCodeAt(i);
    h |= 0;
  }
  return h;
}

export type UiAction =
  | { type: "chat"; text: string }
  | { type: "week_nav"; direction: "prev" | "today" | "next" }
  | { type: "open_event_editor"; mode: "create" | "edit"; seed_date?: string }
  | { type: "close_event_editor" }
  | { type: "save_event_editor"; draft: EventEditorDraft }
  | { type: "toggle_weekend"; include_weekend: boolean }
  | { type: "set_selected_calendars"; selected_calendar_ids: string[] }
  | { type: "select_event"; calendarId: string; eventId: string }
  | { type: "close_event_detail" }
  | { type: "select_email"; messageId: string }
  | { type: "close_email_detail" }
  | {
      type: "email_mark_read";
      messageId: string;
    }
  | {
      type: "email_mark_unread";
      messageId: string;
    }
  | {
      type: "email_archive";
      messageId: string;
    }
  | {
      type: "email_trash";
      messageId: string;
    }
  | {
      type: "email_untrash";
      messageId: string;
    }
  | {
      type: "email_mark_spam";
      messageId: string;
    }
  | {
      type: "email_mark_not_spam";
      messageId: string;
    }
  | {
      type: "email_download_attachment";
      messageId: string;
      attachmentId: string;
      filename: string;
      mimeType?: string;
    }
  | {
      type: "open_attachment";
      url: string;
    }
  | {
      type: "download_attachment";
      url: string;
      name: string;
      mimeType?: string;
    }
  | {
      type: "calendar_rsvp";
      calendarId: string;
      eventId: string;
      responseStatus: "accepted" | "tentative" | "declined";
    }
  | {
      type: "calendar_reschedule";
      calendarId: string;
      eventId: string;
      start: string;
      end: string;
      timezone: string;
      shiftMinutes: number;
    }
  | {
      type: "calendar_cancel";
      calendarId: string;
      eventId: string;
    };

type ActionHandler = (action: UiAction) => void;
let _onAction: ActionHandler = () => {};
const EVENT_TOOLTIP_ID = "calendar-event-hover-portal";

export interface RenderOptions {
  include_weekend?: boolean;
  selected_calendar_ids?: string[];
  calendar_catalog?: CalendarCatalogItem[];
  tool_capabilities?: UiToolCapabilities;
}

export function setActionHandler(handler: ActionHandler) {
  _onAction = handler;
}

function ensureEventTooltipLayer(): HTMLDivElement {
  let layer = document.getElementById(EVENT_TOOLTIP_ID) as HTMLDivElement | null;
  if (!layer) {
    layer = document.createElement("div");
    layer.id = EVENT_TOOLTIP_ID;
    layer.className = "event-tooltip-layer";
    layer.setAttribute("aria-hidden", "true");
    document.body.appendChild(layer);
  }
  return layer;
}

function hideEventTooltipLayer() {
  const layer = document.getElementById(EVENT_TOOLTIP_ID) as HTMLDivElement | null;
  if (layer) {
    layer.style.display = "none";
  }
}

function showEventTooltipLayer(anchor: HTMLElement, html: string) {
  if (!html.trim()) {
    hideEventTooltipLayer();
    return;
  }
  const layer = ensureEventTooltipLayer();
  layer.innerHTML = html;
  layer.style.display = "block";
  layer.style.visibility = "hidden";
  layer.style.left = "0px";
  layer.style.top = "0px";

  const margin = 8;
  const anchorRect = anchor.getBoundingClientRect();
  const tooltipRect = layer.getBoundingClientRect();
  let left = anchorRect.left + (anchorRect.width - tooltipRect.width) / 2;
  left = Math.max(margin, Math.min(left, window.innerWidth - tooltipRect.width - margin));
  let top = anchorRect.top - tooltipRect.height - margin;
  if (top < margin) {
    top = Math.min(window.innerHeight - tooltipRect.height - margin, anchorRect.bottom + margin);
  }
  layer.style.left = `${left}px`;
  layer.style.top = `${top}px`;
  layer.style.visibility = "visible";
}

export const RENDER_CSS = `
.dashboard {
  max-width: 1360px;
  margin: 0 auto;
  padding: 24px 20px 40px;
  display: grid;
  gap: 18px;
}

/* ── Top bar ─────────────────────────────────────────────────────────── */
.top-bar {
  background: var(--md-sys-color-surface-container);
  border: 1px solid var(--md-sys-color-outline-variant);
  border-radius: var(--radius-md);
  box-shadow: var(--md-sys-elevation-1);
  padding: 14px 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.top-bar h1 {
  margin: 0;
  font-size: 1.15rem;
  font-weight: 500;
  letter-spacing: -0.01em;
}

.top-sub {
  color: var(--md-sys-color-on-surface-variant);
  font-size: 0.82rem;
  margin-top: 2px;
}

.quick-stats {
  display: inline-flex;
  gap: 8px;
  flex-wrap: wrap;
}

.stat-chip {
  border-radius: 999px;
  border: 1px solid var(--md-sys-color-outline-variant);
  background: var(--md-sys-color-surface-variant);
  color: var(--md-sys-color-on-surface-variant);
  padding: 5px 10px;
  font-size: 0.76rem;
  font-weight: 500;
}

/* ── Two-column layout ───────────────────────────────────────────────── */
.main-grid {
  display: grid;
  grid-template-columns: minmax(0, 65fr) minmax(320px, 35fr);
  gap: 16px;
}

.main-grid-full {
  grid-template-columns: 1fr;
}

.surface {
  background: var(--md-sys-color-surface-container);
  border: 1px solid var(--md-sys-color-outline-variant);
  border-radius: var(--radius-md);
  box-shadow: var(--md-sys-elevation-1);
}

.calendar-shell {
  padding: 14px;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  gap: 10px;
}

.section-title {
  font-size: 0.82rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--md-sys-color-on-surface-variant);
}

.section-subtitle {
  font-size: 0.78rem;
  color: var(--md-sys-color-outline);
}

/* ── Navigation / chip buttons ───────────────────────────────────────── */
.week-nav {
  display: inline-flex;
  gap: 6px;
}

.calendar-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.calendar-select {
  position: relative;
}

.calendar-select summary {
  list-style: none;
}

.calendar-select summary::-webkit-details-marker {
  display: none;
}

.calendar-select-list {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  min-width: 260px;
  max-height: 260px;
  overflow: auto;
  border: 1px solid var(--md-sys-color-outline-variant);
  border-radius: var(--radius-sm);
  background: var(--md-sys-color-surface-container-high);
  box-shadow: var(--md-sys-elevation-2);
  padding: 8px;
  z-index: 30;
  display: grid;
  gap: 6px;
}

.calendar-option {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  font-size: 0.76rem;
  color: var(--md-sys-color-on-surface);
}

.calendar-option small {
  color: var(--md-sys-color-outline);
  font-size: 0.68rem;
}

.nav-btn,
.chip-btn,
.action-btn {
  border: 1px solid var(--md-sys-color-outline-variant);
  background: transparent;
  color: var(--md-sys-color-on-surface-variant);
  border-radius: 999px;
  padding: 5px 10px;
  font-size: 0.76rem;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.nav-btn:hover,
.chip-btn:hover,
.action-btn:hover {
  background: color-mix(in srgb, var(--md-sys-color-primary) 12%, transparent);
  border-color: var(--md-sys-color-primary);
  color: var(--md-sys-color-primary);
}

/* ── Weekly calendar grid ────────────────────────────────────────────── */
.week-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 8px;
  position: relative;
  isolation: isolate;
}

.day-col {
  background: var(--md-sys-color-surface-container-high);
  border: 1px solid var(--md-sys-color-outline-variant);
  border-radius: var(--radius-sm);
  padding: 8px;
  min-height: 540px;
  overflow: visible;
  position: relative;
  z-index: 1;
}

.day-col:hover {
  z-index: 15;
}

.day-col.today {
  border-color: var(--md-sys-color-primary);
  background: color-mix(in srgb, var(--md-sys-color-primary) 5%, var(--md-sys-color-surface-container-high));
}

.day-head {
  font-size: 0.76rem;
  color: var(--md-sys-color-on-surface-variant);
  margin-bottom: 8px;
  font-weight: 600;
  display: flex;
  justify-content: space-between;
}

.day-col.today .day-head {
  color: var(--md-sys-color-primary);
}

.day-all-day {
  display: grid;
  gap: 4px;
  margin-bottom: 8px;
}

.all-day-chip {
  font-size: 0.72rem;
  border-radius: var(--radius-xs);
  padding: 3px 7px;
  background: color-mix(in srgb, var(--md-sys-color-primary) 15%, transparent);
  color: var(--md-sys-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.3;
}

.day-events {
  display: grid;
  gap: 6px;
  overflow: visible;
}

/* ── Event card ──────────────────────────────────────────────────────── */
.calendar-event {
  border-radius: var(--radius-xs);
  border-left: 3px solid var(--event-color);
  background: color-mix(in srgb, var(--event-color) 10%, var(--md-sys-color-surface-container));
  padding: 7px 8px;
  cursor: pointer;
  position: relative;
  z-index: 1;
  transition: background 0.15s;
}

.calendar-event:hover {
  background: color-mix(in srgb, var(--event-color) 20%, var(--md-sys-color-surface-container));
  z-index: 120;
}

.event-time {
  font-size: 0.7rem;
  color: var(--md-sys-color-outline);
  font-weight: 600;
  letter-spacing: 0.01em;
}

.event-title {
  font-size: 0.78rem;
  font-weight: 500;
  margin-top: 1px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.event-meta {
  margin-top: 2px;
  color: var(--md-sys-color-outline);
  font-size: 0.7rem;
}

/* ── Event hover tooltip ─────────────────────────────────────────────── */
.event-hover {
  display: none !important;
}

.event-tooltip-layer {
  position: fixed;
  z-index: 2147483000;
  border-radius: var(--radius-sm);
  border: 1px solid var(--md-sys-color-outline-variant);
  background: var(--md-sys-color-surface-container-high);
  box-shadow: var(--md-sys-elevation-2);
  padding: 8px 10px;
  font-size: 0.74rem;
  line-height: 1.45;
  color: var(--md-sys-color-on-surface);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  max-width: 320px;
  overflow-y: auto;
  pointer-events: none;
}

/* ── Event inline actions (shown on hover) ───────────────────────────── */
.event-actions {
  margin-top: 4px;
  display: none;
  flex-wrap: wrap;
  gap: 3px;
}

.calendar-event:hover .event-actions {
  display: flex;
}

.rsvp-chip {
  border: 1px solid var(--md-sys-color-outline-variant);
  border-radius: 999px;
  background: transparent;
  font-size: 0.68rem;
  padding: 2px 7px;
  cursor: pointer;
  color: var(--md-sys-color-on-surface-variant);
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.rsvp-chip:hover {
  border-color: var(--md-sys-color-primary);
  color: var(--md-sys-color-primary);
}

.rsvp-chip.active {
  background: var(--md-sys-color-primary);
  border-color: var(--md-sys-color-primary);
  color: var(--md-sys-color-on-primary);
}

/* ── Sidebar ─────────────────────────────────────────────────────────── */
.sidebar {
  display: grid;
  gap: 12px;
  align-content: start;
}

.inbox-shell {
  padding: 12px;
}

/* ── Inbox ────────────────────────────────────────────────────────────── */
.inbox-list {
  display: grid;
  gap: 1px;
  max-height: 460px;
  overflow: auto;
}

.inbox-row {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  padding: 7px 6px;
  border-radius: var(--radius-xs);
  cursor: pointer;
  transition: background 0.12s;
}

.inbox-row:hover {
  background: var(--md-sys-color-surface-variant);
}

.inbox-row.unread {
  background: color-mix(in srgb, var(--md-sys-color-primary) 10%, transparent);
}

.avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--md-sys-color-primary) 18%, transparent);
  color: var(--md-sys-color-primary);
  display: grid;
  place-items: center;
  font-size: 0.72rem;
  font-weight: 700;
}

.mail-content {
  min-width: 0;
}

.mail-subject {
  font-size: 0.78rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--md-sys-color-on-surface);
}

.mail-subject.unread {
  font-weight: 700;
  color: var(--md-sys-color-primary);
}

.mail-subline {
  font-size: 0.7rem;
  color: var(--md-sys-color-outline);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.mail-date {
  font-size: 0.68rem;
  color: var(--md-sys-color-outline);
  padding-left: 6px;
  white-space: nowrap;
}

/* ── Overlay panels (event detail / email detail) ────────────────────── */
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: grid;
  place-items: center;
  padding: 20px;
  z-index: 50;
}

.panel {
  width: min(780px, 94vw);
  max-height: 88vh;
  overflow: auto;
  border-radius: var(--radius-lg);
  border: 1px solid var(--md-sys-color-outline-variant);
  background: var(--md-sys-color-surface-container);
  box-shadow: var(--md-sys-elevation-3);
  padding: 18px;
}

.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.panel-title {
  font-size: 1.05rem;
  font-weight: 500;
  letter-spacing: -0.01em;
}

.panel-sub {
  font-size: 0.8rem;
  color: var(--md-sys-color-on-surface-variant);
  margin-top: 2px;
}

.panel-body {
  margin-top: 14px;
  display: grid;
  gap: 10px;
}

.event-editor-form {
  display: grid;
  gap: 10px;
}

.editor-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.editor-field {
  display: grid;
  gap: 4px;
  font-size: 0.74rem;
  color: var(--md-sys-color-on-surface-variant);
}

.editor-field input,
.editor-field textarea,
.editor-field select {
  border: 1px solid var(--md-sys-color-outline-variant);
  border-radius: var(--radius-xs);
  background: var(--md-sys-color-surface-container-high);
  color: var(--md-sys-color-on-surface);
  padding: 7px 8px;
}

.editor-field textarea {
  min-height: 76px;
  resize: vertical;
}

.editor-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.inline-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 0.76rem;
  color: var(--md-sys-color-on-surface-variant);
}

.banner {
  border-radius: var(--radius-sm);
  border: 1px solid var(--md-sys-color-outline-variant);
  background: var(--md-sys-color-surface-container);
  color: var(--md-sys-color-on-surface-variant);
  padding: 8px 10px;
  font-size: 0.76rem;
}

.banner.error {
  border-color: color-mix(in srgb, var(--md-sys-color-error) 70%, transparent);
  color: var(--md-sys-color-error);
}

.detail-block {
  background: var(--md-sys-color-surface-container-high);
  border: 1px solid var(--md-sys-color-outline-variant);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  font-size: 0.8rem;
  line-height: 1.5;
}

.detail-block a {
  color: var(--md-sys-color-primary);
  text-decoration: none;
}

.detail-block a:hover {
  text-decoration: underline;
}

.attendee-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 4px;
}

.attachment-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 6px;
}

.attachment-link {
  color: var(--md-sys-color-primary);
  text-decoration: none;
}

.attachment-link:hover {
  text-decoration: underline;
}

.email-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.email-chip {
  border: 1px solid var(--md-sys-color-outline-variant);
  border-radius: 999px;
  background: transparent;
  font-size: 0.72rem;
  padding: 5px 10px;
  cursor: pointer;
  color: var(--md-sys-color-on-surface-variant);
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.email-chip:hover {
  border-color: var(--md-sys-color-primary);
  color: var(--md-sys-color-primary);
  background: color-mix(in srgb, var(--md-sys-color-primary) 12%, transparent);
}

.email-chip.active {
  border-color: var(--md-sys-color-primary);
  color: var(--md-sys-color-on-primary);
  background: var(--md-sys-color-primary);
}

.email-statuses {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.status-chip {
  border-radius: 999px;
  border: 1px solid var(--md-sys-color-outline-variant);
  padding: 2px 8px;
  font-size: 0.68rem;
  color: var(--md-sys-color-on-surface-variant);
}

/* ── Loading state ───────────────────────────────────────────────────── */
.loading-state {
  min-height: 280px;
  display: grid;
  place-items: center;
  color: var(--md-sys-color-on-surface-variant);
  font-size: 0.85rem;
}

/* ── Responsive ──────────────────────────────────────────────────────── */
@media (max-width: 1120px) {
  .main-grid {
    grid-template-columns: 1fr;
  }

  .day-col {
    min-height: 320px;
  }
}

@media (max-width: 760px) {
  .week-grid {
    grid-template-columns: repeat(7, minmax(170px, 1fr));
    overflow-x: auto;
    padding-bottom: 6px;
  }

  .editor-row {
    grid-template-columns: 1fr;
  }
}
`;

export function renderLoading(root: HTMLElement) {
  hideEventTooltipLayer();
  root.innerHTML = `<div class="loading-state">Loading workspace dashboard...</div>`;
}

type InboxMessage = {
  id?: string;
  subject?: string;
  from?: string;
  date?: string;
  snippet?: string;
  label_ids?: string[];
  is_unread?: boolean;
};

function getInboxData(dashboard?: DashboardViewModel): { unreadCount: number; messages: InboxMessage[] } {
  if (!dashboard) return { unreadCount: 0, messages: [] };
  const section = dashboard.sections.find((item) => item.id === "communications");
  const inbox = section?.cards.find((card) => card.card_type === "inbox");
  const data = (inbox?.data ?? {}) as {
    unread_count?: number;
    unreadCount?: number;
    unread_message_ids?: unknown[];
    unreadMessageIds?: unknown[];
    messages?: Array<Record<string, unknown>>;
  };
  const unreadIdsRaw = Array.isArray(data.unread_message_ids)
    ? data.unread_message_ids
    : Array.isArray(data.unreadMessageIds)
      ? data.unreadMessageIds
      : [];
  const unreadIdSet = new Set(
    unreadIdsRaw.filter((item): item is string => typeof item === "string" && item.length > 0)
  );
  const normalizedMessages: InboxMessage[] = Array.isArray(data.messages)
    ? data.messages.map((msg) => {
        const labelIdsRaw = Array.isArray(msg.label_ids)
          ? msg.label_ids
          : Array.isArray(msg.labelIds)
            ? msg.labelIds
            : [];
        const labelIds = labelIdsRaw.filter((item): item is string => typeof item === "string");
        const isUnreadRaw =
          typeof msg.is_unread === "boolean"
            ? msg.is_unread
            : typeof msg.isUnread === "boolean"
              ? msg.isUnread
              : undefined;
        const messageId = typeof msg.id === "string" ? msg.id : undefined;
        const isUnread =
          (isUnreadRaw ?? false) ||
          labelIds.includes("UNREAD") ||
          (!!messageId && unreadIdSet.has(messageId));
        return {
          id: messageId,
          subject: typeof msg.subject === "string" ? msg.subject : undefined,
          from: typeof msg.from === "string" ? msg.from : undefined,
          date: typeof msg.date === "string" ? msg.date : undefined,
          snippet: typeof msg.snippet === "string" ? msg.snippet : undefined,
          label_ids: labelIds,
          is_unread: isUnread,
        };
      })
    : [];
  const unreadCount =
    typeof data.unread_count === "number"
      ? data.unread_count
      : typeof data.unreadCount === "number"
        ? data.unreadCount
        : normalizedMessages.filter((msg) => !!msg.is_unread).length;
  return {
    unreadCount,
    messages: normalizedMessages,
  };
}

function countWeekEvents(weekly?: WeeklyCalendar): number {
  if (!weekly) return 0;
  return weekly.days.reduce((acc, day) => acc + day.all_day_events.length + day.timed_events.length, 0);
}

export function renderDashboard(root: HTMLElement, data: DashboardData, options: RenderOptions = {}) {
  const hasDashboard = !!data.dashboard;
  const inboxData = getInboxData(data.dashboard);
  const eventsCount = countWeekEvents(data.weekly_calendar);
  const selectedCalendarIds = options.selected_calendar_ids ?? [];
  const includeWeekend = options.include_weekend ?? true;

  hideEventTooltipLayer();

  root.innerHTML = `
    <div class="dashboard">
      ${data.ui_error ? `<div class="banner error">${esc(data.ui_error)}</div>` : ""}
      ${data.ui_notice ? `<div class="banner">${esc(data.ui_notice)}</div>` : ""}
      ${renderTopBar(eventsCount, hasDashboard ? inboxData.unreadCount : undefined)}
      <div class="main-grid${hasDashboard ? "" : " main-grid-full"}">
        ${renderCalendarArea(
          data.weekly_calendar,
          {
            include_weekend: includeWeekend,
            selected_calendar_ids: selectedCalendarIds,
            calendar_catalog: options.calendar_catalog ?? [],
            tool_capabilities: options.tool_capabilities,
          }
        )}
        ${hasDashboard ? `<div class="sidebar">${renderInboxPanel(inboxData.messages, inboxData.unreadCount)}</div>` : ""}
      </div>
      ${renderEventDetailPanel(data.event_detail, options.tool_capabilities)}
      ${renderEventEditorPanel(
        data.event_editor,
        options.calendar_catalog ?? [],
        data.weekly_calendar?.timezone ?? "UTC"
      )}
      ${renderEmailDetailPanel(data.email_detail, options.tool_capabilities)}
    </div>
  `;

  root.onmouseover = (event) => {
    const target = event.target as HTMLElement;
    const eventCard = target.closest<HTMLElement>("[data-open-event]");
    if (!eventCard) return;
    const source = eventCard.querySelector<HTMLElement>(".event-hover");
    if (!source) return;
    showEventTooltipLayer(eventCard, source.innerHTML);
  };

  root.onmousemove = (event) => {
    const target = event.target as HTMLElement;
    const eventCard = target.closest<HTMLElement>("[data-open-event]");
    if (!eventCard) return;
    const source = eventCard.querySelector<HTMLElement>(".event-hover");
    if (!source) return;
    showEventTooltipLayer(eventCard, source.innerHTML);
  };

  root.onmouseout = (event) => {
    const target = event.target as HTMLElement;
    const eventCard = target.closest<HTMLElement>("[data-open-event]");
    if (!eventCard) return;
    const related = event.relatedTarget as HTMLElement | null;
    if (!related || !eventCard.contains(related)) {
      hideEventTooltipLayer();
    }
  };

  root.onmouseleave = () => {
    hideEventTooltipLayer();
  };

  root.onclick = (event) => {
    hideEventTooltipLayer();
    const target = event.target as HTMLElement;

    const chat = target.closest<HTMLElement>("[data-action-msg]");
    if (chat) {
      event.preventDefault();
      _onAction({ type: "chat", text: chat.dataset.actionMsg || "" });
      return;
    }

    const weekNav = target.closest<HTMLElement>("[data-week-nav]");
    if (weekNav) {
      event.preventDefault();
      const direction = weekNav.dataset.weekNav as "prev" | "today" | "next" | undefined;
      if (direction) _onAction({ type: "week_nav", direction });
      return;
    }

    const openEditor = target.closest<HTMLElement>("[data-open-event-editor]");
    if (openEditor) {
      event.preventDefault();
      const mode = openEditor.dataset.openEventEditor as "create" | "edit" | undefined;
      const seedDate = openEditor.dataset.seedDate;
      if (mode) {
        _onAction({ type: "open_event_editor", mode, seed_date: seedDate });
      }
      return;
    }

    if (target.closest("[data-close-event-editor]")) {
      event.preventDefault();
      _onAction({ type: "close_event_editor" });
      return;
    }

    const openEvent = target.closest<HTMLElement>("[data-open-event]");
    if (openEvent) {
      const calendarId = openEvent.dataset.calendarId;
      const eventId = openEvent.dataset.eventId;
      if (calendarId && eventId) {
        _onAction({ type: "select_event", calendarId, eventId });
      }
      return;
    }

    const openEmail = target.closest<HTMLElement>("[data-open-email]");
    if (openEmail) {
      const messageId = openEmail.dataset.messageId;
      if (messageId) {
        _onAction({ type: "select_email", messageId });
      }
      return;
    }

    const openAttachment = target.closest<HTMLElement>("[data-open-attachment-url]");
    if (openAttachment) {
      event.preventDefault();
      const url = openAttachment.dataset.openAttachmentUrl;
      if (url) {
        _onAction({ type: "open_attachment", url });
      }
      return;
    }

    const downloadAttachment = target.closest<HTMLElement>("[data-download-attachment-url]");
    if (downloadAttachment) {
      event.preventDefault();
      const url = downloadAttachment.dataset.downloadAttachmentUrl;
      const name = downloadAttachment.dataset.downloadAttachmentName || "attachment";
      const mimeType = downloadAttachment.dataset.downloadAttachmentMime || undefined;
      if (url) {
        _onAction({ type: "download_attachment", url, name, mimeType });
      }
      return;
    }

    const downloadEmailAttachment = target.closest<HTMLElement>("[data-email-attachment-download]");
    if (downloadEmailAttachment) {
      event.preventDefault();
      const messageId = downloadEmailAttachment.dataset.messageId;
      const attachmentId = downloadEmailAttachment.dataset.attachmentId;
      const filename = downloadEmailAttachment.dataset.filename || "attachment";
      const mimeType = downloadEmailAttachment.dataset.mimeType || undefined;
      if (messageId && attachmentId) {
        _onAction({
          type: "email_download_attachment",
          messageId,
          attachmentId,
          filename,
          mimeType,
        });
      }
      return;
    }

    if (target.closest("[data-close-event]")) {
      _onAction({ type: "close_event_detail" });
      return;
    }

    if (target.closest("[data-close-email]")) {
      _onAction({ type: "close_email_detail" });
      return;
    }

    const emailAction = target.closest<HTMLElement>("[data-email-action]");
    if (emailAction) {
      event.preventDefault();
      const messageId = emailAction.dataset.messageId;
      const action = emailAction.dataset.emailAction;
      if (!messageId || !action) {
        return;
      }
      if (action === "mark_read") _onAction({ type: "email_mark_read", messageId });
      if (action === "mark_unread") _onAction({ type: "email_mark_unread", messageId });
      if (action === "archive") _onAction({ type: "email_archive", messageId });
      if (action === "trash") _onAction({ type: "email_trash", messageId });
      if (action === "untrash") _onAction({ type: "email_untrash", messageId });
      if (action === "spam") _onAction({ type: "email_mark_spam", messageId });
      if (action === "not_spam") _onAction({ type: "email_mark_not_spam", messageId });
      return;
    }

    const rsvp = target.closest<HTMLElement>("[data-rsvp-status]");
    if (rsvp) {
      event.preventDefault();
      event.stopPropagation();
      const calendarId = rsvp.dataset.calendarId;
      const eventId = rsvp.dataset.eventId;
      const responseStatus = rsvp.dataset.rsvpStatus as "accepted" | "tentative" | "declined" | undefined;
      if (calendarId && eventId && responseStatus) {
        _onAction({ type: "calendar_rsvp", calendarId, eventId, responseStatus });
      }
      return;
    }

    const reschedule = target.closest<HTMLElement>("[data-reschedule-minutes]");
    if (reschedule) {
      event.preventDefault();
      event.stopPropagation();
      const calendarId = reschedule.dataset.calendarId;
      const eventId = reschedule.dataset.eventId;
      const start = reschedule.dataset.eventStart;
      const end = reschedule.dataset.eventEnd;
      const timezone = reschedule.dataset.eventTimezone;
      const shiftRaw = reschedule.dataset.rescheduleMinutes;
      const shiftMinutes = shiftRaw ? Number(shiftRaw) : Number.NaN;
      if (calendarId && eventId && start && end && timezone && Number.isFinite(shiftMinutes)) {
        _onAction({
          type: "calendar_reschedule",
          calendarId,
          eventId,
          start,
          end,
          timezone,
          shiftMinutes,
        });
      }
      return;
    }

    const cancel = target.closest<HTMLElement>("[data-cancel-event]");
    if (cancel) {
      event.preventDefault();
      event.stopPropagation();
      const calendarId = cancel.dataset.calendarId;
      const eventId = cancel.dataset.eventId;
      if (calendarId && eventId) {
        _onAction({ type: "calendar_cancel", calendarId, eventId });
      }
    }
  };

  root.onchange = (event) => {
    const target = event.target as HTMLElement;

    const weekendToggle = target.closest<HTMLInputElement>("[data-toggle-weekend]");
    if (weekendToggle) {
      _onAction({ type: "toggle_weekend", include_weekend: weekendToggle.checked });
      return;
    }

    const calendarToggle = target.closest<HTMLInputElement>("[data-calendar-id]");
    if (calendarToggle) {
      const selected = Array.from(
        root.querySelectorAll<HTMLInputElement>("[data-calendar-id]:checked")
      ).map((node) => node.dataset.calendarId || "").filter(Boolean);
      _onAction({ type: "set_selected_calendars", selected_calendar_ids: selected });
    }
  };

  root.onsubmit = (event) => {
    const form = event.target as HTMLFormElement;
    if (!form.matches("[data-event-editor-form]")) {
      return;
    }
    event.preventDefault();
    const formData = new FormData(form);
    const modeRaw = formData.get("mode");
    const mode = modeRaw === "edit" ? "edit" : "create";
    const calendarId = String(formData.get("calendar_id") || "");
    const summary = String(formData.get("summary") || "");
    const startLocal = String(formData.get("start_local") || "");
    const endLocal = String(formData.get("end_local") || "");
    if (!calendarId || !summary || !startLocal || !endLocal) {
      return;
    }
    const draft: EventEditorDraft = {
      mode,
      calendar_id: calendarId,
      event_id: String(formData.get("event_id") || "") || undefined,
      summary,
      start_local: startLocal,
      end_local: endLocal,
      timezone: String(formData.get("timezone") || "UTC"),
      location: String(formData.get("location") || ""),
      description: String(formData.get("description") || ""),
      attendees_csv: String(formData.get("attendees_csv") || ""),
      create_conference: formData.get("create_conference") === "on",
    };
    _onAction({ type: "save_event_editor", draft });
  };
}

function renderTopBar(eventsCount: number, unreadCount?: number): string {
  const unreadChip = unreadCount !== undefined ? `<span class="stat-chip">${unreadCount} unread</span>` : "";
  return `
    <div class="top-bar surface">
      <div>
        <h1>${esc(getGreeting())}</h1>
        <div class="top-sub">${esc(fmtTopDate())}</div>
      </div>
      <div class="quick-stats">
        <span class="stat-chip">${eventsCount} events</span>
        ${unreadChip}
      </div>
    </div>
  `;
}

function renderCalendarArea(
  weekly: WeeklyCalendar | undefined,
  options: {
    include_weekend: boolean;
    selected_calendar_ids: string[];
    calendar_catalog: CalendarCatalogItem[];
    tool_capabilities?: UiToolCapabilities;
  }
): string {
  if (!weekly) {
    return `<section class="calendar-shell surface"><div class="section-subtitle">Calendar data unavailable.</div></section>`;
  }
  const weekRange = `${weekly.week_start} - ${weekly.week_end}`;
  const canCreate = options.tool_capabilities?.can_create_event ?? false;
  const canToggleWeekend = options.tool_capabilities?.can_toggle_weekend ?? false;
  const canSelectCalendars = options.tool_capabilities?.can_select_calendars ?? false;
  return `
    <section class="calendar-shell surface">
      <div class="section-head">
        <div>
          <div class="section-title">Week View</div>
          <div class="section-subtitle">${esc(weekRange)} · ${esc(weekly.timezone)}</div>
        </div>
        <div class="calendar-actions">
          <div class="week-nav">
            <button type="button" class="nav-btn" data-week-nav="prev">Prev</button>
            <button type="button" class="nav-btn" data-week-nav="today">Today</button>
            <button type="button" class="nav-btn" data-week-nav="next">Next</button>
          </div>
          ${canToggleWeekend ? `
            <label class="inline-toggle">
              <input type="checkbox" data-toggle-weekend="1" ${options.include_weekend ? "checked" : ""} />
              Show weekend
            </label>
          ` : ""}
          ${canSelectCalendars ? renderCalendarSelector(options.calendar_catalog, options.selected_calendar_ids) : ""}
          ${canCreate ? `<button type="button" class="action-btn" data-open-event-editor="create">New event</button>` : ""}
        </div>
      </div>
      <div class="week-grid">${weekly.days.map((day) => renderDay(day, weekly.timezone, canCreate, options.tool_capabilities)).join("")}</div>
    </section>
  `;
}

function renderCalendarSelector(catalog: CalendarCatalogItem[], selectedIds: string[]): string {
  if (!catalog.length) {
    return "";
  }
  const options = catalog
    .map((item) => {
      const isChecked = selectedIds.includes(item.id);
      const role = item.access_role ? `<small>${esc(item.access_role)}</small>` : "";
      return `
        <label class="calendar-option">
          <input type="checkbox" data-calendar-id="${esc(item.id)}" ${isChecked ? "checked" : ""} />
          <span>${esc(item.summary)} ${item.primary ? "<small>(primary)</small>" : role}</span>
        </label>
      `;
    })
    .join("");
  return `
    <details class="calendar-select">
      <summary class="chip-btn">Calendars</summary>
      <div class="calendar-select-list">${options}</div>
    </details>
  `;
}

function renderDay(
  day: WeeklyCalendarDay,
  timezone: string,
  canCreate: boolean,
  capabilities?: UiToolCapabilities
): string {
  const timed = day.timed_events.map((event) => renderEvent(event, timezone, capabilities)).join("");
  const allDay = day.all_day_events
    .map((event) => `<div class="all-day-chip" title="${esc(event.title)}">${esc(event.title)}</div>`)
    .join("");
  const empty = !timed && !allDay ? `<div class="section-subtitle">No events</div>` : "";
  return `
    <div class="day-col ${day.is_today ? "today" : ""}">
      <div class="day-head">
        <span>${esc(day.day_label)}</span>
        <span>${esc(fmtDayDate(day.date))}</span>
        ${canCreate ? `<button type="button" class="chip-btn" data-open-event-editor="create" data-seed-date="${esc(day.date)}">Add</button>` : ""}
      </div>
      ${allDay ? `<div class="day-all-day">${allDay}</div>` : ""}
      <div class="day-events">${timed || empty}</div>
    </div>
  `;
}

function renderEvent(event: WeeklyCalendarEvent, timezone: string, capabilities?: UiToolCapabilities): string {
  const eventColor = colorVar(event);
  const metaParts = [event.location || "", event.attendee_count ? `${event.attendee_count} attendees` : "", event.has_conference ? "Meet" : ""]
    .filter(Boolean)
    .join(" \u00b7 ");

  const hoverLines: string[] = [];
  hoverLines.push(`<strong>${esc(event.title)}</strong>`);
  hoverLines.push(`${esc(fmtTime(event.start))} \u2013 ${esc(fmtTime(event.end))}`);
  if (event.location) hoverLines.push(`\ud83d\udccd ${esc(event.location)}`);
  if (event.attendee_count) hoverLines.push(`\ud83d\udc65 ${event.attendee_count} attendee${event.attendee_count > 1 ? "s" : ""}`);
  if (event.has_conference) hoverLines.push(`\ud83c\udf10 Google Meet`);
  if (event.description_snippet) {
    hoverLines.push("");
    hoverLines.push(esc(event.description_snippet));
  }

  return `
    <article class="calendar-event" style="--event-color: var(${eventColor})" data-open-event="1" data-calendar-id="${esc(event.calendar_id || "")}" data-event-id="${esc(event.event_id || "")}">
      <div class="event-time">${esc(fmtTime(event.start))} - ${esc(fmtTime(event.end))}</div>
      <div class="event-title">${esc(event.title)}</div>
      ${metaParts ? `<div class="event-meta">${esc(metaParts)}</div>` : ""}
      <div class="event-actions">${renderEventActionChips(event, timezone, capabilities)}</div>
      <div class="event-hover">${hoverLines.join("<br>")}</div>
    </article>
  `;
}

function renderEventActionChips(
  event: WeeklyCalendarEvent,
  timezone: string,
  capabilities?: UiToolCapabilities
): string {
  if (!event.event_id || !event.calendar_id) return "";
  const current = event.attendee_response_status || "";
  const canRsvp = capabilities?.can_rsvp ?? false;
  const canReschedule = capabilities?.can_reschedule_event ?? false;
  const canDelete = capabilities?.can_delete_event ?? false;
  const chip = (status: "accepted" | "tentative" | "declined", label: string) => `
    <button
      type="button"
      class="rsvp-chip ${current === status ? "active" : ""}"
      data-rsvp-status="${status}"
      data-calendar-id="${esc(event.calendar_id || "")}"
      data-event-id="${esc(event.event_id || "")}">
      ${label}
    </button>
  `;
  return `
    ${canRsvp ? chip("accepted", "Yes") : ""}
    ${canRsvp ? chip("tentative", "Maybe") : ""}
    ${canRsvp ? chip("declined", "No") : ""}
    ${canReschedule ? `<button type="button" class="chip-btn" data-reschedule-minutes="15" data-calendar-id="${esc(event.calendar_id || "")}" data-event-id="${esc(event.event_id || "")}" data-event-start="${esc(event.start)}" data-event-end="${esc(event.end)}" data-event-timezone="${esc(timezone)}">+15m</button>` : ""}
    ${canReschedule ? `<button type="button" class="chip-btn" data-reschedule-minutes="30" data-calendar-id="${esc(event.calendar_id || "")}" data-event-id="${esc(event.event_id || "")}" data-event-start="${esc(event.start)}" data-event-end="${esc(event.end)}" data-event-timezone="${esc(timezone)}">+30m</button>` : ""}
    ${canReschedule ? `<button type="button" class="chip-btn" data-reschedule-minutes="60" data-calendar-id="${esc(event.calendar_id || "")}" data-event-id="${esc(event.event_id || "")}" data-event-start="${esc(event.start)}" data-event-end="${esc(event.end)}" data-event-timezone="${esc(timezone)}">+1h</button>` : ""}
    ${canDelete ? `<button type="button" class="chip-btn" data-cancel-event="1" data-calendar-id="${esc(event.calendar_id || "")}" data-event-id="${esc(event.event_id || "")}">Cancel</button>` : ""}
  `;
}

function renderInboxPanel(messages: InboxMessage[], unreadCount: number): string {
  const rows = messages
    .map((msg) => {
      const labels = Array.isArray(msg.label_ids) ? msg.label_ids : [];
      const isUnread = !!msg.is_unread || labels.includes("UNREAD");
      const unreadClass = isUnread ? "unread" : "";
      return `
        <div class="inbox-row ${unreadClass}" data-open-email="1" data-message-id="${esc(msg.id || "")}">
          <div class="avatar">${esc(initials(msg.from || ""))}</div>
          <div class="mail-content">
            <div class="mail-subject ${unreadClass}">${esc(msg.subject || "(No subject)")}</div>
            <div class="mail-subline">${esc((msg.from || "Unknown sender").replace(/<.*?>/g, "").trim())}${msg.snippet ? ` · ${esc(msg.snippet)}` : ""}</div>
          </div>
          <div class="mail-date">${esc(relDate(msg.date))}</div>
        </div>
      `;
    })
    .join("");

  return `
    <section class="inbox-shell surface">
      <div class="section-head">
        <div>
          <div class="section-title">Inbox</div>
          <div class="section-subtitle">${unreadCount} unread</div>
        </div>
      </div>
      <div class="inbox-list">${rows || `<div class="section-subtitle">No messages</div>`}</div>
    </section>
  `;
}

function renderEventDetailPanel(detail: EventDetail | undefined, capabilities?: UiToolCapabilities): string {
  if (!detail) return "";
  const canEdit = capabilities?.can_edit_event ?? false;
  const canRsvp = capabilities?.can_rsvp ?? false;
  const canReschedule = capabilities?.can_reschedule_event ?? false;
  const canDelete = capabilities?.can_delete_event ?? false;
  const attendees = detail.attendees
    .map(
      (attendee) =>
        `<li>${esc(attendee.display_name || attendee.email)}${attendee.response_status ? ` · ${esc(attendee.response_status)}` : ""}</li>`
    )
    .join("");
  const attachments = (detail.attachments || [])
    .map((attachment) => {
      const label = attachment.mime_type ? `${attachment.title} (${attachment.mime_type})` : attachment.title;
      if (attachment.file_url) {
        return `
          <li>
            <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
              <span>${esc(label)}</span>
              <button type="button" class="chip-btn" data-open-attachment-url="${esc(attachment.file_url)}">Open</button>
              <button
                type="button"
                class="chip-btn"
                data-download-attachment-url="${esc(attachment.file_url)}"
                data-download-attachment-name="${esc(attachment.title)}"
                data-download-attachment-mime="${esc(attachment.mime_type || "")}">
                Download
              </button>
            </div>
          </li>
        `;
      }
      return `<li>${esc(label)}</li>`;
    })
    .join("");
  const currentResponse = detail.self_response_status || "";
  const description = detail.description?.trim() || "No description.";
  return `
    <div class="overlay" role="dialog" aria-modal="true">
      <section class="panel">
        <div class="panel-head">
          <div>
            <div class="panel-title">${esc(detail.title)}</div>
            <div class="panel-sub">${esc(fmtTime(detail.start))} - ${esc(fmtTime(detail.end))}${detail.timezone ? ` · ${esc(detail.timezone)}` : ""}</div>
          </div>
          <button type="button" class="nav-btn" data-close-event="1">Close</button>
        </div>
        <div class="panel-body">
          <div class="detail-block">${detail.location ? `<strong>Location:</strong> ${esc(detail.location)}` : "<strong>Location:</strong> None"}</div>
          <div class="detail-block">${detail.conference_link ? `<strong>Conference:</strong> <a href="${esc(detail.conference_link)}" target="_blank" rel="noreferrer">${esc(detail.conference_link)}</a>` : "<strong>Conference:</strong> None"}</div>
          <div class="detail-block"><strong>Attachments</strong><ul class="attachment-list">${attachments || "<li>No attachments.</li>"}</ul></div>
          <div class="detail-block"><strong>Description</strong><div style="margin-top:6px; white-space:pre-wrap;">${esc(description)}</div></div>
          <div class="detail-block"><strong>Attendees</strong><ul class="attendee-list">${attendees || "<li>No attendees.</li>"}</ul></div>
          <div class="detail-block">
            <div class="event-actions" style="display:flex; flex-wrap:wrap; gap:6px;">
              ${canRsvp ? `<button type="button" class="rsvp-chip ${currentResponse === "accepted" ? "active" : ""}" data-rsvp-status="accepted" data-calendar-id="${esc(detail.calendar_id)}" data-event-id="${esc(detail.event_id)}">Accept</button>` : ""}
              ${canRsvp ? `<button type="button" class="rsvp-chip ${currentResponse === "tentative" ? "active" : ""}" data-rsvp-status="tentative" data-calendar-id="${esc(detail.calendar_id)}" data-event-id="${esc(detail.event_id)}">Tentative</button>` : ""}
              ${canRsvp ? `<button type="button" class="rsvp-chip ${currentResponse === "declined" ? "active" : ""}" data-rsvp-status="declined" data-calendar-id="${esc(detail.calendar_id)}" data-event-id="${esc(detail.event_id)}">Decline</button>` : ""}
              ${canReschedule ? `<button type="button" class="chip-btn" data-reschedule-minutes="15" data-calendar-id="${esc(detail.calendar_id)}" data-event-id="${esc(detail.event_id)}" data-event-start="${esc(detail.start)}" data-event-end="${esc(detail.end)}" data-event-timezone="${esc(detail.timezone || "UTC")}">+15m</button>` : ""}
              ${canReschedule ? `<button type="button" class="chip-btn" data-reschedule-minutes="30" data-calendar-id="${esc(detail.calendar_id)}" data-event-id="${esc(detail.event_id)}" data-event-start="${esc(detail.start)}" data-event-end="${esc(detail.end)}" data-event-timezone="${esc(detail.timezone || "UTC")}">+30m</button>` : ""}
              ${canReschedule ? `<button type="button" class="chip-btn" data-reschedule-minutes="60" data-calendar-id="${esc(detail.calendar_id)}" data-event-id="${esc(detail.event_id)}" data-event-start="${esc(detail.start)}" data-event-end="${esc(detail.end)}" data-event-timezone="${esc(detail.timezone || "UTC")}">+1h</button>` : ""}
              ${canDelete ? `<button type="button" class="chip-btn" data-cancel-event="1" data-calendar-id="${esc(detail.calendar_id)}" data-event-id="${esc(detail.event_id)}">Cancel event</button>` : ""}
              ${canEdit ? `<button type="button" class="chip-btn" data-open-event-editor="edit" data-calendar-id="${esc(detail.calendar_id)}" data-event-id="${esc(detail.event_id)}">Edit</button>` : ""}
            </div>
          </div>
        </div>
      </section>
    </div>
  `;
}

function renderEventEditorPanel(
  draft: EventEditorDraft | undefined,
  calendars: CalendarCatalogItem[],
  fallbackTimezone: string
): string {
  if (!draft) return "";
  const calendarOptions = calendars.length
    ? calendars
        .map(
          (item) =>
            `<option value="${esc(item.id)}" ${item.id === draft.calendar_id ? "selected" : ""}>${esc(item.summary)}</option>`
        )
        .join("")
    : `<option value="${esc(draft.calendar_id)}">${esc(draft.calendar_id)}</option>`;
  const title = draft.mode === "create" ? "Create event" : "Edit event";

  return `
    <div class="overlay" role="dialog" aria-modal="true">
      <section class="panel">
        <div class="panel-head">
          <div>
            <div class="panel-title">${esc(title)}</div>
            <div class="panel-sub">Self-service calendar action</div>
          </div>
          <button type="button" class="nav-btn" data-close-event-editor="1">Close</button>
        </div>
        <div class="panel-body">
          <form class="event-editor-form" data-event-editor-form="1">
            <input type="hidden" name="mode" value="${esc(draft.mode)}" />
            <input type="hidden" name="event_id" value="${esc(draft.event_id || "")}" />
            <div class="editor-row">
              <label class="editor-field">
                <span>Calendar</span>
                <select name="calendar_id">
                  ${calendarOptions}
                </select>
              </label>
              <label class="editor-field">
                <span>Timezone</span>
                <input name="timezone" value="${esc(draft.timezone || fallbackTimezone)}" />
              </label>
            </div>
            <label class="editor-field">
              <span>Title</span>
              <input name="summary" value="${esc(draft.summary)}" required />
            </label>
            <div class="editor-row">
              <label class="editor-field">
                <span>Start</span>
                <input type="datetime-local" name="start_local" value="${esc(draft.start_local)}" required />
              </label>
              <label class="editor-field">
                <span>End</span>
                <input type="datetime-local" name="end_local" value="${esc(draft.end_local)}" required />
              </label>
            </div>
            <label class="editor-field">
              <span>Location</span>
              <input name="location" value="${esc(draft.location || "")}" />
            </label>
            <label class="editor-field">
              <span>Attendees (comma-separated emails)</span>
              <input name="attendees_csv" value="${esc(draft.attendees_csv || "")}" />
            </label>
            <label class="editor-field">
              <span>Description</span>
              <textarea name="description">${esc(draft.description || "")}</textarea>
            </label>
            <label class="inline-toggle">
              <input
                type="checkbox"
                name="create_conference"
                ${draft.create_conference ? "checked" : ""}
              />
              Add Google Meet conference
            </label>
            <div class="editor-actions">
              <button type="button" class="nav-btn" data-close-event-editor="1">Cancel</button>
              <button type="submit" class="action-btn">${draft.mode === "create" ? "Create event" : "Save changes"}</button>
            </div>
          </form>
        </div>
      </section>
    </div>
  `;
}

function renderEmailDetailPanel(detail: EmailDetail | undefined, capabilities?: UiToolCapabilities): string {
  if (!detail) return "";
  const body = detail.text_body || detail.html_body || detail.snippet || "No body content.";
  const labels = new Set(detail.labels || []);
  const isUnread = detail.is_unread || labels.has("UNREAD");
  const inInbox = labels.has("INBOX");
  const inSpam = labels.has("SPAM");
  const inTrash = labels.has("TRASH");
  const canRead = capabilities?.can_mark_email_read ?? false;
  const canUnread = capabilities?.can_mark_email_unread ?? false;
  const canArchive = capabilities?.can_archive_email ?? false;
  const canTrash = capabilities?.can_trash_email ?? false;
  const canUntrash = capabilities?.can_untrash_email ?? false;
  const canSpam = capabilities?.can_mark_email_spam ?? false;
  const canNotSpam = capabilities?.can_mark_email_not_spam ?? false;
  const statusChips = [
    isUnread ? `<span class="status-chip">Unread</span>` : `<span class="status-chip">Read</span>`,
    inInbox ? `<span class="status-chip">Inbox</span>` : "",
    inTrash ? `<span class="status-chip">Trash</span>` : "",
    inSpam ? `<span class="status-chip">Spam</span>` : "",
  ].filter(Boolean).join("");
  const attachments = detail.attachments
    .map((attachment) => {
      const label = attachment.mime_type ? `${attachment.filename} (${attachment.mime_type})` : attachment.filename;
      return `
        <li>
          <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
            <span>${esc(label)}</span>
            <button
              type="button"
              class="email-chip"
              data-email-attachment-download="1"
              data-message-id="${esc(detail.message_id)}"
              data-attachment-id="${esc(attachment.attachment_id)}"
              data-filename="${esc(attachment.filename)}"
              data-mime-type="${esc(attachment.mime_type || "")}">
              Download
            </button>
          </div>
        </li>
      `;
    })
    .join("");
  return `
    <div class="overlay" role="dialog" aria-modal="true">
      <section class="panel">
        <div class="panel-head">
          <div>
            <div class="panel-title">${esc(detail.subject || "(No subject)")}</div>
            <div class="panel-sub">${esc(detail.from_value)}${detail.date ? ` · ${esc(detail.date)}` : ""}</div>
          </div>
          <button type="button" class="nav-btn" data-close-email="1">Close</button>
        </div>
        <div class="panel-body">
          <div class="detail-block"><strong>From:</strong> ${esc(detail.from_value)}</div>
          <div class="detail-block"><strong>To:</strong> ${esc(detail.to || "(not set)")}</div>
          ${detail.cc ? `<div class="detail-block"><strong>Cc:</strong> ${esc(detail.cc)}</div>` : ""}
          <div class="detail-block"><strong>Labels:</strong> ${esc(detail.labels.join(", ") || "none")}</div>
          <div class="detail-block"><strong>Status:</strong> <div class="email-statuses" style="margin-top:6px;">${statusChips}</div></div>
          <div class="detail-block"><strong>Attachments</strong><ul class="attachment-list">${attachments || "<li>No attachments.</li>"}</ul></div>
          <div class="detail-block"><strong>Body</strong><div style="margin-top:6px; white-space:pre-wrap;">${esc(body)}</div></div>
          <div class="detail-block">
            <div class="email-actions">
              ${canRead ? `<button type="button" class="email-chip ${!isUnread ? "active" : ""}" data-email-action="mark_read" data-message-id="${esc(detail.message_id)}">Mark read</button>` : ""}
              ${canUnread ? `<button type="button" class="email-chip ${isUnread ? "active" : ""}" data-email-action="mark_unread" data-message-id="${esc(detail.message_id)}">Mark unread</button>` : ""}
              ${canArchive ? `<button type="button" class="email-chip ${!inInbox ? "active" : ""}" data-email-action="archive" data-message-id="${esc(detail.message_id)}">Archive</button>` : ""}
              ${canTrash ? `<button type="button" class="email-chip ${inTrash ? "active" : ""}" data-email-action="trash" data-message-id="${esc(detail.message_id)}">Trash</button>` : ""}
              ${canUntrash && inTrash ? `<button type="button" class="email-chip" data-email-action="untrash" data-message-id="${esc(detail.message_id)}">Restore</button>` : ""}
              ${canSpam ? `<button type="button" class="email-chip ${inSpam ? "active" : ""}" data-email-action="spam" data-message-id="${esc(detail.message_id)}">Spam</button>` : ""}
              ${canNotSpam && inSpam ? `<button type="button" class="email-chip" data-email-action="not_spam" data-message-id="${esc(detail.message_id)}">Not spam</button>` : ""}
              <button type="button" class="email-chip" data-action-msg="${esc(`Reply to ${detail.from_value} about: ${detail.subject}`)}">Reply in chat</button>
            </div>
          </div>
        </div>
      </section>
    </div>
  `;
}
