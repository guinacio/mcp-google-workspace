import { expect, test } from "@playwright/test";
import type { Frame, Page } from "@playwright/test";
import {
  ATTR_BREAKOUT,
  DATA_IMAGE,
  ENCODED_QUOTES,
  EVENT_TITLE,
  PAYLOAD,
  SAFE_ATTACHMENT_URL,
  SAFE_CONFERENCE_LINK,
  SAFE_EMAIL_LINK,
  UNSAFE_URLS,
} from "./fixtures";

const ADVERSARIAL_HOST = "/tests/host.html?discovery=unsupported&fixture=adversarial";
const HOSTILE_HOSTS = /tracker\.example|evil\.example/;

test.beforeEach(async ({ page }) => {
  await page.route(/https:\/\/fonts\.(googleapis|gstatic)\.com\//, (route) => route.abort());
});

function trackHostileRequests(page: Page): string[] {
  const hostile: string[] = [];
  page.on("request", (request) => {
    if (HOSTILE_HOSTS.test(request.url())) hostile.push(request.url());
  });
  void page.context().route(HOSTILE_HOSTS, (route) => {
    hostile.push(route.request().url());
    return route.abort();
  });
  return hostile;
}

async function dashboardFrame(page: Page, urlPart: string): Promise<Frame> {
  await expect.poll(() => page.frames().some((frame) => frame.url().includes(urlPart))).toBe(true);
  return page.frames().find((frame) => frame.url().includes(urlPart))!;
}

/** Structural audit of the App document: injected attributes, handlers, script execution. */
async function auditDom(frame: Frame) {
  return frame.evaluate(() => {
    const handlers: string[] = [];
    const oddAttributeNames: string[] = [];
    for (const element of Array.from(document.querySelectorAll("*"))) {
      for (const attribute of Array.from(element.attributes)) {
        if (/^on/i.test(attribute.name)) handlers.push(`${element.localName}[${attribute.name}]`);
        if (/["'<>=`\\/]/.test(attribute.name)) oddAttributeNames.push(`${element.localName}[${attribute.name}]`);
      }
    }
    const javascriptUrls = Array.from(document.querySelectorAll("[href], [src], [action], [formaction]"))
      .map((element) => element.getAttribute("href") ?? element.getAttribute("src") ?? element.getAttribute("action") ?? "")
      .filter((value) => /^\s*(javascript|vbscript|data:text)/i.test(value));
    return {
      injected: document.querySelectorAll("[data-audit-injected]").length,
      handlers,
      oddAttributeNames,
      javascriptUrls,
      pwned: (window as unknown as { __pwned?: unknown }).__pwned,
      parentPwned: (window.parent as unknown as { __pwned?: unknown }).__pwned,
    };
  });
}

async function expectCleanDom(frame: Frame) {
  expect(await auditDom(frame)).toEqual({
    injected: 0,
    handlers: [],
    oddAttributeNames: [],
    javascriptUrls: [],
    pwned: undefined,
    parentPwned: undefined,
  });
}

test("renders adversarial calendar and inbox strings as inert text", async ({ page }) => {
  const hostile = trackHostileRequests(page);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(ADVERSARIAL_HOST);
  const app = page.frameLocator("#dashboard");
  const eventCard = app.getByRole("button", { name: `Open event: ${EVENT_TITLE}`, exact: true });
  await expect(eventCard).toBeVisible();
  await expect(app.locator(".event-title", { hasText: "Fixture baseline meeting" })).toBeVisible();
  await expect(app.locator(".event-title").first()).toHaveText(EVENT_TITLE);
  await expect(eventCard).toHaveAttribute("data-event-id", `evt${ATTR_BREAKOUT}`);
  await expect(app.locator(".all-day-chip")).toHaveAttribute("title", `All day ${PAYLOAD}`);
  await expect(app.locator(".mail-subject").first()).toContainText(`Subject ${PAYLOAD}`);
  await expect(app.locator(".mail-subject").first()).toContainText(ENCODED_QUOTES.join(" "));
  await expect(app.locator(".inbox-row").first()).toHaveAttribute("data-message-id", `msg${ATTR_BREAKOUT}`);
  await expect(app.locator(".calendar-count")).toContainText("<b><img");

  await app.getByText("Calendars", { exact: true }).click();
  await expect(app.locator(".calendar-option").nth(1)).toContainText(`Shared ${PAYLOAD}`);
  await expect(app.locator(".calendar-option input").nth(1)).toHaveAttribute("data-calendar-id", `cal${ATTR_BREAKOUT}`);

  await eventCard.hover();
  await expect(app.locator(".event-tooltip-layer")).toContainText(`Snippet ${PAYLOAD}`);
  await expect(app.locator(".event-tooltip-layer img")).toHaveCount(0);

  const frame = await dashboardFrame(page, "/dist/index.html");
  await expectCleanDom(frame);
  expect(hostile).toEqual([]);
  expect(errors).toEqual([]);
});

test("event detail blocks unsafe URLs and opens safe ones through the host", async ({ page }) => {
  const hostile = trackHostileRequests(page);
  await page.goto(ADVERSARIAL_HOST);
  const app = page.frameLocator("#dashboard");
  await app.getByRole("button", { name: `Open event: ${EVENT_TITLE}`, exact: true }).click();
  const panel = app.locator(".event-panel");
  await expect(panel.getByRole("heading", { name: EVENT_TITLE })).toBeVisible();
  await expect(panel).toContainText(`Description ${PAYLOAD}`);

  // javascript: conference link is shown as text, never as a link.
  await expect(panel).toContainText(`Meet ${PAYLOAD}`);
  await expect(panel.locator("a")).toHaveCount(0);

  // Only the https attachment is actionable; every unsafe URL is rendered as a label only.
  await expect(panel.locator("[data-open-attachment-url]")).toHaveCount(1);
  await expect(panel.locator("[data-open-attachment-url]")).toHaveAttribute("data-open-attachment-url", SAFE_ATTACHMENT_URL);
  await expect(panel.locator("[data-download-attachment-url]")).toHaveAttribute("data-download-attachment-name", `Safe ${ATTR_BREAKOUT}`);
  for (let index = 0; index < UNSAFE_URLS.length; index += 1) {
    await expect(panel.getByText(`Unsafe attachment ${index}`, { exact: true })).toBeVisible();
  }
  await panel.getByRole("button", { name: "Open", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).openedLinks)).toEqual([SAFE_ATTACHMENT_URL]);

  // Form values round-trip exactly through the editor.
  await panel.getByRole("button", { name: "Edit", exact: true }).click();
  const form = app.locator("[data-event-editor-form]");
  await expect(form.locator('input[name="summary"]')).toHaveValue(EVENT_TITLE);
  await expect(form.locator('input[name="event_id"]')).toHaveValue(`evt${ATTR_BREAKOUT}`);
  await expect(form.locator('input[name="location"]')).toHaveValue(`Room ${PAYLOAD}`);
  await expect(form.locator('input[name="timezone"]')).toHaveValue(`UTC${ATTR_BREAKOUT}`);
  await expect(form.locator('textarea[name="description"]')).toHaveValue(
    `Description ${PAYLOAD} ${ENCODED_QUOTES.join(" ")}`,
  );

  const frame = await dashboardFrame(page, "/dist/index.html");
  await expectCleanDom(frame);
  expect(page.context().pages()).toHaveLength(1);
  expect(hostile).toEqual([]);
});

test("conference links are https-only and host-mediated", async ({ page }) => {
  await page.goto(`${ADVERSARIAL_HOST}&safeConference`);
  const app = page.frameLocator("#dashboard");
  await app.getByRole("button", { name: `Open event: ${EVENT_TITLE}`, exact: true }).click();
  const link = app.locator('.event-panel a[data-open-link="conference"]');
  await expect(link).toHaveAttribute("href", SAFE_CONFERENCE_LINK);
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
  await link.click();
  await expect.poll(() => page.evaluate(() => (window as any).openedLinks)).toEqual([SAFE_CONFERENCE_LINK]);
  const frame = await dashboardFrame(page, "/dist/index.html");
  expect(frame.url()).toContain("/dist/index.html");
  expect(page.context().pages()).toHaveLength(1);
});

test("sanitizes hostile email HTML without script, handlers, or remote loads", async ({ page }) => {
  const hostile = trackHostileRequests(page);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(ADVERSARIAL_HOST);
  const app = page.frameLocator("#dashboard");
  await app.locator(".inbox-row").first().click();
  const panel = app.locator(".email-panel");
  await expect(panel.getByRole("heading", { name: `Subject ${PAYLOAD}` })).toBeVisible();
  await expect(panel.locator(".email-sender-identities")).toContainText(`Mallory ${PAYLOAD}`);
  const body = panel.locator(".email-html");
  await expect(body).toContainText("Intro paragraph");
  await expect(body).toContainText("Tail paragraph");

  // Active, embedded, form and document-level elements are removed.
  await expect(
    body.locator("script, style, iframe, object, embed, svg, math, form, input, button, textarea, select, video, link, meta, base, noscript"),
  ).toHaveCount(0);
  await expect(app.locator('base, meta[http-equiv], link[href*="example"]')).toHaveCount(0);

  // Sender ids, classes and data-* action hooks never survive.
  await expect(body.locator("[id], [data-email-action], [data-message-id], [srcset], [background]")).toHaveCount(0);
  const classes = await body.locator("[class]").evaluateAll((nodes) => [...new Set(nodes.map((node) => node.className))]);
  expect(classes.every((name) => ["email-html-image", "email-image-blocked", "email-layout-table", "email-data-table"].includes(name))).toBe(true);
  await expect(app.locator(".overlay")).toHaveCount(1);
  await expect(body).toContainText("Spoofed overlay text");

  // Only the inline raster data image renders; remote, cid: and SVG images are blocked.
  await expect(body.locator("img")).toHaveCount(1);
  await expect(body.locator("img")).toHaveAttribute("src", DATA_IMAGE);
  await expect(body.locator(".email-image-blocked")).toContainText(["Tracking pixel", "Inline logo", "srcset", "SVG data image"]);

  // Inline styles keep only allowlisted, URL-free properties.
  const styles = await body.locator("[style]").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("style") || ""));
  expect(styles.length).toBeGreaterThan(0);
  for (const style of styles) {
    expect(style).not.toMatch(/url\(|expression|\\|color:|background/i);
  }
  await expect(body.locator("p").first()).toHaveAttribute("style", /font-weight:\s*700/);
  await expect(body.locator("td[colspan]")).toHaveCount(0);

  // Links: only http(s)/mailto keep an href, all marked for host-mediated opening.
  const links = body.locator("a[href]");
  await expect(links).toHaveCount(2);
  await expect(links.nth(0)).toHaveAttribute("href", SAFE_EMAIL_LINK);
  await expect(links.nth(1)).toHaveAttribute("href", "mailto:someone@example.com");
  for (const index of [0, 1]) {
    await expect(links.nth(index)).toHaveAttribute("rel", "noopener noreferrer nofollow");
    await expect(links.nth(index)).toHaveAttribute("target", "_blank");
    await expect(links.nth(index)).toHaveAttribute("data-open-link", "email");
  }
  await expect(body.locator("a:not([href])")).toHaveCount(UNSAFE_URLS.length);

  // Attachment metadata round-trips exactly as data, not markup.
  const downloads = panel.locator("[data-email-attachment-download]");
  await expect(downloads.nth(0)).toHaveAttribute("data-filename", ATTR_BREAKOUT);
  for (let index = 0; index < ENCODED_QUOTES.length; index += 1) {
    await expect(downloads.nth(index + 1)).toHaveAttribute("data-filename", ENCODED_QUOTES[index]);
  }
  await expect(panel.getByRole("button", { name: "Reply in chat" })).toBeVisible();

  // Spoofed data-* actions are inert; safe links go to the host, unsafe ones go nowhere.
  const callsBefore = await page.evaluate(() => (window as any).calls.length);
  await body.getByText("Spoofed action").click();
  await body.getByText("unsafe link").first().click();
  await body.getByRole("link", { name: "Safe link" }).click();
  await body.getByRole("link", { name: "Safe link" }).click({ button: "middle" });
  await expect.poll(() => page.evaluate(() => (window as any).openedLinks)).toEqual([SAFE_EMAIL_LINK, SAFE_EMAIL_LINK]);
  expect(await page.evaluate(() => (window as any).calls.length)).toBe(callsBefore);

  const frame = await dashboardFrame(page, "/dist/index.html");
  await page.waitForTimeout(500);
  await expectCleanDom(frame);
  expect(frame.url()).toContain("/dist/index.html");
  expect(page.context().pages()).toHaveLength(1);
  expect(hostile).toEqual([]);
  expect(errors).toEqual([]);
});

test("development standalone bridge trusts only the embedding parent", async ({ page }) => {
  await page.goto("/tests/standalone-host.html?noreply");
  const frame = await dashboardFrame(page, "/index.html?mode=standalone");
  const app = page.frameLocator('iframe[name="dashboard"]');
  await expect(app.getByText("Loading workspace dashboard...")).toBeVisible();

  // The data request goes to the parent's exact origin.
  await expect.poll(() => page.evaluate(() => (window as any).received)).toContainEqual({
    origin: new URL(page.url()).origin,
    fromDashboard: true,
    data: { type: "request_dashboard_data" },
  });

  // Count deliveries with a listener registered after the app's, so each hostile message
  // has already been seen (and ignored) by the app when the count advances.
  await frame.evaluate(() => {
    (window as any).__delivered = 0;
    window.addEventListener("message", () => { (window as any).__delivered += 1; });
  });
  await page.evaluate(() => (window as any).sendFromSibling("Injected by sibling"));
  await frame.evaluate(() => {
    const data = { type: "dashboard_data", data: { weekly_calendar: { week_start: "2026-09-21", week_end: "2026-09-27", timezone: "UTC", total_events: 0, days: [{ date: "2026-09-21", day_label: "Mon", is_today: false, all_day_events: [], timed_events: [{ event_id: "x", calendar_id: "primary", title: "Injected by wrong origin", start: "2026-09-21T10:00:00Z", end: "2026-09-21T11:00:00Z", all_day: false, status: "confirmed" }] }] } } };
    window.dispatchEvent(new MessageEvent("message", { data, origin: "https://evil.example", source: window.parent }));
    window.dispatchEvent(new MessageEvent("message", { data, origin: location.origin, source: window }));
  });
  await expect.poll(() => frame.evaluate(() => (window as any).__delivered)).toBe(3);
  await expect(app.getByText("Loading workspace dashboard...")).toBeVisible();
  await expect(app.getByText("Injected by sibling")).toHaveCount(0);
  await expect(app.getByText("Injected by wrong origin")).toHaveCount(0);

  await page.evaluate(() => (window as any).sendFromParent("Trusted parent meeting"));
  await expect(app.getByRole("button", { name: "Open event: Trusted parent meeting", exact: true })).toBeVisible();
});

test("production artifact does not include the standalone bridge", async ({ page }) => {
  await page.goto("/tests/standalone-host.html?target=dist");
  const frame = await dashboardFrame(page, "/dist/index.html?mode=standalone");
  await frame.waitForLoadState("load");
  await page.evaluate(() => (window as any).sendFromParent("Standalone data in production"));
  const app = page.frameLocator('iframe[name="dashboard"]');
  await expect(app.getByText("MCP app connection failed.").or(app.getByText("Loading workspace dashboard..."))).toBeVisible();
  await page.waitForTimeout(500);
  await expect(app.getByText("Standalone data in production")).toHaveCount(0);
  const requests = await page.evaluate(() => (window as any).received.filter((item: any) => item.data?.type === "request_dashboard_data"));
  expect(requests).toEqual([]);
});
