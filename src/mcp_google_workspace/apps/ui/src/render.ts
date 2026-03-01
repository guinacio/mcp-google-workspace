import type {
  DashboardData,
  MorningBriefing,
  WeeklyCalendar,
  DashboardViewModel,
  BriefingPriority,
  BriefingRisk,
  BriefingAction,
  WeeklyCalendarDay,
} from "./types";

// ── Helpers ─────────────────────────────────────────────────────────────────

function esc(text: string): string {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function fmtTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch {
    return "";
  }
}

function fmtDate(iso: string): number | string {
  try {
    return new Date(iso + "T00:00:00").getDate();
  } catch {
    return iso;
  }
}

function fmtGreetingDate(): string {
  return new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
}

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

type ActionHandler = (text: string) => void;
let _onAction: ActionHandler = () => {};

export function setActionHandler(handler: ActionHandler) {
  _onAction = handler;
}

// ── Render CSS ──────────────────────────────────────────────────────────────

export const RENDER_CSS = `
/* ── Layout ─────────────────────────────────────────────────────────────── */
.dashboard {
  max-width: 720px;
  margin: 0 auto;
  padding: 32px 24px 48px;
  display: flex;
  flex-direction: column;
  gap: 32px;
}

/* ── Fade-in animation ──────────────────────────────────────────────────── */
.section {
  opacity: 0;
  transform: translateY(12px);
  animation: sectionIn 0.4s ease forwards;
}

@keyframes sectionIn {
  to { opacity: 1; transform: translateY(0); }
}

/* ── Hero ────────────────────────────────────────────────────────────────── */
.hero {
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border-subtle);
}

.hero-greeting {
  font-family: 'Cormorant Garamond', Georgia, serif;
  font-size: 36px;
  font-weight: 400;
  line-height: 1.2;
  color: var(--fg-primary);
  letter-spacing: -0.01em;
}

.hero-date {
  font-size: 13px;
  color: var(--fg-muted);
  margin-top: 6px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.hero-summary {
  font-size: 14px;
  color: var(--fg-secondary);
  margin-top: 12px;
  line-height: 1.6;
}

/* ── Section titles ─────────────────────────────────────────────────────── */
.section-title {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fg-muted);
  margin-bottom: 10px;
}

/* ── Priority cards ─────────────────────────────────────────────────────── */
.priorities { display: flex; flex-direction: column; gap: 6px; }

.priority-card {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  background: var(--bg-card);
  box-shadow: var(--shadow-card);
}

.priority-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 5px;
}

.priority-dot.high   { background: var(--accent-red); }
.priority-dot.medium { background: var(--accent-amber); }
.priority-dot.low    { background: var(--accent-green); }

.priority-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--fg-primary);
}

.priority-reason {
  font-size: 11px;
  color: var(--fg-muted);
  line-height: 1.4;
  margin-top: 2px;
}

/* ── Conflicts ──────────────────────────────────────────────────────────── */
.conflicts { display: flex; flex-direction: column; gap: 6px; }

.conflict-bar {
  padding: 10px 14px;
  border-left: 3px solid var(--accent-amber);
  background: var(--bg-card);
  border-radius: 0 var(--radius-md) var(--radius-md) 0;
  box-shadow: var(--shadow-card);
}

.conflict-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--fg-primary);
}

.conflict-detail {
  font-size: 11px;
  color: var(--fg-secondary);
  margin-top: 2px;
}

/* ── Action pills ───────────────────────────────────────────────────────── */
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.action-pill {
  padding: 5px 14px;
  border-radius: 999px;
  border: 1px solid var(--border-medium);
  background: transparent;
  color: var(--fg-secondary);
  font-family: 'DM Sans', sans-serif;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
  white-space: nowrap;
}

.action-pill:hover {
  background: color-mix(in srgb, var(--accent-teal) 12%, transparent);
  color: var(--accent-teal);
  border-color: var(--accent-teal);
}

/* ── Weekly calendar ────────────────────────────────────────────────────── */
.week-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 4px;
}

@media (max-width: 600px) {
  .week-grid {
    grid-template-columns: repeat(7, min(100%, 120px));
    overflow-x: auto;
  }
}

.week-col {
  padding: 8px;
  border-radius: var(--radius-md);
  background: var(--bg-card);
  min-height: 80px;
  border: 1px solid transparent;
  transition: border-color 0.15s;
}

.week-col.today {
  border-color: var(--accent-teal);
  background: color-mix(in srgb, var(--accent-teal) 5%, var(--bg-card));
}

.week-col-header {
  font-weight: 600;
  font-size: 11px;
  color: var(--fg-muted);
  margin-bottom: 6px;
}

.week-col.today .week-col-header { color: var(--accent-teal); }

.week-col-date {
  font-size: 10px;
  opacity: 0.7;
}

.week-allday {
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
  margin-bottom: 4px;
}

.week-allday-pill {
  padding: 1px 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--accent-teal) 15%, transparent);
  color: var(--accent-teal);
  font-size: 10px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.week-event {
  display: block;
  padding: 2px 4px;
  border-radius: 3px;
  background: color-mix(in srgb, var(--accent-teal) 10%, transparent);
  color: var(--fg-secondary);
  margin-bottom: 2px;
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  border-left: 2px solid var(--accent-teal);
}

.week-event:hover {
  background: color-mix(in srgb, var(--accent-teal) 20%, transparent);
  color: var(--fg-primary);
}

.week-empty {
  font-size: 10px;
  color: var(--fg-muted);
  opacity: 0.4;
  font-style: italic;
  padding-top: 4px;
}

/* ── Inbox card ─────────────────────────────────────────────────────────── */
.inbox-card {
  padding: 14px;
  border-radius: var(--radius-md);
  background: var(--bg-card);
  box-shadow: var(--shadow-card);
}

.inbox-badge {
  font-size: 13px;
  font-weight: 600;
  color: var(--fg-primary);
  margin-bottom: 10px;
}

.inbox-row {
  font-size: 11px;
  padding: 5px 0;
  border-top: 1px solid var(--border-subtle);
  display: flex;
  gap: 8px;
  align-items: baseline;
}

.inbox-subject {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg-secondary);
}

.inbox-from {
  color: var(--fg-muted);
  white-space: nowrap;
  flex-shrink: 0;
}

/* ── Empty/loading state ────────────────────────────────────────────────── */
.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 300px;
  gap: 16px;
  color: var(--fg-muted);
}

.loading-icon {
  opacity: 0.3;
}

.loading-text {
  font-size: 13px;
}

.loading-hint {
  font-size: 11px;
  opacity: 0.6;
}
`;

