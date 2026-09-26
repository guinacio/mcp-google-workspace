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
import { sanitizeEmailHtml } from "./email-html";
import { html, setHtml, textWithBreaks } from "./safe-html";
import type { SafeHtml } from "./safe-html";
import { isLinkKind, safeExternalUrl } from "./urls";
import type { LinkKind } from "./urls";

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

function fmtWeekRange(start: string, end: string): string {
  try {
    const startDate = new Date(`${start}T00:00:00`);
    const endDate = new Date(`${end}T00:00:00`);
    const startLabel = startDate.toLocaleDateString([], { month: "short", day: "numeric" });
    const endLabel = endDate.toLocaleDateString([], {
      month: startDate.getMonth() === endDate.getMonth() ? undefined : "short",
      day: "numeric",
      year: startDate.getFullYear() === endDate.getFullYear() ? undefined : "numeric",
    });
    return `${startLabel} – ${endLabel}`;
  } catch {
    return `${start} – ${end}`;
  }
}

function dayNumber(value: string): string {
  try {
    return String(new Date(`${value}T00:00:00`).getDate());
  } catch {
    return value.slice(-2);
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

function renderPlainTextEmailBody(text: string): SafeHtml {
  const normalized = text.replace(/\r\n?/g, "\n").trim();
  if (!normalized) {
    return html`<p class="email-body-empty">No body content.</p>`;
  }
  const paragraphs = normalized.split(/\n{2,}/).filter(Boolean);
  return html`${paragraphs.map((paragraph) => {
    const lines = paragraph.split("\n");
    if (lines.every((line) => line.trim().startsWith(">"))) {
      const quoted = lines.map((line) => line.replace(/^\s*> ?/, "")).join("\n");
      return html`<blockquote>${textWithBreaks(quoted)}</blockquote>`;
    }
    return html`<p>${textWithBreaks(lines.join("\n"))}</p>`;
  })}`;
}

function renderEmailBody(detail: EmailDetail): SafeHtml {
  if (detail.html_body?.trim()) {
    // Filled with sanitized DOM nodes by mountEmailHtml() after the chrome is rendered.
    return html`<div class="email-html" data-email-html-body="1"></div>`;
  }
  if (detail.text_body?.trim()) {
    return html`<div class="email-plain">${renderPlainTextEmailBody(detail.text_body)}</div>`;
  }
  if (detail.snippet?.trim()) {
    return html`<div class="email-plain">${renderPlainTextEmailBody(detail.snippet)}</div>`;
  }
  return html`<div class="email-plain"><p class="email-body-empty">No body content.</p></div>`;
}

function mountEmailHtml(root: HTMLElement, detail: EmailDetail | undefined): void {
  const htmlBody = detail?.html_body;
  if (!htmlBody?.trim()) return;
  const container = root.querySelector<HTMLElement>("[data-email-html-body]");
  container?.replaceChildren(sanitizeEmailHtml(htmlBody));
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
      type: "open_link";
      url: string;
      kind: LinkKind;
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

/**
 * Anchors never navigate the App frame directly: every link activation is re-validated
 * and handed to the host-mediated open-link adapter. Returns true when handled.
 */
function routeLinkActivation(event: MouseEvent): boolean {
  const target = event.target as Element | null;
  const link = target?.closest?.<HTMLAnchorElement>("a[href]");
  if (!link) return false;
  event.preventDefault();
  event.stopPropagation();
  const kind = link.dataset.openLink;
  if (isLinkKind(kind)) {
    const url = safeExternalUrl(link.getAttribute("href"), kind);
    if (url) _onAction({ type: "open_link", url, kind });
  }
  return true;
}

function showEventTooltipLayer(anchor: HTMLElement, source: HTMLElement) {
  if (!source.textContent?.trim()) {
    hideEventTooltipLayer();
    return;
  }
  const layer = ensureEventTooltipLayer();
  // Clone the already-rendered nodes instead of re-parsing serialized markup.
  layer.replaceChildren(...Array.from(source.childNodes, (node) => node.cloneNode(true)));
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
  max-width: 1400px;
  margin: 0 auto;
  padding: 18px 16px 32px;
  display: grid;
  gap: 18px;
}

/* ── Top bar ─────────────────────────────────────────────────────────── */
.top-bar {
  background: var(--workspace-header);
  border: 1px solid color-mix(in srgb, var(--md-sys-color-outline-variant) 82%, transparent);
  border-radius: var(--radius-lg);
  box-shadow: var(--md-sys-elevation-1);
  padding: 15px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}

.top-brand {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 12px;
}

.workspace-mark {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  background: linear-gradient(135deg, #4285f4 0%, #1a73e8 100%);
  color: #fff;
  box-shadow: 0 3px 7px color-mix(in srgb, #1a73e8 30%, transparent);
  font-size: 1.02rem;
  font-weight: 700;
}

.top-bar h1 {
  margin: 0;
  font-size: 1.08rem;
  font-weight: 600;
  letter-spacing: -0.015em;
}

.top-eyebrow,
.calendar-kicker {
  color: var(--md-sys-color-on-surface-variant);
  font-size: 0.66rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.top-sub {
  color: var(--md-sys-color-on-surface-variant);
  font-size: 0.76rem;
  margin-top: 1px;
}

.quick-stats {
  display: inline-flex;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.stat-chip {
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--md-sys-color-outline-variant) 85%, transparent);
  background: var(--md-sys-color-surface-container-high);
  color: var(--md-sys-color-on-surface-variant);
  padding: 6px 10px;
  font-size: 0.72rem;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.stat-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--md-sys-color-primary);
}

.stat-chip-mail .stat-dot {
  background: #34a853;
}

/* ── Two-column layout ───────────────────────────────────────────────── */
.main-grid {
  display: grid;
  grid-template-columns: minmax(0, 68fr) minmax(320px, 32fr);
  gap: 18px;
}

.main-grid-full {
  grid-template-columns: 1fr;
}

.surface {
  background: var(--md-sys-color-surface-container);
  border: 1px solid color-mix(in srgb, var(--md-sys-color-outline-variant) 85%, transparent);
  border-radius: var(--radius-lg);
  box-shadow: var(--md-sys-elevation-1);
}

.calendar-shell {
  overflow: hidden;
  padding: 0;
}

.calendar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 20px 16px;
  overflow-x: auto;
}

.calendar-heading {
  min-width: max-content;
}

.calendar-title-line {
  display: flex;
  align-items: center;
  gap: 9px;
  margin: 2px 0;
}

.calendar-title-line h2 {
  margin: 0;
  color: var(--md-sys-color-on-surface);
  font-size: 1.12rem;
  font-weight: 500;
  letter-spacing: -0.016em;
}

.calendar-count {
  border-radius: 999px;
  background: var(--workspace-tint);
  color: var(--md-sys-color-primary);
  font-size: 0.69rem;
  font-weight: 700;
  padding: 3px 7px;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 11px;
  gap: 10px;
}

.section-title {
  color: var(--md-sys-color-on-surface);
  font-size: 0.98rem;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.section-subtitle {
  color: var(--md-sys-color-on-surface-variant);
  font-size: 0.73rem;
}

/* ── Navigation / chip buttons ───────────────────────────────────────── */
.week-nav {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px;
  border: 1px solid var(--md-sys-color-outline-variant);
  border-radius: 999px;
}

.calendar-actions {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 7px;
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
  border: 1px solid transparent;
  background: transparent;
  color: var(--md-sys-color-on-surface-variant);
  border-radius: 999px;
  padding: 6px 11px;
  font-size: 0.73rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.nav-btn:hover,
.chip-btn:hover,
.action-btn:hover {
  background: var(--workspace-tint);
  border-color: transparent;
  color: var(--md-sys-color-primary);
}

.nav-icon {
  width: 30px;
  height: 30px;
  padding: 0;
  display: grid;
  place-items: center;
  font-size: 1.3rem;
  line-height: 1;
}

.nav-today {
  padding-inline: 9px;
}

.create-event-btn {
  background: var(--md-sys-color-primary);
  color: var(--md-sys-color-on-primary);
  box-shadow: 0 1px 2px color-mix(in srgb, var(--md-sys-color-primary) 35%, transparent);
}

.create-event-btn:hover {
  background: color-mix(in srgb, var(--md-sys-color-primary) 88%, #000);
  color: var(--md-sys-color-on-primary);
}

/* ── Weekly calendar grid ────────────────────────────────────────────── */
.week-grid {
  display: grid;
  grid-template-columns: repeat(var(--day-count, 7), minmax(150px, 1fr));
  gap: 0;
  position: relative;
  isolation: isolate;
  overflow-x: auto;
  border-top: 1px solid var(--md-sys-color-outline-variant);
  padding-bottom: 0;
  scrollbar-gutter: stable both-edges;
}

.day-col {
  background: var(--md-sys-color-surface-container);
  border: 0;
  border-right: 1px solid var(--md-sys-color-outline-variant);
  padding: 12px 10px;
  min-height: 470px;
  overflow: visible;
  position: relative;
  z-index: 1;
}

.day-col:last-child {
  border-right: 0;
}

.day-col:hover {
  z-index: 15;
}

.day-col.today {
  background: color-mix(in srgb, var(--md-sys-color-primary) 3%, var(--md-sys-color-surface-container));
}

.day-head {
  min-height: 42px;
  margin-bottom: 12px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 6px;
}

.day-label {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.day-label-name {
  color: var(--md-sys-color-on-surface-variant);
  font-size: 0.69rem;
  font-weight: 700;
  letter-spacing: 0.065em;
  text-transform: uppercase;
}

.day-label-number {
  width: 27px;
  height: 27px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: var(--md-sys-color-on-surface);
  font-size: 0.96rem;
  font-weight: 500;
}

.day-col.today .day-label-name {
  color: var(--md-sys-color-primary);
}

.day-col.today .day-label-number {
  background: var(--md-sys-color-primary);
  color: var(--md-sys-color-on-primary);
}

.day-all-day {
  display: grid;
  gap: 5px;
  margin-bottom: 10px;
}

.all-day-chip {
  font-size: 0.7rem;
  border-radius: 6px;
  padding: 4px 7px;
  background: var(--workspace-tint);
  color: var(--md-sys-color-primary);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.3;
}

.day-events {
  display: grid;
  gap: 7px;
  overflow: visible;
}

.day-empty {
  color: var(--md-sys-color-outline);
  font-size: 0.7rem;
  padding: 12px 2px;
}

/* ── Event card ──────────────────────────────────────────────────────── */
.calendar-event {
  border-radius: 7px;
  border-left: 4px solid var(--event-color);
  background: color-mix(in srgb, var(--event-color) 16%, var(--md-sys-color-surface-container));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--event-color) 22%, transparent);
  padding: 8px 9px;
  cursor: pointer;
  position: relative;
  z-index: 1;
  transition: background 0.15s;
}

.calendar-event:hover {
  background: color-mix(in srgb, var(--event-color) 26%, var(--md-sys-color-surface-container));
  box-shadow: var(--md-sys-elevation-1), inset 0 0 0 1px color-mix(in srgb, var(--event-color) 36%, transparent);
  z-index: 120;
}

.event-time {
  font-size: 0.68rem;
  color: color-mix(in srgb, var(--md-sys-color-on-surface) 72%, var(--event-color));
  font-weight: 700;
  letter-spacing: 0.01em;
}

.event-title {
  font-size: 0.76rem;
  font-weight: 600;
  margin-top: 2px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.event-meta {
  margin-top: 3px;
  color: var(--md-sys-color-on-surface-variant);
  font-size: 0.68rem;
}

/* ── Event hover tooltip ─────────────────────────────────────────────── */
.event-hover {
  display: none !important;
}

.event-tooltip-layer {
  position: fixed;
  z-index: 2147483000;
  min-width: 238px;
  max-width: 340px;
  border-radius: 12px;
  border: 1px solid var(--md-sys-color-outline-variant);
  background: var(--md-sys-color-surface);
  box-shadow: 0 6px 18px color-mix(in srgb, #000 24%, transparent), var(--md-sys-elevation-2);
  overflow: hidden;
  font-size: 0.74rem;
  line-height: 1.45;
  color: var(--md-sys-color-on-surface);
  word-break: break-word;
  max-height: 220px;
  overflow-y: auto;
  pointer-events: none;
}

.event-tooltip-head {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 11px 12px 7px;
}

.event-tooltip-color {
  width: 9px;
  height: 9px;
  margin-top: 4px;
  border-radius: 50%;
  flex: 0 0 auto;
  background: var(--tooltip-color, var(--md-sys-color-primary));
}

.event-tooltip-title {
  color: var(--md-sys-color-on-surface);
  font-size: 0.82rem;
  font-weight: 650;
  line-height: 1.3;
}

.event-tooltip-content {
  display: grid;
  gap: 5px;
  padding: 1px 12px 10px 29px;
  color: var(--md-sys-color-on-surface-variant);
}

.event-tooltip-description {
  margin-top: 3px;
  padding-top: 8px;
  border-top: 1px solid var(--md-sys-color-outline-variant);
  color: var(--md-sys-color-on-surface);
}

.event-tooltip-foot {
  padding: 7px 12px;
  border-top: 1px solid var(--md-sys-color-outline-variant);
  background: var(--md-sys-color-surface-container);
  color: var(--md-sys-color-primary);
  font-size: 0.7rem;
  font-weight: 600;
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
  gap: 16px;
  align-content: start;
}

.inbox-shell {
  overflow: hidden;
  padding: 16px 0 0;
  border-radius: var(--radius-lg);
}

.inbox-shell .section-head {
  margin: 0;
  padding: 0 16px 12px;
}

/* ── Inbox ────────────────────────────────────────────────────────────── */
.inbox-list {
  display: grid;
  gap: 0;
  max-height: 460px;
  overflow: auto;
  border-top: 1px solid var(--md-sys-color-outline-variant);
}

.inbox-row {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
  min-height: 61px;
  padding: 10px 13px;
  border-bottom: 1px solid var(--md-sys-color-outline-variant);
  border-left: 3px solid transparent;
  cursor: pointer;
  transition: background 0.12s, box-shadow 0.12s;
}

.inbox-row:hover {
  background: color-mix(in srgb, var(--md-sys-color-primary) 8%, var(--md-sys-color-surface-container-high));
  box-shadow: inset 2px 0 0 var(--md-sys-color-primary);
}

.inbox-row.unread {
  background: var(--workspace-tint);
  border-left-color: var(--md-sys-color-primary);
}

.avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--md-sys-color-primary) 18%, transparent);
  color: var(--md-sys-color-primary);
  display: grid;
  place-items: center;
  font-size: 0.72rem;
  font-weight: 700;
}

.mail-avatar {
  background: color-mix(in srgb, var(--md-sys-color-primary) 20%, var(--md-sys-color-surface-container-highest));
}

.mail-content {
  min-width: 0;
}

.mail-from {
  font-size: 0.76rem;
  font-weight: 600;
  color: var(--md-sys-color-on-surface);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.mail-subject {
  margin-top: 1px;
  font-size: 0.72rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--md-sys-color-on-surface);
}

.mail-subject.unread {
  font-weight: 650;
  color: var(--md-sys-color-on-surface);
}

.mail-subject span {
  color: var(--md-sys-color-on-surface-variant);
  font-weight: 400;
}

.inbox-title span {
  margin-left: 6px;
  color: var(--md-sys-color-on-primary-container);
  background: var(--md-sys-color-primary-container);
  border-radius: 999px;
  padding: 3px 7px;
  font-size: 0.66rem;
  font-weight: 600;
}

.mail-date {
  align-self: start;
  color: var(--md-sys-color-on-surface-variant);
  font-size: 0.67rem;
  font-weight: 600;
  padding-left: 5px;
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

.email-panel {
  width: min(920px, 96vw);
  padding: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--md-sys-color-surface);
}

.event-panel {
  width: min(760px, 96vw);
  padding: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--md-sys-color-surface);
}

.event-toolbar {
  min-height: 54px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--md-sys-color-outline-variant);
  background: var(--md-sys-color-surface-container);
  color: var(--md-sys-color-on-surface-variant);
  font-size: 0.82rem;
  font-weight: 600;
}

.event-toolbar .nav-btn {
  margin-left: auto;
}

.event-panel-body {
  gap: 16px;
  padding: 22px clamp(18px, 4vw, 36px) 28px;
  margin-top: 0;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}

.event-subject-line {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.event-color-dot {
  width: 13px;
  height: 13px;
  margin-top: 6px;
  border-radius: 50%;
  flex: 0 0 auto;
  background: var(--md-sys-color-primary);
}

.event-subject-line h2 {
  margin: 0;
  color: var(--md-sys-color-on-surface);
  font-size: clamp(1.15rem, 2vw, 1.45rem);
  line-height: 1.3;
  font-weight: 500;
}

.event-when {
  margin-top: 4px;
  color: var(--md-sys-color-on-surface-variant);
  font-size: 0.82rem;
}

.event-command-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  padding: 10px 0;
  border-block: 1px solid var(--md-sys-color-outline-variant);
}

.event-info-list {
  display: grid;
  gap: 12px;
}

.event-info-row {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
  color: var(--md-sys-color-on-surface);
  font-size: 0.84rem;
  line-height: 1.45;
}

.event-info-icon {
  color: var(--md-sys-color-on-surface-variant);
  font-size: 1rem;
  line-height: 1.25;
  text-align: center;
}

.event-info-label {
  display: block;
  margin-bottom: 2px;
  color: var(--md-sys-color-on-surface-variant);
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.event-description {
  white-space: pre-wrap;
  word-break: break-word;
}

.event-attendee-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 8px;
}

.event-attendee {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
}

.event-attendee-avatar {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--md-sys-color-primary-container);
  color: var(--md-sys-color-on-primary-container);
  font-size: 0.66rem;
  font-weight: 700;
}

.event-attendee-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.event-attendee-status {
  color: var(--md-sys-color-on-surface-variant);
  font-size: 0.72rem;
  text-transform: capitalize;
}

.event-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.event-attachment {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  max-width: 100%;
  padding: 7px 9px;
  border: 1px solid var(--md-sys-color-outline-variant);
  border-radius: 8px;
  background: var(--md-sys-color-surface-container-high);
}

.event-attachment-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.76rem;
}

