import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { appFrame, blockExternalNetwork, viewFrame } from "./helpers";

type Call = { name: string; arguments: Record<string, unknown> };

const handle = (n: number) => `wsv_${String(n).padStart(43, "0")}`;
const EVENT = "Open event: AppBridge regression meeting";

test.beforeEach(async ({ page }) => {
  await blockExternalNetwork(page);
});

const callLog = (page: Page) => page.evaluate(() => (window as any).callLog as Call[]);
const viewCalls = async (page: Page) =>
  (await callLog(page)).filter((call) =>
    /(get_dashboard|get_weekly_calendar_view|patch_state|next_range|prev_range|today)$/.test(call.name),
  );

test("uses the server-issued handle on every callback and never mints an id", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/tests/host.html?discovery=unsupported");
  const app = appFrame(page);
  await expect(app.getByRole("button", { name: EVENT, exact: true })).toBeVisible();

  await app.getByRole("button", { name: "Next week" }).click();
  await expect.poll(async () => (await viewCalls(page)).map((call) => call.name)).toContain(
    "apps_next_range",
  );
  await expect.poll(async () => (await viewCalls(page)).length).toBeGreaterThanOrEqual(3);

  const calls = await viewCalls(page);
  // The first launch call has no handle: the server mints one.
  expect(calls[0]).toEqual({ name: "apps_get_dashboard", arguments: {} });
  for (const call of calls.slice(1)) {
    expect(call.arguments.view_handle).toBe(handle(1));
  }
  const next = calls.find((call) => call.name === "apps_next_range")!;
  expect(next.arguments).toEqual({ view_handle: handle(1), expected_revision: 1 });

  const storage = await (await viewFrame(page)).evaluate(() => {
    try {
      return Object.keys(window.localStorage);
    } catch {
      return [];
    }
  });
  expect(storage).toEqual([]);
  expect(errors).toEqual([]);
});

test("adopts the handle carried by the host-pushed tool result", async ({ page }) => {
  await page.goto("/tests/host.html?discovery=unsupported&pushResult&pushHandle");
  const app = appFrame(page);
  await expect(app.getByRole("button", { name: EVENT, exact: true })).toBeVisible();

  await app.getByRole("button", { name: "Next week" }).click();
  await expect.poll(async () => (await viewCalls(page)).map((call) => call.name)).toContain(
    "apps_get_weekly_calendar_view",
  );
  const calls = await viewCalls(page);
  expect(calls.map((call) => call.arguments.view_handle)).toEqual(calls.map(() => handle(1)));
  expect(calls[0]).toEqual({
    name: "apps_next_range",
    arguments: { view_handle: handle(1), expected_revision: 1 },
  });
});

test("reopens the view whose handle arrives as tool input", async ({ page }) => {
  await page.goto("/tests/host.html?discovery=unsupported&inputHandle");
  const app = appFrame(page);
  await expect(app.getByRole("button", { name: EVENT, exact: true })).toBeVisible();
  const calls = await viewCalls(page);
  expect(calls[0]).toEqual({ name: "apps_get_dashboard", arguments: { view_handle: handle(1) } });
  expect(calls.filter((call) => !call.arguments.view_handle)).toEqual([]);
});

test("re-requests a fresh view once when the handle expired", async ({ page }) => {
  await page.goto("/tests/host.html?discovery=unsupported&view=expired");
  const app = appFrame(page);
  await expect(app.getByRole("button", { name: EVENT, exact: true })).toBeVisible();

  await app.getByRole("button", { name: "Next week" }).click();
  await expect.poll(async () => (await viewCalls(page)).filter((c) => c.name === "apps_next_range").length).toBe(2);
  await expect.poll(async () => (await viewCalls(page)).at(-1)?.name).toBe("apps_get_weekly_calendar_view");

  const calls = await viewCalls(page);
  const expired = calls.findIndex((call) => call.name === "apps_next_range");
  expect(calls[expired].arguments.view_handle).toBe(handle(1));
  // Fresh view: a launch call without a handle, then one retry with the new handle.
  expect(calls[expired + 1]).toEqual({ name: "apps_get_dashboard", arguments: {} });
  expect(calls[expired + 2]).toEqual({
    name: "apps_next_range",
    arguments: { view_handle: handle(2), expected_revision: 1 },
  });
  expect(calls[expired + 3].arguments.view_handle).toBe(handle(2));
  await expect(app.getByText("Failed to navigate week")).toHaveCount(0);
});

test("stops after a single fresh-view retry when the handle keeps failing", async ({ page }) => {
  await page.goto("/tests/host.html?discovery=unsupported&view=expired-always");
  const app = appFrame(page);
  await expect(app.getByRole("button", { name: EVENT, exact: true })).toBeVisible();

  await app.getByRole("button", { name: "Next week" }).click();
  await expect(app.getByText(/Failed to navigate week: .*unknown or has expired/)).toBeVisible();
  const calls = await viewCalls(page);
  expect(calls.filter((call) => call.name === "apps_next_range")).toHaveLength(2);
  expect(calls.filter((call) => call.name === "apps_get_dashboard" && !call.arguments.view_handle)).toHaveLength(2);
});

test("refetches instead of overwriting after a stale-revision conflict", async ({ page }) => {
  await page.goto("/tests/host.html?discovery=unsupported&view=conflict");
  const app = appFrame(page);
  await expect(app.getByRole("button", { name: EVENT, exact: true })).toBeVisible();

  // click(), not uncheck(): without a dashboard state the view re-renders the
  // checkbox from server state, which Playwright's uncheck() would keep retrying.
  await app.getByLabel("Show weekend").click();
  await expect(app.getByText("This dashboard view changed elsewhere and was refreshed. Try again.")).toBeVisible();

  const calls = await viewCalls(page);
  const patches = calls.filter((call) => call.name === "apps_patch_state");
  expect(patches).toEqual([
    {
      name: "apps_patch_state",
      arguments: { view_handle: handle(1), expected_revision: 1, include_weekend: false },
    },
  ]);
  const refetch = calls.slice(calls.indexOf(patches[0]) + 1);
  expect(refetch.map((call) => call.name)).toContain("apps_get_dashboard");
  expect(refetch.every((call) => call.arguments.view_handle === handle(1))).toBe(true);
  await expect(app.getByLabel("Show weekend")).toBeChecked();
});
