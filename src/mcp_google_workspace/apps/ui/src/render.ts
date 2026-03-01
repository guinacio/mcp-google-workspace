import type {
  DashboardData,
  WeeklyCalendar,
  DashboardViewModel,
  WeeklyCalendarDay,
  WeeklyCalendarEvent,
  EventDetail,
  EmailDetail,
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
  | { type: "select_event"; calendarId: string; eventId: string }
  | { type: "close_event_detail" }
  | { type: "select_email"; messageId: string }
  | { type: "close_email_detail" }
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

export function setActionHandler(handler: ActionHandler) {
  _onAction = handler;
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
}

.day-col {
  background: var(--md-sys-color-surface-container-high);
  border: 1px solid var(--md-sys-color-outline-variant);
  border-radius: var(--radius-sm);
  padding: 8px;
  min-height: 540px;
  overflow: visible;
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
  transition: background 0.15s;
}

.calendar-event:hover {
  background: color-mix(in srgb, var(--event-color) 20%, var(--md-sys-color-surface-container));
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
  display: none;
  position: absolute;
  left: -4px;
  right: -4px;
  bottom: calc(100% + 4px);
  border-radius: var(--radius-sm);
  border: 1px solid var(--md-sys-color-outline-variant);
  background: var(--md-sys-color-surface-container-high);
  box-shadow: var(--md-sys-elevation-2);
  padding: 8px 10px;
  z-index: 20;
  font-size: 0.74rem;
  line-height: 1.45;
  color: var(--md-sys-color-on-surface);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 200px;
  overflow-y: auto;
  pointer-events: none;
}

.calendar-event:hover .event-hover {
  display: block;
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
}
`;

export function renderLoading(root: HTMLElement) {
  root.innerHTML = `<div class="loading-state">Loading workspace dashboard...</div>`;
}

type InboxMessage = { id?: string; subject?: string; from?: string; date?: string; snippet?: string };

function getInboxData(dashboard?: DashboardViewModel): { unreadCount: number; messages: InboxMessage[] } {
  if (!dashboard) return { unreadCount: 0, messages: [] };
  const section = dashboard.sections.find((item) => item.id === "communications");
  const inbox = section?.cards.find((card) => card.card_type === "inbox");
  const data = (inbox?.data ?? {}) as { unread_count?: number; messages?: InboxMessage[] };
  return {
    unreadCount: data.unread_count ?? 0,
    messages: data.messages ?? [],
  };
}

function countWeekEvents(weekly?: WeeklyCalendar): number {
  if (!weekly) return 0;
  return weekly.days.reduce((acc, day) => acc + day.all_day_events.length + day.timed_events.length, 0);
}

export function renderDashboard(root: HTMLElement, data: DashboardData) {
  const hasDashboard = !!data.dashboard;
  const inboxData = getInboxData(data.dashboard);
  const eventsCount = countWeekEvents(data.weekly_calendar);

  root.innerHTML = `
    <div class="dashboard">
      ${renderTopBar(eventsCount, hasDashboard ? inboxData.unreadCount : undefined)}
      <div class="main-grid${hasDashboard ? "" : " main-grid-full"}">
        ${renderCalendarArea(data.weekly_calendar)}
        ${hasDashboard ? `<div class="sidebar">${renderInboxPanel(inboxData.messages, inboxData.unreadCount)}</div>` : ""}
      </div>
      ${renderEventDetailPanel(data.event_detail)}
      ${renderEmailDetailPanel(data.email_detail)}
    </div>
  `;

  root.onclick = (event) => {
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

    if (target.closest("[data-close-event]")) {
      _onAction({ type: "close_event_detail" });
      return;
    }

    if (target.closest("[data-close-email]")) {
      _onAction({ type: "close_email_detail" });
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

function renderCalendarArea(weekly?: WeeklyCalendar): string {
  if (!weekly) {
    return `<section class="calendar-shell surface"><div class="section-subtitle">Calendar data unavailable.</div></section>`;
  }
  const weekRange = `${weekly.week_start} - ${weekly.week_end}`;
  return `
    <section class="calendar-shell surface">
      <div class="section-head">
        <div>
          <div class="section-title">Week View</div>
          <div class="section-subtitle">${esc(weekRange)} · ${esc(weekly.timezone)}</div>
        </div>
        <div class="week-nav">
          <button type="button" class="nav-btn" data-week-nav="prev">Prev</button>
          <button type="button" class="nav-btn" data-week-nav="today">Today</button>
          <button type="button" class="nav-btn" data-week-nav="next">Next</button>
        </div>
      </div>
      <div class="week-grid">${weekly.days.map((day) => renderDay(day, weekly.timezone)).join("")}</div>
    </section>
  `;
}

function renderDay(day: WeeklyCalendarDay, timezone: string): string {
  const timed = day.timed_events.map((event) => renderEvent(event, timezone)).join("");
  const allDay = day.all_day_events
    .map((event) => `<div class="all-day-chip" title="${esc(event.title)}">${esc(event.title)}</div>`)
    .join("");
  const empty = !timed && !allDay ? `<div class="section-subtitle">No events</div>` : "";
  return `
    <div class="day-col ${day.is_today ? "today" : ""}">
      <div class="day-head"><span>${esc(day.day_label)}</span><span>${esc(fmtDayDate(day.date))}</span></div>
      ${allDay ? `<div class="day-all-day">${allDay}</div>` : ""}
      <div class="day-events">${timed || empty}</div>
    </div>
  `;
}

function renderEvent(event: WeeklyCalendarEvent, timezone: string): string {
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
      <div class="event-actions">${renderEventActionChips(event, timezone)}</div>
      <div class="event-hover">${hoverLines.join("<br>")}</div>
    </article>
  `;
}