.email-panel-body {
  gap: 14px;
  padding: 22px clamp(18px, 4vw, 42px) 26px;
  margin-top: 0;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}

.email-toolbar {
  min-height: 54px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--md-sys-color-outline-variant);
  background: var(--md-sys-color-surface-container);
  color: var(--md-sys-color-on-surface-variant);
  font-size: 0.82rem;
  font-weight: 600;
}

.email-toolbar .nav-btn {
  margin-left: auto;
}

.email-back {
  width: 34px;
  height: 34px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--md-sys-color-on-surface);
  cursor: pointer;
  font-size: 1.25rem;
  line-height: 1;
}

.email-back:hover {
  background: color-mix(in srgb, var(--md-sys-color-primary) 14%, transparent);
}

.email-subject-line {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.email-subject-line h2 {
  margin: 0;
  color: var(--md-sys-color-on-surface);
  font-size: clamp(1.15rem, 2vw, 1.45rem);
  line-height: 1.3;
  font-weight: 500;
}

.email-sender-row {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 12px 0 4px;
}

.email-sender-avatar {
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--md-sys-color-primary-container);
  color: var(--md-sys-color-on-primary-container);
  font-size: 0.78rem;
  font-weight: 700;
}

.email-sender-identities {
  min-width: 0;
  font-size: 0.82rem;
  color: var(--md-sys-color-on-surface);
}