// ── Render functions ────────────────────────────────────────────────────────

export function renderLoading(root: HTMLElement) {
  root.innerHTML = `
    <div class="loading-state">
      <svg class="loading-icon" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="3" width="7" height="7" rx="1"/>
        <rect x="14" y="3" width="7" height="4" rx="1"/>
        <rect x="3" y="14" width="7" height="4" rx="1"/>
        <rect x="14" y="11" width="7" height="7" rx="1"/>
      </svg>
      <div class="loading-text">Waiting for dashboard data...</div>
      <div class="loading-hint">Data arrives from the morning briefing.</div>
    </div>
  `;
}

export function renderDashboard(root: HTMLElement, data: DashboardData) {
  const sections: string[] = [];
  let delay = 0;
  const nextDelay = () => { delay += 50; return delay + 100; };

  // Hero greeting
  const summary = data.briefing?.summary || "";
  sections.push(`
    <div class="section hero" style="animation-delay: ${nextDelay()}ms">
      <div class="hero-greeting">${getGreeting()}</div>
      <div class="hero-date">${fmtGreetingDate()}</div>
      ${summary ? `<div class="hero-summary">${esc(summary)}</div>` : ""}
    </div>
  `);

  // Priorities
  if (data.briefing?.priorities?.length) {
    sections.push(renderPriorities(data.briefing.priorities, nextDelay()));
  }

  // Conflicts
  if (data.briefing?.conflicts?.length) {
    sections.push(renderConflicts(data.briefing.conflicts, nextDelay()));
  }

  // Actions
  const actions = [
    ...(data.briefing?.prep_actions || []),
    ...(data.briefing?.quick_wins || []),
  ];
  if (actions.length) {
    sections.push(renderActions(actions, nextDelay()));
  }

  // Weekly calendar
  if (data.weekly_calendar?.days?.length) {
    sections.push(renderCalendar(data.weekly_calendar, nextDelay()));
  }

  // Inbox
  if (data.dashboard) {
    const inbox = renderInbox(data.dashboard, nextDelay());
    if (inbox) sections.push(inbox);
  }

  root.innerHTML = `<div class="dashboard">${sections.join("")}</div>`;

  // Bind action clicks via delegation
  root.addEventListener("click", (e) => {
    const target = e.target as HTMLElement;
    const pill = target.closest<HTMLElement>("[data-action-msg]");
    if (pill) {
      e.preventDefault();
      _onAction(pill.dataset.actionMsg!);
    }
    const event = target.closest<HTMLElement>("[data-event-msg]");
    if (event) {
      e.preventDefault();
      _onAction(event.dataset.eventMsg!);
    }
  });
}

