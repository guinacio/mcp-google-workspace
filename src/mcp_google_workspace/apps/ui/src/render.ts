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

function sanitizeUrl(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const trimmed = raw.trim();
  if (!trimmed) return null;
  if (trimmed.startsWith("#")) return trimmed;
  if (trimmed.startsWith("mailto:") || trimmed.startsWith("tel:")) {
    return trimmed;
  }
  if (/^https?:\/\//i.test(trimmed)) {
    return trimmed;
  }
  if (trimmed.startsWith("//")) {
    return `https:${trimmed}`;
  }
  return null;
}

function sanitizeCssValue(value: string): string | null {
  const normalized = value.trim();
  if (!normalized) return null;
  const lowered = normalized.toLowerCase();
  if (
    lowered.includes("url(") ||
    lowered.includes("expression(") ||
    lowered.includes("@import") ||
    lowered.includes("javascript:")
  ) {
    return null;
  }
  return normalized;
}

function sanitizeInlineStyle(styleText: string | null | undefined): string {
  if (!styleText) return "";
  const probe = document.createElement("div");
  probe.setAttribute("style", styleText);
  // Sender-defined foreground and background colors can be unreadable in the
  // host theme, so message text always inherits the app's contrast-safe palette.
  const allowed = [
    "borderBottomColor",
    "borderBottomStyle",
    "borderBottomWidth",
    "borderCollapse",
    "borderColor",
    "borderLeftColor",
    "borderLeftStyle",
    "borderLeftWidth",
    "borderRadius",
    "borderRightColor",
    "borderRightStyle",
    "borderRightWidth",
    "borderSpacing",
    "borderTopColor",
    "borderTopStyle",
    "borderTopWidth",
    "borderWidth",
    "fontFamily",
    "fontSize",
    "fontStyle",
    "fontWeight",
    "height",
    "lineHeight",
    "margin",
    "marginBottom",
    "marginLeft",
    "marginRight",
    "marginTop",
    "maxWidth",
    "minWidth",
    "padding",
    "paddingBottom",
    "paddingLeft",
    "paddingRight",
    "paddingTop",
    "textAlign",
    "textDecoration",
    "verticalAlign",
    "whiteSpace",
    "width",
  ] as const;
  const declarations: string[] = [];
  for (const property of allowed) {
    const value = probe.style[property];
    const safeValue = sanitizeCssValue(value);
    if (!safeValue) continue;
    const cssProperty = property.replace(/[A-Z]/g, (match) => `-${match.toLowerCase()}`);
    declarations.push(`${cssProperty}:${safeValue}`);
  }
  return declarations.join("; ");
}

function renderPlainTextEmailBody(text: string): string {
  const normalized = text.replace(/\r\n?/g, "\n").trim();
  if (!normalized) {
    return `<p class="email-body-empty">No body content.</p>`;
  }
  const paragraphs = normalized.split(/\n{2,}/).filter(Boolean);
  return paragraphs
    .map((paragraph) => {
      const lines = paragraph.split("\n");
      if (lines.every((line) => line.trim().startsWith(">"))) {
        const quoted = lines.map((line) => line.replace(/^\s*> ?/, "")).join("\n");
        return `<blockquote>${esc(quoted).replace(/\n/g, "<br />")}</blockquote>`;
      }
      return `<p>${esc(lines.join("\n")).replace(/\n/g, "<br />")}</p>`;
    })
    .join("");
}