.email-sender-identities span,
.email-recipient-extra,
.email-sender-row time {
  color: var(--md-sys-color-on-surface-variant);
  font-size: 0.72rem;
}

.email-sender-row time {
  text-align: right;
  white-space: nowrap;
}

.email-attachments {
  padding: 11px 13px;
  border: 1px solid var(--md-sys-color-outline-variant);
  border-radius: 12px;
  background: var(--md-sys-color-surface-container);
  font-size: 0.76rem;
}

.email-attachments .attachment-list {
  margin-top: 8px;
}

.gmail-message-surface {
  border-radius: 12px;
  box-shadow: var(--md-sys-elevation-1);
}

.email-footer-actions {
  display: flex;
  justify-content: flex-end;
  padding-top: 2px;
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

.email-meta-grid {
  display: grid;
  gap: 8px;
}

.email-body-block {
  padding: 0;
  overflow: hidden;
}

.email-body-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--md-sys-color-outline-variant);
  background: color-mix(in srgb, var(--md-sys-color-surface-container) 82%, transparent);
}

.email-body-mode {
  font-size: 0.68rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--md-sys-color-on-surface-variant);
}

.email-body-content {
  padding: clamp(18px, 4vw, 34px);
  max-height: min(58vh, 760px);
  overflow: auto;
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--md-sys-color-surface) 88%, transparent), transparent 90px),
    var(--md-sys-color-surface);
  color: var(--md-sys-color-on-surface);
}

