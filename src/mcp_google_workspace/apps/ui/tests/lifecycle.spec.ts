/**
 * W6 dashboard lifecycle through the sandbox host: invocation timing, the
 * operation manifest, typed errors, cancellation, teardown and stale responses.
 */
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { appFrame, blockExternalNetwork, hostValue } from "./helpers";

type Call = { name: string; arguments: Record<string, unknown> };

const EVENT = "Open event: AppBridge regression meeting";
const handle = (n: number) => `wsv_${String(n).padStart(43, "0")}`;
const callLog = (page: Page) => hostValue<Call[]>(page, "callLog");
const launchCalls = async (page: Page) =>
  (await callLog(page)).filter((call) => /(get_dashboard|get_weekly_calendar_view)$/.test(call.name));

let external: string[] = [];
test.beforeEach(async ({ page }) => {
  external = await blockExternalNetwork(page);
});
test.afterEach(() => {
  expect(external).toEqual([]);
});

test.describe("invocation timing", () => {
  test("input first, result later: renders the host result and never mints an orphan view", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&invocation=input-first&resultDelay=1500");
    const app = appFrame(page);
    await expect(app.getByRole("button", { name: EVENT, exact: true })).toBeVisible();
    // The only view is the one the host's launch call minted; the view made no launch call.
    expect(await hostValue<number>(page, "mintedViews")).toBe(1);
    expect(await hostValue<number>(page, "hostMintCount")).toBe(1);
    expect(await launchCalls(page)).toEqual([]);

    await app.getByRole("button", { name: "Next week" }).click();
    await expect.poll(async () => (await callLog(page)).map((call) => call.name)).toContain("apps_next_range");
    const next = (await callLog(page)).find((call) => call.name === "apps_next_range")!;
    expect(next.arguments).toEqual({ view_handle: handle(1), expected_revision: 1 });
    expect(await hostValue<number>(page, "mintedViews")).toBe(1);
  });

  test("input without a result never mints a view on its own", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&invocation=input-only");
    const app = appFrame(page);
    await expect(app.getByText("Loading workspace dashboard...")).toBeVisible();
    await page.waitForTimeout(2500);
    expect(await launchCalls(page)).toEqual([]);
    expect(await hostValue<number>(page, "mintedViews")).toBe(0);
  });

  test("result before input (racing host) renders once and ignores the late input", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&invocation=result-first");
    const app = appFrame(page);
    await expect(app.getByRole("button", { name: EVENT, exact: true })).toBeVisible();
    await page.waitForTimeout(2000);
    expect(await launchCalls(page)).toEqual([]);
    expect(await hostValue<number>(page, "mintedViews")).toBe(1);
  });

  test("a reopened weekly view replays the announced launch tool with the input handle", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&inputHandle&launch=weekly");
    const app = appFrame(page);
    await expect(app.getByRole("button", { name: EVENT, exact: true })).toBeVisible();
    expect(await launchCalls(page)).toEqual([
      { name: "apps_get_weekly_calendar_view", arguments: { view_handle: handle(1) } },
    ]);
  });

  test("a later pushed result replaces the rendered data", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&pushResult&pushHandle");
    const app = appFrame(page);
    await expect(app.getByRole("button", { name: EVENT, exact: true })).toBeVisible();
    await page.evaluate(() => (window as any).pushToolResult("Pushed again"));
    await expect(app.getByRole("button", { name: "Open event: Pushed again", exact: true })).toBeVisible();
  });

  test("a failed launch result is reported with an explicit way forward", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&invocation=error");
    const app = appFrame(page);
    await expect(app.getByText(/The dashboard could not be opened: .*unknown or has expired/)).toBeVisible();
    expect(await launchCalls(page)).toEqual([]);
    await app.getByRole("button", { name: "Open a new view" }).click();
    await expect(app.getByRole("button", { name: EVENT, exact: true })).toBeVisible();
    expect(await launchCalls(page)).toEqual([{ name: "apps_get_dashboard", arguments: {} }]);
  });
});

test.describe("cancellation and teardown", () => {
  test("a cancelled invocation stops loading and only loads on the user's request", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&invocation=cancelled");
    const app = appFrame(page);
    await expect(app.getByText("The dashboard request was cancelled.")).toBeVisible();
    await page.waitForTimeout(1500);
    expect(await callLog(page)).toEqual([]);
    await app.getByRole("button", { name: "Load dashboard" }).click();
    await expect(app.getByRole("button", { name: EVENT, exact: true })).toBeVisible();
    expect(await launchCalls(page)).toEqual([{ name: "apps_get_dashboard", arguments: {} }]);
  });

  test("teardown cancels the pending load and ignores a late result", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&invocation=input-only");
    const app = appFrame(page);
    await expect(app.getByText("Loading workspace dashboard...")).toBeVisible();
    await page.evaluate(() => (window as any).teardownAndSendLateResult());
    expect(await hostValue<number>(page, "lateResultCount")).toBe(1);
    await page.waitForTimeout(1000);
    await expect(app.getByText("Late result after teardown")).toHaveCount(0);
    await expect(app.getByText("Loading workspace dashboard...")).toBeVisible();
    expect(await callLog(page)).toEqual([]);
  });

  test("teardown during an in-flight request drops its response and its follow-ups", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&inputHandle&delay=get_dashboard:1500");
    const app = appFrame(page);
    // The reopen load starts after its grace period and is held by the host.
    await expect.poll(async () => (await launchCalls(page)).length, { timeout: 5000 }).toBe(1);
    await page.evaluate(() => (window as any).bridge.teardownResource({}));
    await page.waitForTimeout(2500);
    await expect(app.getByRole("button", { name: EVENT, exact: true })).toHaveCount(0);
    // No calendar list or other follow-up call was made after teardown.
    expect((await callLog(page)).map((call) => call.name)).toEqual(["apps_get_dashboard"]);
  });
});