function renderPriorities(priorities: BriefingPriority[], animDelay: number): string {
  const cards = priorities.map((p) => `
    <div class="priority-card">
      <span class="priority-dot ${p.priority}"></span>
      <div>
        <div class="priority-title">${esc(p.title)}</div>
        <div class="priority-reason">${esc(p.reason)}</div>
      </div>
    </div>
  `).join("");

  return `
    <div class="section" style="animation-delay: ${animDelay}ms">
      <div class="section-title">Priorities</div>
      <div class="priorities">${cards}</div>
    </div>
  `;
}

function renderConflicts(conflicts: BriefingRisk[], animDelay: number): string {
  const bars = conflicts.map((c) => `
    <div class="conflict-bar">
      <div class="conflict-title">${esc(c.title)}</div>
      ${c.detail ? `<div class="conflict-detail">${esc(c.detail)}</div>` : ""}
    </div>
  `).join("");

  return `
    <div class="section" style="animation-delay: ${animDelay}ms">
      <div class="section-title">Conflicts</div>
      <div class="conflicts">${bars}</div>
    </div>
  `;
}

function renderActions(actions: BriefingAction[], animDelay: number): string {
  const pills = actions.map((a) => {
    const msg = `Help me with: ${a.title}`;
    return `<button class="action-pill" data-action-msg="${esc(msg)}">${esc(a.title)}</button>`;
  }).join("");

  return `
    <div class="section" style="animation-delay: ${animDelay}ms">
      <div class="section-title">Actions</div>
      <div class="actions">${pills}</div>
    </div>
  `;
}

function renderCalendar(wc: WeeklyCalendar, animDelay: number): string {
  const cols = wc.days.map((day: WeeklyCalendarDay) => {
    const allDayPills = (day.all_day_events || []).map((ev) =>
      `<span class="week-allday-pill" title="${esc(ev.title)}">${esc(ev.title)}</span>`
    ).join("");
    const allDay = allDayPills ? `<div class="week-allday">${allDayPills}</div>` : "";

    const timed = (day.timed_events || []).map((ev) => {
      const time = fmtTime(ev.start);
      const msg = `Tell me about the "${ev.title}" event`;
      return `<div class="week-event" title="${esc(ev.title)}" data-event-msg="${esc(msg)}">${time} · ${esc(ev.title)}</div>`;
    }).join("");

    const empty = !allDayPills && !timed ? '<div class="week-empty">&ndash;</div>' : "";

    return `
      <div class="week-col${day.is_today ? " today" : ""}">
        <div class="week-col-header">${esc(day.day_label)} <span class="week-col-date">${fmtDate(day.date)}</span></div>
        ${allDay}${timed}${empty}
      </div>
    `;
  }).join("");

  const weekRange = `${wc.week_start || ""} \u2013 ${wc.week_end || ""}`;

  return `
    <div class="section" style="animation-delay: ${animDelay}ms">
      <div class="section-title">Week \u00b7 ${esc(weekRange)}</div>
      <div class="week-grid">${cols}</div>
    </div>
  `;
}

function renderInbox(dashboard: DashboardViewModel, animDelay: number): string | null {
  const sections = dashboard.sections || [];
  const commSection = sections.find((s) => s.id === "communications");
  if (!commSection) return null;
  const inboxCard = (commSection.cards || []).find((c) => c.card_type === "inbox");
  if (!inboxCard) return null;
  const data = inboxCard.data as { unread_count?: number; messages?: { subject?: string; from?: string }[] };
  const { unread_count = 0, messages = [] } = data;

  const rows = messages.map((m) => `
    <div class="inbox-row">
      <span class="inbox-subject">${esc(m.subject || "(no subject)")}</span>
      <span class="inbox-from">${esc((m.from || "").replace(/<.*>/, "").trim())}</span>
    </div>
  `).join("");

  return `
    <div class="section" style="animation-delay: ${animDelay}ms">
      <div class="section-title">Inbox</div>
      <div class="inbox-card">
        <div class="inbox-badge">${unread_count} unread</div>
        ${rows}
      </div>
    </div>
  `;
}