.email-body-content p,
.email-body-content ul,
.email-body-content ol,
.email-body-content pre,
.email-body-content blockquote,
.email-body-content table,
.email-body-content h1,
.email-body-content h2,
.email-body-content h3,
.email-body-content h4,
.email-body-content h5,
.email-body-content h6 {
  margin: 0 0 0.9rem;
}

.email-body-content p:last-child,
.email-body-content ul:last-child,
.email-body-content ol:last-child,
.email-body-content pre:last-child,
.email-body-content blockquote:last-child,
.email-body-content table:last-child {
  margin-bottom: 0;
}

.email-body-content h1,
.email-body-content h2,
.email-body-content h3,
.email-body-content h4,
.email-body-content h5,
.email-body-content h6 {
  line-height: 1.25;
  color: var(--md-sys-color-on-surface);
}

.email-body-content h1 { font-size: 1.35rem; }
.email-body-content h2 { font-size: 1.2rem; }
.email-body-content h3 { font-size: 1.06rem; }
.email-body-content h4,
.email-body-content h5,
.email-body-content h6 { font-size: 0.95rem; }

.email-body-content ul,
.email-body-content ol {
  padding-left: 1.25rem;
}

.email-body-content li + li {
  margin-top: 0.35rem;
}

.email-body-content blockquote {
  padding: 0.85rem 1rem;
  border-left: 3px solid color-mix(in srgb, var(--md-sys-color-primary) 70%, transparent);
  background: color-mix(in srgb, var(--md-sys-color-primary) 8%, transparent);
  color: var(--md-sys-color-on-surface-variant);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}

.email-body-content pre,
.email-body-content code {
  font-family: var(--font-mono, "IBM Plex Mono", "SFMono-Regular", Consolas, monospace);
}

.email-body-content pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  padding: 0.9rem 1rem;
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--md-sys-color-surface-container-highest) 84%, transparent);
  border: 1px solid var(--md-sys-color-outline-variant);
}

.email-body-content table {
  border-collapse: collapse;
  max-width: 100%;
}

.email-body-content .email-layout-table {
  margin: 0;
}

.email-body-content .email-layout-table td,
.email-body-content .email-layout-table th {
  border: 0;
}

.email-body-content .email-data-table {
  width: 100%;
  margin: 0.9rem 0;
  border: 1px solid var(--md-sys-color-outline-variant);
}

.email-body-content .email-data-table th,
.email-body-content .email-data-table td {
  border: 1px solid var(--md-sys-color-outline-variant);
  padding: 0.5rem 0.65rem;
  vertical-align: top;
}

.email-body-content .email-data-table th {
  background: color-mix(in srgb, var(--md-sys-color-surface-container-highest) 80%, transparent);
  font-weight: 700;
}

.email-body-content img.email-html-image {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 0.5rem 0;
  border-radius: var(--radius-sm);
}

.email-image-blocked {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.35rem 0.6rem;
  border-radius: 999px;
  border: 1px solid var(--md-sys-color-outline-variant);
  color: var(--md-sys-color-on-surface-variant);
  background: color-mix(in srgb, var(--md-sys-color-surface-container-highest) 68%, transparent);
  font-size: 0.72rem;
}

.email-body-content a {
  color: var(--md-sys-color-primary);
  text-decoration: underline;
  text-underline-offset: 0.18em;
  cursor: pointer;
}

.email-body-content a:hover {
  color: color-mix(in srgb, var(--md-sys-color-primary) 78%, white);
}

.email-body-empty {
  color: var(--md-sys-color-on-surface-variant);
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

  .week-grid {
    grid-template-columns: repeat(var(--day-count, 7), minmax(140px, 1fr));
  }

  .day-col {
    min-height: 320px;
  }
}