function sanitizeEmailHtml(html: string): string {
  const parser = new DOMParser();
  const parsed = parser.parseFromString(html, "text/html");
  const blockedTags = new Set([
    "script",
    "style",
    "iframe",
    "object",
    "embed",
    "form",
    "input",
    "button",
    "select",
    "textarea",
    "canvas",
    "svg",
    "math",
    "meta",
    "link",
    "base",
  ]);
  const allowedTags = new Set([
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "code",
    "del",
    "div",
    "em",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
  ]);

  const renderNode = (node: Node): string => {
    if (node.nodeType === Node.TEXT_NODE) {
      return esc(node.textContent || "");
    }
    if (node.nodeType !== Node.ELEMENT_NODE) {
      return "";
    }

    const element = node as HTMLElement;
    const tag = element.tagName.toLowerCase();
    if (blockedTags.has(tag)) {
      return "";
    }
    if (!allowedTags.has(tag)) {
      return Array.from(element.childNodes).map(renderNode).join("");
    }

    if (tag === "img") {
      const src = element.getAttribute("src")?.trim() || "";
      if (src.toLowerCase().startsWith("data:image/")) {
        const alt = esc(element.getAttribute("alt") || "");
        const title = esc(element.getAttribute("title") || "");
        return `<img class="email-html-image" src="${esc(src)}" alt="${alt}"${title ? ` title="${title}"` : ""} />`;
      }
      const altText = esc(element.getAttribute("alt") || element.getAttribute("title") || "Remote image blocked");
      return `<div class="email-image-blocked">${altText}</div>`;
    }

    const attrs: string[] = [];
    const safeStyle = sanitizeInlineStyle(element.getAttribute("style"));
    if (safeStyle) {
      attrs.push(`style="${esc(safeStyle)}"`);
    }

    if (tag === "a") {
      const href = sanitizeUrl(element.getAttribute("href"));
      if (href) {
        attrs.push(`href="${esc(href)}"`);
        attrs.push(`data-open-link="1"`);
        attrs.push(`data-link-url="${esc(href)}"`);
        attrs.push(`rel="noopener noreferrer nofollow"`);
        attrs.push(`target="_blank"`);
      }
    }

    if (["td", "th"].includes(tag)) {
      const colspan = element.getAttribute("colspan");
      const rowspan = element.getAttribute("rowspan");
      if (colspan && /^\d+$/.test(colspan)) attrs.push(`colspan="${colspan}"`);
      if (rowspan && /^\d+$/.test(rowspan)) attrs.push(`rowspan="${rowspan}"`);
    }

    const content = Array.from(element.childNodes).map(renderNode).join("");
    if (tag === "br" || tag === "hr") {
      return `<${tag}${attrs.length ? ` ${attrs.join(" ")}` : ""} />`;
    }
    return `<${tag}${attrs.length ? ` ${attrs.join(" ")}` : ""}>${content}</${tag}>`;
  };

  const htmlContent = Array.from(parsed.body.childNodes).map(renderNode).join("").trim();
  return htmlContent || `<p class="email-body-empty">No body content.</p>`;
}

function renderEmailBody(detail: EmailDetail): string {
  if (detail.html_body?.trim()) {
    return `<div class="email-html">${sanitizeEmailHtml(detail.html_body)}</div>`;
  }
  if (detail.text_body?.trim()) {
    return `<div class="email-plain">${renderPlainTextEmailBody(detail.text_body)}</div>`;
  }
  if (detail.snippet?.trim()) {
    return `<div class="email-plain">${renderPlainTextEmailBody(detail.snippet)}</div>`;
  }
  return `<div class="email-plain"><p class="email-body-empty">No body content.</p></div>`;
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
  overflow: hidden;
  background: var(--md-sys-color-surface);
}

.email-panel-body {
  gap: 14px;
  padding: 22px clamp(18px, 4vw, 42px) 26px;
  margin-top: 0;
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
  font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
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
  width: 100%;
  border-collapse: collapse;
  display: block;
  overflow-x: auto;
}

.email-body-content th,
.email-body-content td {
  border: 1px solid var(--md-sys-color-outline-variant);
  padding: 0.5rem 0.65rem;
  vertical-align: top;
}

