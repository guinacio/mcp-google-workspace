// Development-only standalone preview host. It embeds the dashboard next to an unrelated
// sibling frame so tests can prove that only the embedding parent is trusted.
const params = new URLSearchParams(location.search);
const received: Array<{ origin: string; fromDashboard: boolean; data: unknown }> = [];

const weekly = (title: string) => ({
  week_start: "2026-09-21",
  week_end: "2026-09-27",
  timezone: "UTC",
  total_events: 1,
  days: [{
    date: "2026-09-21",
    day_label: "Mon",
    is_today: false,
    all_day_events: [],
    timed_events: [{
      event_id: "standalone-event",
      calendar_id: "primary",
      title,
      start: "2026-09-21T10:00:00Z",
      end: "2026-09-21T11:00:00Z",
      all_day: false,
      status: "confirmed",
    }],
  }],
  fallback_text: title,
});

const dashboard = document.createElement("iframe");
dashboard.name = "dashboard";
dashboard.style.cssText = "width: 1200px; height: 700px; border: 0";
const sibling = document.createElement("iframe");
sibling.name = "sibling";
document.body.append(dashboard, sibling);

window.addEventListener("message", (event) => {
  received.push({
    origin: event.origin,
    fromDashboard: event.source === dashboard.contentWindow,
    data: event.data,
  });
});

function sendFromParent(title: string) {
  dashboard.contentWindow!.postMessage(
    { type: "dashboard_data", data: { weekly_calendar: weekly(title) } },
    location.origin,
  );
}

function sendFromSibling(title: string) {
  const script = sibling.contentDocument!.createElement("script");
  script.textContent = `parent.frames["dashboard"].postMessage(${JSON.stringify({
    type: "dashboard_data",
    data: { weekly_calendar: weekly(title) },
  })}, "*");`;
  sibling.contentDocument!.body.append(script);
}

Object.assign(window, { received, sendFromParent, sendFromSibling });
dashboard.src = params.get("target") === "dist"
  ? "/dist/index.html?mode=standalone"
  : "/index.html?mode=standalone";