@media (max-width: 760px) {
  .dashboard {
    padding: 10px 8px 20px;
    gap: 12px;
  }

  .top-bar {
    align-items: flex-start;
    flex-direction: column;
    padding: 14px;
  }

  .quick-stats {
    justify-content: flex-start;
  }

  .calendar-header {
    align-items: flex-start;
    flex-direction: column;
    padding: 16px 14px 14px;
  }

  .calendar-actions {
    justify-content: flex-start;
  }

  .calendar-title-line h2 {
    font-size: 1rem;
  }

  .week-grid {
    grid-template-columns: repeat(var(--day-count, 7), minmax(128px, 1fr));
  }

  .day-col {
    min-height: 380px;
    padding: 10px 8px;
  }

  .inbox-row {
    grid-template-columns: 30px minmax(0, 1fr) auto;
    min-height: 56px;
    padding: 9px 10px;
  }

  .avatar {
    width: 30px;
    height: 30px;
  }

  .email-panel {
    width: 100%;
    max-height: 100vh;
    border-radius: 0;
  }

  .email-panel-body {
    padding: 18px;
  }

  .email-subject-line {
    align-items: flex-start;
    flex-direction: column;
    gap: 8px;
  }

  .email-sender-row {
    grid-template-columns: 38px minmax(0, 1fr);
    align-items: start;
  }

  .email-sender-avatar {
    width: 38px;
    height: 38px;
  }

  .email-sender-row time {
    grid-column: 2;
    text-align: left;
  }

  .email-footer-actions {
    justify-content: flex-start;
  }

  .editor-row {
    grid-template-columns: 1fr;
  }
}
`;

export function renderLoading(root: HTMLElement) {
  hideEventTooltipLayer();
  setHtml(root, html`<div class="loading-state">Loading workspace dashboard...</div>`);
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

  setHtml(root, html`
    <div class="dashboard">
      ${data.ui_error ? html`<div class="banner error">${data.ui_error}</div>` : ""}
      ${data.ui_notice ? html`<div class="banner">${data.ui_notice}</div>` : ""}
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
        ${hasDashboard ? html`<div class="sidebar">${renderInboxPanel(inboxData.messages, inboxData.unreadCount)}</div>` : ""}
      </div>
      ${renderEventDetailPanel(data.event_detail, options.tool_capabilities)}
      ${renderEventEditorPanel(
        data.event_editor,
        options.calendar_catalog ?? [],
        data.weekly_calendar?.timezone ?? "UTC"
      )}
      ${renderEmailDetailPanel(data.email_detail, options.tool_capabilities)}
    </div>
  `);
  mountEmailHtml(root, data.email_detail);

  root.onmouseover = (event) => {
    const target = event.target as HTMLElement;
    const eventCard = target.closest<HTMLElement>("[data-open-event]");
    if (!eventCard) return;
    const source = eventCard.querySelector<HTMLElement>(".event-hover");
    if (!source) return;
    showEventTooltipLayer(eventCard, source);
  };

  root.onmousemove = (event) => {
    const target = event.target as HTMLElement;
    const eventCard = target.closest<HTMLElement>("[data-open-event]");
    if (!eventCard) return;
    const source = eventCard.querySelector<HTMLElement>(".event-hover");
    if (!source) return;
    showEventTooltipLayer(eventCard, source);
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

  root.onauxclick = (event) => {
    if (event.button === 1) {
      routeLinkActivation(event);
    }
  };

  root.onkeydown = (event) => {
    const target = event.target as HTMLElement;
    if (event.key === "Escape") {
      if (root.querySelector("[data-close-event-editor]")) {
        _onAction({ type: "close_event_editor" });
      } else if (root.querySelector("[data-close-email]")) {
        _onAction({ type: "close_email_detail" });
      } else if (root.querySelector("[data-close-event]")) {
        _onAction({ type: "close_event_detail" });
      }
      return;
    }
    if (event.key !== "Enter" && event.key !== " ") return;
    if (target.closest("button, a, input, select, textarea")) return;
    const eventCard = target.closest<HTMLElement>("[data-open-event]");
    if (!eventCard) return;
    const calendarId = eventCard.dataset.calendarId;
    const eventId = eventCard.dataset.eventId;
    if (!calendarId || !eventId) return;
    event.preventDefault();
    hideEventTooltipLayer();
    _onAction({ type: "select_event", calendarId, eventId });
  };

  root.onclick = (event) => {
    hideEventTooltipLayer();
    const target = event.target as HTMLElement;

    if (routeLinkActivation(event)) {
      return;
    }

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
      const url = safeExternalUrl(openAttachment.dataset.openAttachmentUrl, "attachment");
      if (url) {
        _onAction({ type: "open_attachment", url });
      }
      return;
    }

    const downloadAttachment = target.closest<HTMLElement>("[data-download-attachment-url]");
    if (downloadAttachment) {
      event.preventDefault();
      const url = safeExternalUrl(downloadAttachment.dataset.downloadAttachmentUrl, "attachment");
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

function renderTopBar(eventsCount: number, unreadCount?: number): SafeHtml {
  const unreadChip = unreadCount !== undefined ? html`<span class="stat-chip stat-chip-mail"><span class="stat-dot"></span>${unreadCount} unread</span>` : "";
  return html`
    <div class="top-bar surface">
      <div class="top-brand">
        <div class="workspace-mark" aria-hidden="true">W</div>
        <div>
          <div class="top-eyebrow">Google Workspace</div>
          <h1>${getGreeting()}</h1>
          <div class="top-sub">${fmtTopDate()}</div>
        </div>
      </div>
      <div class="quick-stats">
        <span class="stat-chip stat-chip-calendar"><span class="stat-dot"></span>${eventsCount} events</span>
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
): SafeHtml {
  if (!weekly) {
    return html`<section class="calendar-shell surface"><div class="section-subtitle">Calendar data unavailable.</div></section>`;
  }
  const weekRange = fmtWeekRange(weekly.week_start, weekly.week_end);
  const canCreate = options.tool_capabilities?.can_create_event ?? false;
  const canToggleWeekend = options.tool_capabilities?.can_toggle_weekend ?? false;
  const canSelectCalendars = options.tool_capabilities?.can_select_calendars ?? false;
  return html`
    <section class="calendar-shell surface">
      <div class="calendar-header">
        <div class="calendar-heading">
          <div class="calendar-kicker">Google Calendar</div>
          <div class="calendar-title-line">
            <h2>Week of ${weekRange}</h2>
            <span class="calendar-count">${weekly.total_events} event${weekly.total_events === 1 ? "" : "s"}</span>
          </div>
          <div class="section-subtitle">${weekly.timezone}</div>
        </div>
        <div class="calendar-actions">
          <div class="week-nav">
            <button type="button" class="nav-btn nav-icon" data-week-nav="prev" aria-label="Previous week" title="Previous week">‹</button>
            <button type="button" class="nav-btn nav-today" data-week-nav="today" title="Return to the current week">Today</button>
            <button type="button" class="nav-btn nav-icon" data-week-nav="next" aria-label="Next week" title="Next week">›</button>
          </div>
          ${canToggleWeekend ? html`
            <label class="inline-toggle" title="Include Saturday and Sunday in the calendar">
              <input type="checkbox" data-toggle-weekend="1" ${options.include_weekend ? "checked" : ""} />
              Show weekend
            </label>
          ` : ""}
          ${canSelectCalendars ? renderCalendarSelector(options.calendar_catalog, options.selected_calendar_ids) : ""}
          ${canCreate ? html`<button type="button" class="action-btn create-event-btn" data-open-event-editor="create" title="Create a calendar event"><span aria-hidden="true">＋</span> Create</button>` : ""}
        </div>
      </div>
      <div class="week-grid" style="--day-count:${Number(weekly.days.length) || 0}">${weekly.days.map((day) => renderDay(day, weekly.timezone, canCreate, options.tool_capabilities))}</div>
    </section>
  `;
}

function renderCalendarSelector(catalog: CalendarCatalogItem[], selectedIds: string[]): SafeHtml {
  if (!catalog.length) {
    return html``;
  }
  const options = catalog
    .map((item) => {
      const isChecked = selectedIds.includes(item.id);
      const role = item.access_role ? html`<small>${item.access_role}</small>` : "";
      return html`
        <label class="calendar-option">
          <input type="checkbox" data-calendar-id="${item.id}" ${isChecked ? "checked" : ""} />
          <span>${item.summary} ${item.primary ? html`<small>(primary)</small>` : role}</span>
        </label>
      `;
    });
  return html`
    <details class="calendar-select">
      <summary class="chip-btn" title="Choose the calendars shown in this view">Calendars</summary>
      <div class="calendar-select-list">${options}</div>
    </details>
  `;
}