.email-body-content th {
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

    const openBodyLink = target.closest<HTMLAnchorElement>("[data-open-link]");
    if (openBodyLink) {
      event.preventDefault();
      const url = openBodyLink.dataset.linkUrl || openBodyLink.getAttribute("href");
      if (url) {
        _onAction({ type: "open_attachment", url });
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

function renderTopBar(eventsCount: number, unreadCount?: number): string {
  const unreadChip = unreadCount !== undefined ? `<span class="stat-chip stat-chip-mail"><span class="stat-dot"></span>${unreadCount} unread</span>` : "";
  return `
    <div class="top-bar surface">
      <div class="top-brand">
        <div class="workspace-mark" aria-hidden="true">W</div>
        <div>
          <div class="top-eyebrow">Google Workspace</div>
          <h1>${esc(getGreeting())}</h1>
          <div class="top-sub">${esc(fmtTopDate())}</div>
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
): string {
  if (!weekly) {
    return `<section class="calendar-shell surface"><div class="section-subtitle">Calendar data unavailable.</div></section>`;
  }
  const weekRange = fmtWeekRange(weekly.week_start, weekly.week_end);
  const canCreate = options.tool_capabilities?.can_create_event ?? false;
  const canToggleWeekend = options.tool_capabilities?.can_toggle_weekend ?? false;
  const canSelectCalendars = options.tool_capabilities?.can_select_calendars ?? false;
  return `
    <section class="calendar-shell surface">
      <div class="calendar-header">
        <div class="calendar-heading">
          <div class="calendar-kicker">Google Calendar</div>
          <div class="calendar-title-line">
            <h2>Week of ${esc(weekRange)}</h2>
            <span class="calendar-count">${weekly.total_events} event${weekly.total_events === 1 ? "" : "s"}</span>
          </div>
          <div class="section-subtitle">${esc(weekly.timezone)}</div>
        </div>
        <div class="calendar-actions">
          <div class="week-nav">
            <button type="button" class="nav-btn nav-icon" data-week-nav="prev" aria-label="Previous week" title="Previous week">‹</button>
            <button type="button" class="nav-btn nav-today" data-week-nav="today">Today</button>
            <button type="button" class="nav-btn nav-icon" data-week-nav="next" aria-label="Next week" title="Next week">›</button>
          </div>
          ${canToggleWeekend ? `
            <label class="inline-toggle">
              <input type="checkbox" data-toggle-weekend="1" ${options.include_weekend ? "checked" : ""} />
              Show weekend
            </label>
          ` : ""}
          ${canSelectCalendars ? renderCalendarSelector(options.calendar_catalog, options.selected_calendar_ids) : ""}
          ${canCreate ? `<button type="button" class="action-btn create-event-btn" data-open-event-editor="create"><span aria-hidden="true">＋</span> Create</button>` : ""}
        </div>
      </div>
      <div class="week-grid" style="--day-count:${weekly.days.length}">${weekly.days.map((day) => renderDay(day, weekly.timezone, canCreate, options.tool_capabilities)).join("")}</div>
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
  const empty = !timed && !allDay ? `<div class="day-empty">No events</div>` : "";
  return `
    <div class="day-col ${day.is_today ? "today" : ""}">
      <div class="day-head">
        <div class="day-label">
          <span class="day-label-name">${esc(day.day_label)}</span>
          <span class="day-label-number">${esc(dayNumber(day.date))}</span>
        </div>
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
          <div class="avatar mail-avatar">${esc(initials(msg.from || ""))}</div>
          <div class="mail-content">
            <div class="mail-from">${esc((msg.from || "Unknown sender").replace(/<.*?>/g, "").trim())}</div>
            <div class="mail-subject ${unreadClass}">${esc(msg.subject || "(No subject)")}${msg.snippet ? ` <span>— ${esc(msg.snippet)}</span>` : ""}</div>
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
          <div class="section-title inbox-title">Inbox <span>${unreadCount} unread</span></div>
          <div class="section-subtitle">Recent messages</div>
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
    isUnread ? `<span class="status-chip">Unread</span>` : `<span class="status-chip">Read</span>`,
    inInbox ? `<span class="status-chip">Inbox</span>` : "",
    inTrash ? `<span class="status-chip">Trash</span>` : "",
    inSpam ? `<span class="status-chip">Spam</span>` : "",
  ].filter(Boolean).join("");
  const sender = detail.from_value || "Unknown sender";
  const senderInitials = initials(sender);
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
      <section class="panel email-panel">
        <div class="email-toolbar">
          <button type="button" class="email-back" data-close-email="1" aria-label="Back to inbox">←</button>
          <span>Message</span>
          <button type="button" class="nav-btn" data-close-email="1">Close</button>
        </div>
        <div class="panel-body email-panel-body">
          <div class="email-subject-line">
            <h2>${esc(detail.subject || "(No subject)")}</h2>
            <div class="email-statuses">${statusChips}</div>
          </div>
          <div class="email-sender-row">
            <div class="email-sender-avatar">${esc(senderInitials)}</div>
            <div class="email-sender-identities">
              <div><strong>${esc(sender)}</strong> <span>to ${esc(detail.to || "me")}</span></div>
              ${detail.cc ? `<div class="email-recipient-extra">Cc ${esc(detail.cc)}</div>` : ""}
            </div>
            <time>${esc(detail.date || "")}</time>
          </div>
          ${detail.attachments.length ? `<div class="email-attachments"><strong>Attachments</strong><ul class="attachment-list">${attachments}</ul></div>` : ""}
          <div class="detail-block email-body-block gmail-message-surface">
            <div class="email-body-header">
              <strong>Message</strong>
              <span class="email-body-mode">${bodyMode}${bodyMode === "HTML" ? " · sanitized" : ""}</span>
            </div>
            <div class="email-body-content">${bodyHtml}</div>
          </div>
          <div class="email-footer-actions">
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


