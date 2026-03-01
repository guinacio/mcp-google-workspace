/** Matches WeeklyCalendarViewModel from Python schemas */
export interface WeeklyCalendarEvent {
  event_id: string | null;
  calendar_id: string | null;
  title: string;
  start: string;
  end: string;
  all_day: boolean;
  status: string;
  attendee_response_status?: "needsAction" | "declined" | "tentative" | "accepted" | null;
  location?: string | null;
  description_snippet?: string | null;
  attendee_count?: number | null;
  has_conference?: boolean;
  color_id?: string | null;
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

export interface EventDetailAttendee {
  email: string;
  display_name?: string | null;
  optional: boolean;
  organizer: boolean;
  self: boolean;
  response_status?: string | null;
}

export interface EventDetail {
  event_id: string;
  calendar_id: string;
  title: string;
  start: string;
  end: string;
  timezone?: string | null;
  status: string;
  location?: string | null;
  description?: string | null;
  conference_link?: string | null;
  conference_provider?: string | null;
  organizer_email?: string | null;
  organizer_name?: string | null;
  attendees: EventDetailAttendee[];
}

export interface EmailDetail {
  message_id: string;
  thread_id?: string | null;
  subject: string;
  from_value: string;
  to?: string | null;
  cc?: string | null;
  bcc?: string | null;
  date?: string | null;
  snippet?: string | null;
  text_body?: string | null;
  html_body?: string | null;
  labels: string[];
  is_unread: boolean;
}

/** Matches DashboardViewModel from Python schemas */
export interface DashboardCard {
  id: string;
  title: string;
  card_type: "calendar" | "inbox" | "prep" | "meta" | "error";
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

/** Combined payload sent to the iframe */
export interface DashboardData {
  weekly_calendar?: WeeklyCalendar;
  dashboard?: DashboardViewModel;
  event_detail?: EventDetail;
  email_detail?: EmailDetail;
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