function renderDay(
  day: WeeklyCalendarDay,
  timezone: string,
  canCreate: boolean,
  capabilities?: UiToolCapabilities
): SafeHtml {
  const timed = day.timed_events.map((event) => renderEvent(event, timezone, capabilities));
  const allDay = day.all_day_events.map(
    (event) => html`<div class="all-day-chip" title="${event.title}">${event.title}</div>`
  );
  const empty = !timed.length && !allDay.length ? html`<div class="day-empty">No events</div>` : "";
  return html`
    <div class="day-col ${day.is_today ? "today" : ""}">
      <div class="day-head">
        <div class="day-label">
          <span class="day-label-name">${day.day_label}</span>
          <span class="day-label-number">${dayNumber(day.date)}</span>
        </div>
        ${canCreate ? html`<button type="button" class="chip-btn" data-open-event-editor="create" data-seed-date="${day.date}" title="Create an event on ${day.date}">Add</button>` : ""}
      </div>
      ${allDay.length ? html`<div class="day-all-day">${allDay}</div>` : ""}
      <div class="day-events">${timed.length ? timed : empty}</div>
    </div>
  `;
}

function renderEvent(event: WeeklyCalendarEvent, timezone: string, capabilities?: UiToolCapabilities): SafeHtml {
  const eventColor = colorVar(event);
  const metaParts = [event.location || "", event.attendee_count ? `${event.attendee_count} attendees` : "", event.has_conference ? "Meet" : ""]
    .filter(Boolean)
    .join(" \u00b7 ");

  const tooltipDetails = [
    html`<span>◷ ${fmtTime(event.start)} – ${fmtTime(event.end)}</span>`,
    event.location ? html`<span>⌖ ${event.location}</span>` : "",
    event.attendee_count ? html`<span>♙ ${event.attendee_count} guest${event.attendee_count > 1 ? "s" : ""}</span>` : "",
    event.has_conference ? html`<span>↗ Google Meet</span>` : "",
  ];
  const tooltip = html`
    <div class="event-tooltip-head">
      <span class="event-tooltip-color" style="--tooltip-color:var(${eventColor})"></span>
      <div class="event-tooltip-title">${event.title}</div>
    </div>
    <div class="event-tooltip-content">
      ${tooltipDetails}
      ${event.description_snippet ? html`<div class="event-tooltip-description">${event.description_snippet}</div>` : ""}
    </div>
    <div class="event-tooltip-foot">Click to open event</div>
  `;

  return html`
    <article class="calendar-event" style="--event-color: var(${eventColor})" data-open-event="1" data-calendar-id="${event.calendar_id || ""}" data-event-id="${event.event_id || ""}" role="button" tabindex="0" aria-label="Open event: ${event.title}">
      <div class="event-time">${fmtTime(event.start)} - ${fmtTime(event.end)}</div>
      <div class="event-title">${event.title}</div>
      ${metaParts ? html`<div class="event-meta">${metaParts}</div>` : ""}
      <div class="event-actions">${renderEventActionChips(event, timezone, capabilities)}</div>
      <div class="event-hover">${tooltip}</div>
    </article>
  `;
}

function renderEventActionChips(
  event: WeeklyCalendarEvent,
  timezone: string,
  capabilities?: UiToolCapabilities
): SafeHtml {
  if (!event.event_id || !event.calendar_id) return html``;
  const current = event.attendee_response_status || "";
  const canRsvp = capabilities?.can_rsvp ?? false;
  const canReschedule = capabilities?.can_reschedule_event ?? false;
  const canDelete = capabilities?.can_delete_event ?? false;
  const chip = (status: "accepted" | "tentative" | "declined", label: string) => html`
    <button
      type="button"
      class="rsvp-chip ${current === status ? "active" : ""}"
      data-rsvp-status="${status}"
      data-calendar-id="${event.calendar_id || ""}"
      data-event-id="${event.event_id || ""}">
      ${label}
    </button>
  `;
  return html`
    ${canRsvp ? chip("accepted", "Yes") : ""}
    ${canRsvp ? chip("tentative", "Maybe") : ""}
    ${canRsvp ? chip("declined", "No") : ""}
    ${canReschedule ? html`<button type="button" class="chip-btn" data-reschedule-minutes="15" data-calendar-id="${event.calendar_id || ""}" data-event-id="${event.event_id || ""}" data-event-start="${event.start}" data-event-end="${event.end}" data-event-timezone="${timezone}">+15m</button>` : ""}
    ${canReschedule ? html`<button type="button" class="chip-btn" data-reschedule-minutes="30" data-calendar-id="${event.calendar_id || ""}" data-event-id="${event.event_id || ""}" data-event-start="${event.start}" data-event-end="${event.end}" data-event-timezone="${timezone}">+30m</button>` : ""}
    ${canReschedule ? html`<button type="button" class="chip-btn" data-reschedule-minutes="60" data-calendar-id="${event.calendar_id || ""}" data-event-id="${event.event_id || ""}" data-event-start="${event.start}" data-event-end="${event.end}" data-event-timezone="${timezone}">+1h</button>` : ""}
    ${canDelete ? html`<button type="button" class="chip-btn" data-cancel-event="1" data-calendar-id="${event.calendar_id || ""}" data-event-id="${event.event_id || ""}">Cancel</button>` : ""}
  `;
}

function renderInboxPanel(messages: InboxMessage[], unreadCount: number): SafeHtml {
  const rows = messages
    .map((msg) => {
      const labels = Array.isArray(msg.label_ids) ? msg.label_ids : [];
      const isUnread = !!msg.is_unread || labels.includes("UNREAD");
      const unreadClass = isUnread ? "unread" : "";
      return html`
        <div class="inbox-row ${unreadClass}" data-open-email="1" data-message-id="${msg.id || ""}">
          <div class="avatar mail-avatar">${initials(msg.from || "")}</div>
          <div class="mail-content">
            <div class="mail-from">${(msg.from || "Unknown sender").replace(/<.*?>/g, "").trim()}</div>
            <div class="mail-subject ${unreadClass}">${msg.subject || "(No subject)"}${msg.snippet ? html` <span>— ${msg.snippet}</span>` : ""}</div>
          </div>
          <div class="mail-date">${relDate(msg.date)}</div>
        </div>
      `;
    });

  return html`
    <section class="inbox-shell surface">
      <div class="section-head">
        <div>
          <div class="section-title inbox-title">Inbox <span>${unreadCount} unread</span></div>
          <div class="section-subtitle">Recent messages</div>
        </div>
      </div>
      <div class="inbox-list">${rows.length ? rows : html`<div class="section-subtitle">No messages</div>`}</div>
    </section>
  `;
}