test.describe("stale responses", () => {
  test("a slow earlier detail response never overwrites a newer selection", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&inbox&delay=get_email_detail:1500:msg-a");
    const app = appFrame(page);
    await expect(app.getByRole("button", { name: "Open email: Quarterly plan" })).toBeVisible();
    await app.getByRole("button", { name: "Open email: Quarterly plan" }).click();
    await app.getByRole("button", { name: "Open email: Lunch" }).click();
    const panel = app.locator(".email-panel");
    await expect(panel.getByRole("heading", { name: "Lunch" })).toBeVisible();
    await page.waitForTimeout(2000);
    await expect(panel.getByRole("heading", { name: "Lunch" })).toBeVisible();
    await expect(app.getByRole("heading", { name: "Quarterly plan" })).toHaveCount(0);
  });
});

test.describe("operation manifest", () => {
  test("writes missing from the manifest are not offered and reads still work", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&inbox&manifest=reads");
    const app = appFrame(page);
    await expect(app.getByRole("button", { name: EVENT, exact: true })).toBeVisible();
    await expect(app.getByRole("button", { name: "Next week" })).toBeDisabled();
    await expect(app.getByLabel("Show weekend")).toHaveCount(0);
    await app.getByRole("button", { name: "Open email: Quarterly plan" }).click();
    const panel = app.locator(".email-panel");
    await expect(panel.getByRole("heading", { name: "Quarterly plan" })).toBeVisible();
    await expect(panel.getByRole("button", { name: "Archive" })).toHaveCount(0);
    await expect(panel.getByRole("button", { name: "Mark read" })).toHaveCount(0);
  });

  test("without a manifest no write is guessed", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&inbox&manifest=none");
    const app = appFrame(page);
    await expect(app.getByRole("button", { name: EVENT, exact: true })).toBeVisible();
    await expect(app.getByRole("button", { name: "Next week" })).toBeDisabled();
    await expect(app.getByRole("button", { name: "Create" })).toHaveCount(0);
    const names = (await callLog(page)).map((call) => call.name);
    expect(names.every((name) => /get_dashboard|get_weekly_calendar_view|get_event_detail|get_email_detail|list_calendars/.test(name))).toBe(true);
  });

  test("a principal without the Gmail grant gets no mail actions", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&inbox&grants=calendar");
    const app = appFrame(page);
    await expect(app.getByRole("button", { name: "Create" })).toBeVisible();
    await app.getByRole("button", { name: "Open email: Quarterly plan" }).click();
    await expect(app.getByText("This action is not available for your account here.", { exact: false })).toBeVisible();
  });
});

test.describe("errors", () => {
  async function openAndArchive(page: Page, query: string) {
    await page.goto(`/tests/host.html?discovery=unsupported&inbox&${query}`);
    const app = appFrame(page);
    await app.getByRole("button", { name: "Open email: Quarterly plan" }).click();
    const panel = app.locator(".email-panel");
    await expect(panel.getByRole("heading", { name: "Quarterly plan" })).toBeVisible();
    await expect(panel.locator(".status-chip", { hasText: "Inbox" })).toBeVisible();
    await panel.getByRole("button", { name: "Archive" }).click();
    return { app, panel };
  }

  test("an isError result is a failure: no success notice, optimistic state restored", async ({ page }) => {
    const { app, panel } = await openAndArchive(page, "fail=move_email:iserror:provider_error");
    await expect(app.getByText("Failed to archive email: The server could not run gmail_move_email.")).toBeVisible();
    await expect(app.getByText("Email archived.")).toHaveCount(0);
    await expect(panel.locator(".status-chip", { hasText: "Inbox" })).toBeVisible();
  });

  test("a rejected call is a failure with the server's typed error message", async ({ page }) => {
    const { app, panel } = await openAndArchive(page, "fail=move_email:reject:quota_exceeded");
    await expect(app.getByText("Failed to archive email: Server rejected gmail_move_email [quota_exceeded]")).toBeVisible();
    await expect(app.getByText("Email archived.")).toHaveCount(0);
    await expect(panel.locator(".status-chip", { hasText: "Inbox" })).toBeVisible();
  });

  test("a typed confirmation error asks the user to use the chat", async ({ page }) => {
    const { app } = await openAndArchive(page, "fail=move_email:iserror:confirmation_required");
    await expect(app.getByText(/Failed to archive email: The server needs your confirmation/)).toBeVisible();
  });

  test("success is reported only after the server confirmed the action", async ({ page }) => {
    const { app, panel } = await openAndArchive(page, "delay=move_email:1000");
    await expect(app.getByText("Email archived.")).toHaveCount(0);
    await expect(app.getByText("Email archived.")).toBeVisible();
    await expect(panel.locator(".status-chip", { hasText: "Inbox" })).toHaveCount(0);
  });

  test("an embedded provider error in a detail result is reported", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&inbox&fail=get_email_detail:embedded");
    const app = appFrame(page);
    await app.getByRole("button", { name: "Open email: Quarterly plan" }).click();
    await expect(app.getByText("Failed to load email details: Provider exploded")).toBeVisible();
    await expect(app.locator(".email-panel")).toHaveCount(0);
  });

  test("the host's visibility rejection is shown and the optimistic change undone", async ({ page }) => {
    const { app, panel } = await openAndArchive(page, "modelOnly=move_email");
    await expect(app.getByText(/Failed to archive email: .*not available to apps/)).toBeVisible();
    expect(await hostValue<string[]>(page, "rejectedCalls")).toEqual(["gmail_move_email"]);
    await expect(panel.locator(".status-chip", { hasText: "Inbox" })).toBeVisible();
  });
});