function renderEventActionChips(event: WeeklyCalendarEvent, timezone: string): string {
  if (!event.event_id || !event.calendar_id) return "";
  const current = event.attendee_response_status || "";
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
    ${chip("accepted", "Yes")}
    ${chip("tentative", "Maybe")}
    ${chip("declined", "No")}
    <button type="button" class="chip-btn" data-reschedule-minutes="15" data-calendar-id="${esc(event.calendar_id || "")}" data-event-id="${esc(event.event_id || "")}" data-event-start="${esc(event.start)}" data-event-end="${esc(event.end)}" data-event-timezone="${esc(timezone)}">+15m</button>
    <button type="button" class="chip-btn" data-reschedule-minutes="30" data-calendar-id="${esc(event.calendar_id || "")}" data-event-id="${esc(event.event_id || "")}" data-event-start="${esc(event.start)}" data-event-end="${esc(event.end)}" data-event-timezone="${esc(timezone)}">+30m</button>
    <button type="button" class="chip-btn" data-reschedule-minutes="60" data-calendar-id="${esc(event.calendar_id || "")}" data-event-id="${esc(event.event_id || "")}" data-event-start="${esc(event.start)}" data-event-end="${esc(event.end)}" data-event-timezone="${esc(timezone)}">+1h</button>
    <button type="button" class="chip-btn" data-cancel-event="1" data-calendar-id="${esc(event.calendar_id || "")}" data-event-id="${esc(event.event_id || "")}">Cancel</button>
  `;
}

function renderInboxPanel(messages: InboxMessage[], unreadCount: number): string {
  const rows = messages
    .map((msg) => {
      const unreadClass = unreadCount > 0 ? "unread" : "";
      return `
        <div class="inbox-row" data-open-email="1" data-message-id="${esc(msg.id || "")}">
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

function renderEventDetailPanel(detail?: EventDetail): string {
  if (!detail) return "";
  const attendees = detail.attendees
    .map(
      (attendee) =>
        `<li>${esc(attendee.display_name || attendee.email)}${attendee.response_status ? ` · ${esc(attendee.response_status)}` : ""}</li>`
    )
    .join("");
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
          <div class="detail-block"><strong>Description</strong><div style="margin-top:6px; white-space:pre-wrap;">${esc(description)}</div></div>
          <div class="detail-block"><strong>Attendees</strong><ul class="attendee-list">${attendees || "<li>No attendees.</li>"}</ul></div>
          <div class="detail-block">
            <div class="event-actions">
              <button type="button" class="rsvp-chip" data-rsvp-status="accepted" data-calendar-id="${esc(detail.calendar_id)}" data-event-id="${esc(detail.event_id)}">Accept</button>
              <button type="button" class="rsvp-chip" data-rsvp-status="tentative" data-calendar-id="${esc(detail.calendar_id)}" data-event-id="${esc(detail.event_id)}">Tentative</button>
              <button type="button" class="rsvp-chip" data-rsvp-status="declined" data-calendar-id="${esc(detail.calendar_id)}" data-event-id="${esc(detail.event_id)}">Decline</button>
              <button type="button" class="chip-btn" data-reschedule-minutes="15" data-calendar-id="${esc(detail.calendar_id)}" data-event-id="${esc(detail.event_id)}" data-event-start="${esc(detail.start)}" data-event-end="${esc(detail.end)}" data-event-timezone="${esc(detail.timezone || "UTC")}">+15m</button>
              <button type="button" class="chip-btn" data-reschedule-minutes="30" data-calendar-id="${esc(detail.calendar_id)}" data-event-id="${esc(detail.event_id)}" data-event-start="${esc(detail.start)}" data-event-end="${esc(detail.end)}" data-event-timezone="${esc(detail.timezone || "UTC")}">+30m</button>
              <button type="button" class="chip-btn" data-reschedule-minutes="60" data-calendar-id="${esc(detail.calendar_id)}" data-event-id="${esc(detail.event_id)}" data-event-start="${esc(detail.start)}" data-event-end="${esc(detail.end)}" data-event-timezone="${esc(detail.timezone || "UTC")}">+1h</button>
              <button type="button" class="chip-btn" data-cancel-event="1" data-calendar-id="${esc(detail.calendar_id)}" data-event-id="${esc(detail.event_id)}">Cancel event</button>
            </div>
          </div>
        </div>
      </section>
    </div>
  `;
}

function renderEmailDetailPanel(detail?: EmailDetail): string {
  if (!detail) return "";
  const body = detail.text_body || detail.html_body || detail.snippet || "No body content.";
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
          <div class="detail-block"><strong>Body</strong><div style="margin-top:6px; white-space:pre-wrap;">${esc(body)}</div></div>
          <div class="detail-block" style="display:flex; gap:8px; flex-wrap:wrap;">
            <button type="button" class="action-btn" data-action-msg="${esc(`Reply to ${detail.from_value} about: ${detail.subject}`)}">Reply in chat</button>
            <button type="button" class="action-btn" data-action-msg="${esc(`Archive email ${detail.message_id}`)}">Archive</button>
            <button type="button" class="action-btn" data-action-msg="${esc(`Mark email ${detail.message_id} as read`)}">Mark as read</button>
          </div>
        </div>
      </section>
    </div>
  `;
}