function renderEventDetailPanel(detail: EventDetail | undefined, capabilities?: UiToolCapabilities): SafeHtml {
  if (!detail) return html``;
  const canEdit = capabilities?.can_edit_event ?? false;
  const canRsvp = capabilities?.can_rsvp ?? false;
  const canReschedule = capabilities?.can_reschedule_event ?? false;
  const canDelete = capabilities?.can_delete_event ?? false;
  const attendees = detail.attendees.map((attendee) => {
    const name = attendee.display_name || attendee.email;
    const role = attendee.organizer ? "Organizer" : attendee.self ? "You" : "Guest";
    return html`
      <li class="event-attendee">
        <span class="event-attendee-avatar">${initials(name)}</span>
        <span class="event-attendee-name">${name} <span class="event-attendee-status">${role}</span></span>
        ${attendee.response_status ? html`<span class="event-attendee-status">${attendee.response_status}</span>` : ""}
      </li>
    `;
  });
  const attachments = (detail.attachments || []).map((attachment) => {
    const label = attachment.mime_type ? `${attachment.title} (${attachment.mime_type})` : attachment.title;
    const fileUrl = safeExternalUrl(attachment.file_url, "attachment");
    if (fileUrl) {
      return html`
        <div class="event-attachment">
          <span class="event-attachment-label">${label}</span>
          <button type="button" class="chip-btn" data-open-attachment-url="${fileUrl}">Open</button>
          <button
            type="button"
            class="chip-btn"
            data-download-attachment-url="${fileUrl}"
            data-download-attachment-name="${attachment.title}"
            data-download-attachment-mime="${attachment.mime_type || ""}">
            Download
          </button>
        </div>
      `;
    }
    return html`<div class="event-attachment"><span class="event-attachment-label">${label}</span></div>`;
  });
  const currentResponse = detail.self_response_status || "";
  const description = detail.description?.trim() || "No description.";
  const conferenceLabel = detail.conference_provider || "Join Google Meet";
  const conferenceUrl = safeExternalUrl(detail.conference_link, "conference");
  const conference = conferenceUrl
    ? html`<a href="${conferenceUrl}" data-open-link="conference" target="_blank" rel="noopener noreferrer">${conferenceLabel}</a>`
    : html`${conferenceLabel}`;
  return html`
    <div class="overlay" role="dialog" aria-modal="true">
      <section class="panel event-panel" aria-label="Event details">
        <div class="event-toolbar">
          <button type="button" class="email-back" data-close-event="1" aria-label="Back to calendar" title="Back to calendar">←</button>
          <span>Event</span>
          <button type="button" class="nav-btn" data-close-event="1">Close</button>
        </div>
        <div class="panel-body event-panel-body">
          <div class="event-subject-line">
            <span class="event-color-dot" aria-hidden="true"></span>
            <div>
              <h2>${detail.title}</h2>
              <div class="event-when">${fmtTime(detail.start)} – ${fmtTime(detail.end)}${detail.timezone ? ` · ${detail.timezone}` : ""}</div>
            </div>
          </div>
          <div class="event-command-bar">
            ${canRsvp ? html`<button type="button" class="rsvp-chip ${currentResponse === "accepted" ? "active" : ""}" data-rsvp-status="accepted" data-calendar-id="${detail.calendar_id}" data-event-id="${detail.event_id}">Accept</button>` : ""}
            ${canRsvp ? html`<button type="button" class="rsvp-chip ${currentResponse === "tentative" ? "active" : ""}" data-rsvp-status="tentative" data-calendar-id="${detail.calendar_id}" data-event-id="${detail.event_id}">Tentative</button>` : ""}
            ${canRsvp ? html`<button type="button" class="rsvp-chip ${currentResponse === "declined" ? "active" : ""}" data-rsvp-status="declined" data-calendar-id="${detail.calendar_id}" data-event-id="${detail.event_id}">Decline</button>` : ""}
            ${canEdit ? html`<button type="button" class="chip-btn" data-open-event-editor="edit" data-calendar-id="${detail.calendar_id}" data-event-id="${detail.event_id}">Edit</button>` : ""}
            ${canReschedule ? html`<button type="button" class="chip-btn" data-reschedule-minutes="15" data-calendar-id="${detail.calendar_id}" data-event-id="${detail.event_id}" data-event-start="${detail.start}" data-event-end="${detail.end}" data-event-timezone="${detail.timezone || "UTC"}">+15m</button>` : ""}
            ${canReschedule ? html`<button type="button" class="chip-btn" data-reschedule-minutes="30" data-calendar-id="${detail.calendar_id}" data-event-id="${detail.event_id}" data-event-start="${detail.start}" data-event-end="${detail.end}" data-event-timezone="${detail.timezone || "UTC"}">+30m</button>` : ""}
            ${canReschedule ? html`<button type="button" class="chip-btn" data-reschedule-minutes="60" data-calendar-id="${detail.calendar_id}" data-event-id="${detail.event_id}" data-event-start="${detail.start}" data-event-end="${detail.end}" data-event-timezone="${detail.timezone || "UTC"}">+1h</button>` : ""}
            ${canDelete ? html`<button type="button" class="chip-btn" data-cancel-event="1" data-calendar-id="${detail.calendar_id}" data-event-id="${detail.event_id}">Cancel event</button>` : ""}
          </div>
          <div class="event-info-list">
            <div class="event-info-row"><span class="event-info-icon" aria-hidden="true">◷</span><div><span class="event-info-label">When</span>${fmtTime(detail.start)} – ${fmtTime(detail.end)}</div></div>
            ${detail.location ? html`<div class="event-info-row"><span class="event-info-icon" aria-hidden="true">⌖</span><div><span class="event-info-label">Location</span>${detail.location}</div></div>` : ""}
            ${detail.conference_link ? html`<div class="event-info-row"><span class="event-info-icon" aria-hidden="true">↗</span><div><span class="event-info-label">Video call</span>${conference}</div></div>` : ""}
            <div class="event-info-row"><span class="event-info-icon" aria-hidden="true">≡</span><div><span class="event-info-label">Description</span><div class="event-description">${description}</div></div></div>
            ${attachments.length ? html`<div class="event-info-row"><span class="event-info-icon" aria-hidden="true">⌕</span><div><span class="event-info-label">Attachments</span><div class="event-attachments">${attachments}</div></div></div>` : ""}
            <div class="event-info-row"><span class="event-info-icon" aria-hidden="true">♙</span><div><span class="event-info-label">Guests</span><ul class="event-attendee-list">${attendees.length ? attendees : html`<li>No guests.</li>`}</ul></div></div>
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
): SafeHtml {
  if (!draft) return html``;
  const calendarOptions = calendars.length
    ? calendars.map(
        (item) =>
          html`<option value="${item.id}" ${item.id === draft.calendar_id ? "selected" : ""}>${item.summary}</option>`
      )
    : html`<option value="${draft.calendar_id}">${draft.calendar_id}</option>`;
  const title = draft.mode === "create" ? "Create event" : "Edit event";

  return html`
    <div class="overlay" role="dialog" aria-modal="true">
      <section class="panel">
        <div class="panel-head">
          <div>
            <div class="panel-title">${title}</div>
            <div class="panel-sub">Self-service calendar action</div>
          </div>
          <button type="button" class="nav-btn" data-close-event-editor="1">Close</button>
        </div>
        <div class="panel-body">
          <form class="event-editor-form" data-event-editor-form="1">
            <input type="hidden" name="mode" value="${draft.mode}" />
            <input type="hidden" name="event_id" value="${draft.event_id || ""}" />
            <div class="editor-row">
              <label class="editor-field">
                <span>Calendar</span>
                <select name="calendar_id">
                  ${calendarOptions}
                </select>
              </label>
              <label class="editor-field">
                <span>Timezone</span>
                <input name="timezone" value="${draft.timezone || fallbackTimezone}" />
              </label>
            </div>
            <label class="editor-field">
              <span>Title</span>
              <input name="summary" value="${draft.summary}" required />
            </label>
            <div class="editor-row">
              <label class="editor-field">
                <span>Start</span>
                <input type="datetime-local" name="start_local" value="${draft.start_local}" required />
              </label>
              <label class="editor-field">
                <span>End</span>
                <input type="datetime-local" name="end_local" value="${draft.end_local}" required />
              </label>
            </div>
            <label class="editor-field">
              <span>Location</span>
              <input name="location" value="${draft.location || ""}" />
            </label>
            <label class="editor-field">
              <span>Attendees (comma-separated emails)</span>
              <input name="attendees_csv" value="${draft.attendees_csv || ""}" />
            </label>
            <label class="editor-field">
              <span>Description</span>
              <textarea name="description">${draft.description || ""}</textarea>
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

function renderEmailDetailPanel(detail: EmailDetail | undefined, capabilities?: UiToolCapabilities): SafeHtml {
  if (!detail) return html``;
  const bodyHtml = renderEmailBody(detail);
  const bodyMode = detail.html_body?.trim()
    ? "HTML"
    : detail.text_body?.trim()
      ? "Plain text"
      : "Snippet";
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
    isUnread ? html`<span class="status-chip">Unread</span>` : html`<span class="status-chip">Read</span>`,
    inInbox ? html`<span class="status-chip">Inbox</span>` : "",
    inTrash ? html`<span class="status-chip">Trash</span>` : "",
    inSpam ? html`<span class="status-chip">Spam</span>` : "",
  ];
  const sender = detail.from_value || "Unknown sender";
  const senderInitials = initials(sender);
  const attachments = detail.attachments.map((attachment) => {
    const label = attachment.mime_type ? `${attachment.filename} (${attachment.mime_type})` : attachment.filename;
    return html`
      <li>
        <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
          <span>${label}</span>
          <button
            type="button"
            class="email-chip"
            data-email-attachment-download="1"
            data-message-id="${detail.message_id}"
            data-attachment-id="${attachment.attachment_id}"
            data-filename="${attachment.filename}"
            data-mime-type="${attachment.mime_type || ""}">
            Download
          </button>
        </div>
      </li>
    `;
  });
  return html`
    <div class="overlay" role="dialog" aria-modal="true">
      <section class="panel email-panel">
        <div class="email-toolbar">
          <button type="button" class="email-back" data-close-email="1" aria-label="Back to inbox">←</button>
          <span>Message</span>
          <button type="button" class="nav-btn" data-close-email="1">Close</button>
        </div>
        <div class="panel-body email-panel-body">
          <div class="email-subject-line">
            <h2>${detail.subject || "(No subject)"}</h2>
            <div class="email-statuses">${statusChips}</div>
          </div>
          <div class="email-sender-row">
            <div class="email-sender-avatar">${senderInitials}</div>
            <div class="email-sender-identities">
              <div><strong>${sender}</strong> <span>to ${detail.to || "me"}</span></div>
              ${detail.cc ? html`<div class="email-recipient-extra">Cc ${detail.cc}</div>` : ""}
            </div>
            <time>${detail.date || ""}</time>
          </div>
          ${detail.attachments.length ? html`<div class="email-attachments"><strong>Attachments</strong><ul class="attachment-list">${attachments}</ul></div>` : ""}
          <div class="detail-block email-body-block gmail-message-surface">
            <div class="email-body-header">
              <strong>Message</strong>
              <span class="email-body-mode">${bodyMode}${bodyMode === "HTML" ? " · sanitized" : ""}</span>
            </div>
            <div class="email-body-content">${bodyHtml}</div>
          </div>
          <div class="email-footer-actions">
            <div class="email-actions">
              ${canRead ? html`<button type="button" class="email-chip ${!isUnread ? "active" : ""}" data-email-action="mark_read" data-message-id="${detail.message_id}">Mark read</button>` : ""}
              ${canUnread ? html`<button type="button" class="email-chip ${isUnread ? "active" : ""}" data-email-action="mark_unread" data-message-id="${detail.message_id}">Mark unread</button>` : ""}
              ${canArchive ? html`<button type="button" class="email-chip ${!inInbox ? "active" : ""}" data-email-action="archive" data-message-id="${detail.message_id}">Archive</button>` : ""}
              ${canTrash ? html`<button type="button" class="email-chip ${inTrash ? "active" : ""}" data-email-action="trash" data-message-id="${detail.message_id}">Trash</button>` : ""}
              ${canUntrash && inTrash ? html`<button type="button" class="email-chip" data-email-action="untrash" data-message-id="${detail.message_id}">Restore</button>` : ""}
              ${canSpam ? html`<button type="button" class="email-chip ${inSpam ? "active" : ""}" data-email-action="spam" data-message-id="${detail.message_id}">Spam</button>` : ""}
              ${canNotSpam && inSpam ? html`<button type="button" class="email-chip" data-email-action="not_spam" data-message-id="${detail.message_id}">Not spam</button>` : ""}
              <button type="button" class="email-chip" data-action-msg="${`Reply to ${detail.from_value} about: ${detail.subject}`}">Reply in chat</button>
            </div>
          </div>
        </div>
      </section>
    </div>
  `;
}


