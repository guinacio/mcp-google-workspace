import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  // The dashboard's optional public fonts are unrelated to host integration.
  await page.route(/https:\/\/fonts\.(googleapis|gstatic)\.com\//, (route) => route.abort());
});

for (const discovery of ["supported", "unsupported", "malformed", "partial"]) {
  test(`loads and navigates with ${discovery} tool discovery`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`/tests/host.html?discovery=${discovery}`);
    const app = page.frameLocator("#dashboard");
    await expect(app.getByRole("button", { name: "Open event: AppBridge regression meeting", exact: true })).toBeVisible();
    await expect(app.getByText("MCP app connection failed.")).toHaveCount(0);
    const discovered = discovery === "supported" || discovery === "partial";
    await expect.poll(() => page.evaluate(() => (window as any).calls)).toContain(
      discovered ? "get_dashboard" : "apps_get_dashboard",
    );
    if (discovery === "supported") {
      expect(await page.evaluate(() => (window as any).cursors)).toEqual([undefined, "second-page"]);
    }
    await app.getByRole("button", { name: "Next week" }).click();
    await expect.poll(() => page.evaluate(() => (window as any).calls)).toContain(
      discovery === "supported" ? "get_weekly_calendar_view" : "apps_get_weekly_calendar_view",
    );
    expect(errors).toEqual([]);
  });
}

test("renders host-pushed data when tools/list is unsupported", async ({ page }) => {
  await page.goto("/tests/host.html?discovery=unsupported&pushResult");
  const app = page.frameLocator("#dashboard");
  await expect(app.getByRole("button", { name: "Open event: AppBridge regression meeting", exact: true })).toBeVisible();
  await expect(app.getByText("MCP app connection failed.")).toHaveCount(0);
});

test("applies initial host context and later partial updates", async ({ page }) => {
  await page.goto("/tests/host.html?discovery=unsupported&styled");
  const app = page.frameLocator("#dashboard");
  await expect(app.getByRole("button", { name: "Open event: AppBridge regression meeting", exact: true })).toBeVisible();
  await expect(app.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(app.locator("body")).toHaveCSS("background-color", "rgb(240, 230, 220)");
  await expect(app.locator("body")).toHaveCSS("color", "rgb(10, 20, 30)");
  await expect(app.locator("body")).toHaveCSS("font-family", '"Host Sans", sans-serif');
  await expect(app.locator("body")).toHaveCSS("padding", "1px 2px 3px 4px");
  expect(await app.locator("head").textContent()).toContain('@font-face { font-family: "Host Sans"');
  await app.locator("body").evaluate((body) => {
    const sample = document.createElement("div");
    sample.className = "email-body-content";
    sample.innerHTML = "<code>host font</code>";
    body.appendChild(sample);
  });
  await expect(app.locator("code")).toHaveCSS("font-family", '"Host Mono", monospace');

  await page.evaluate(() => (window as any).bridge.setHostContext({
    theme: "dark",
    styles: { variables: { "--color-background-primary": "rgb(20, 30, 40)" } },
    safeAreaInsets: { top: 0, right: 0, bottom: 0, left: 0 },
  }));
  await expect(app.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(app.locator("body")).toHaveCSS("background-color", "rgb(20, 30, 40)");
  await expect(app.locator("body")).toHaveCSS("padding", "0px");
  await expect(app.locator("body")).toHaveCSS("color", "rgb(10, 20, 30)");
});

test("keeps default colors when the host supplies no style variables", async ({ page }) => {
  await page.goto("/tests/host.html?discovery=unsupported");
  const app = page.frameLocator("#dashboard");
  await expect(app.getByRole("button", { name: "Open event: AppBridge regression meeting", exact: true })).toBeVisible();
  await expect(app.locator("body")).toHaveCSS("background-color", "rgb(32, 33, 36)");
  await page.evaluate(() => (window as any).bridge.setHostContext({ theme: "light" }));
  await expect(app.locator("body")).toHaveCSS("background-color", "rgb(248, 250, 253)");
});
