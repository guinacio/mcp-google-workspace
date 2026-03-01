/** Matches MorningBriefingViewModel from Python schemas */
export interface BriefingPriority {
  title: string;
  reason: string;
  priority: "high" | "medium" | "low";
}

export interface BriefingRisk {
  title: string;
  detail: string;
  severity: "high" | "medium" | "low";
}

export interface BriefingAction {
  title: string;
  detail: string;
  tool_name: string;
  payload: Record<string, unknown>;
}

export interface MorningBriefing {
  date: string;
  timezone: string;
  summary: string;
  priorities: BriefingPriority[];
  conflicts: BriefingRisk[];
  prep_actions: BriefingAction[];
  quick_wins: BriefingAction[];
  fallback_text: string;
}

/** Matches WeeklyCalendarViewModel from Python schemas */
export interface WeeklyCalendarEvent {
  event_id: string | null;
  calendar_id: string | null;
  title: string;
  start: string;
  end: string;
  all_day: boolean;
  status: string;
}

export interface WeeklyCalendarDay {
  date: string;
  day_label: string;
  is_today: boolean;
  all_day_events: WeeklyCalendarEvent[];
  timed_events: WeeklyCalendarEvent[];
}

export interface WeeklyCalendar {
  week_start: string;
  week_end: string;
  timezone: string;
  total_events: number;
  days: WeeklyCalendarDay[];
  fallback_text: string;
}

/** Matches DashboardViewModel from Python schemas */
export interface DashboardCard {
  id: string;
  title: string;
  card_type: "calendar" | "inbox" | "prep" | "briefing" | "meta" | "error";
  summary: string;
  fallback_text: string;
  data: Record<string, unknown>;
  actions: { id: string; label: string; tool_name: string; payload: Record<string, unknown> }[];
}

export interface DashboardSection {
  id: string;
  title: string;
  cards: DashboardCard[];
  fallback_text: string;
}

export interface DashboardViewModel {
  title: string;
  generated_at_utc: string;
  state: Record<string, unknown>;
  sections: DashboardSection[];
  warnings: string[];
  section_errors: Record<string, string>;
}

/** Combined payload that Sentinel sends to the iframe */
export interface DashboardData {
  briefing?: MorningBriefing;
  weekly_calendar?: WeeklyCalendar;
  dashboard?: DashboardViewModel;
  generated_at?: string;
}

/** PostMessage types from parent to iframe */
export type ParentMessage =
  | { type: "dashboard_data"; data: DashboardData }
  | { type: "theme_changed"; theme: "dark" | "light" };

/** PostMessage types from iframe to parent */
export type IframeMessage =
  | { type: "inject_chat_message"; text: string }
  | { type: "request_dashboard_data" };
