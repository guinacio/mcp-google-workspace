/**
 * W6 host policy through the different-origin sandbox proxy: sandbox and CSP
 * enforcement, capability gating of optional host actions, links, downloads,
 * chat messages, model context, display modes, sizing, keyboard and theming,
 * and the bundled Prefab file picker under the restrictive default CSP.
 */
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { SAFE_ATTACHMENT_URL } from "./fixtures";
import {
  SANDBOX_ORIGIN,
  appFrame,
  blockExternalNetwork,
  cspViolations,
  hostValue,
  viewFrame,
  watchCspViolations,
} from "./helpers";

const EVENT = "Open event: AppBridge regression meeting";

let external: string[] = [];
test.beforeEach(async ({ page }) => {
  external = await blockExternalNetwork(page);
});
test.afterEach(() => {
  expect(external).toEqual([]);
});

async function openEmail(page: Page, query: string) {
  await page.goto(`/tests/host.html?discovery=unsupported&inbox&${query}`);
  const app = appFrame(page);
  await app.getByRole("button", { name: "Open email: Quarterly plan" }).click();
  const panel = app.locator(".email-panel");
  await expect(panel.getByRole("heading", { name: "Quarterly plan" })).toBeVisible();
  return { app, panel };
}

test.describe("sandbox and CSP", () => {
  test("the view runs in an opaque-origin sandbox behind a different-origin proxy", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported");
    await expect(appFrame(page).getByRole("button", { name: EVENT, exact: true })).toBeVisible();
    const frame = await viewFrame(page);
    expect(new URL(frame.url()).origin).toBe(SANDBOX_ORIGIN);
    expect(new URL(page.url()).origin).not.toBe(SANDBOX_ORIGIN);
    const probe = await frame.evaluate(() => {
      let parentReadable = true;
      try {
        void (window.parent as unknown as { document: Document }).document.title;
      } catch {
        parentReadable = false;
      }
      let storage = "available";
      try {
        window.localStorage.getItem("x");
      } catch {
        storage = "blocked";
      }
      return { origin: window.origin, parentReadable, storage };
    });
    expect(probe).toEqual({ origin: "null", parentReadable: false, storage: "blocked" });
    const sandbox = await page.frameLocator("#dashboard").locator("#view").getAttribute("sandbox");
    expect(sandbox).toBe("allow-scripts allow-forms");
  });

  test("undeclared requests are blocked by the CSP and never reach a server", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported");
    await expect(appFrame(page).getByRole("button", { name: EVENT, exact: true })).toBeVisible();
    const frame = await viewFrame(page);
    await watchCspViolations(frame);
    const outcome = await frame.evaluate(async (sandboxOrigin) => {
      const results: Record<string, string> = {};
      try {
        await fetch(`${sandboxOrigin}/probe/fetch`);
        results.fetch = "loaded";
      } catch {
        results.fetch = "blocked";
      }
      results.image = await new Promise<string>((resolve) => {
        const image = new Image();
        image.onload = () => resolve("loaded");
        image.onerror = () => resolve("blocked");
        image.src = "http://127.0.0.1:4173/probe/image.png";
      });
      try {
        // eslint-disable-next-line no-eval
        (0, eval)("1 + 1");
        results.eval = "allowed";
      } catch {
        results.eval = "blocked";
      }
      return results;
    }, SANDBOX_ORIGIN);
    expect(outcome).toEqual({ fetch: "blocked", image: "blocked", eval: "blocked" });
    await expect.poll(() => cspViolations(frame)).toEqual(
      expect.arrayContaining([expect.stringMatching(/^connect-src /), expect.stringMatching(/^img-src /)]),
    );
    const probes = await page.evaluate(async (origin) => (await fetch(`${origin}/probe-log`)).json(), SANDBOX_ORIGIN);
    expect(probes).toEqual([]);
  });

  test("the dashboard itself triggers no CSP violation while rendering and navigating", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&inbox&pushResult&pushHandle");
    const app = appFrame(page);
    await expect(app.getByRole("button", { name: EVENT, exact: true })).toBeVisible();
    const frame = await viewFrame(page);
    await watchCspViolations(frame);
    await app.getByRole("button", { name: "Next week" }).click();
    await app.getByRole("button", { name: "Open email: Quarterly plan" }).click();
    await expect(app.locator(".email-panel")).toBeVisible();
    expect(await cspViolations(frame)).toEqual([]);
  });

  test("the bundled Prefab picker renders under its declared CSP with the network blocked", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/tests/host.html?app=prefab");
    const fixture = await hostValue<{ picker: { ui: { csp?: Record<string, string[]> } } }>(page, "serverFixture");
    // Bundled delivery declares no external domains: the restrictive default applies.
    expect(Object.values(fixture.picker.ui.csp ?? {}).flat()).toEqual([]);
    const app = appFrame(page);
    await expect(app.getByText("Drop files here or choose files")).toBeVisible({ timeout: 15_000 });
    await expect(app.getByText("Choose Workspace files")).toBeVisible();
    expect(errors).toEqual([]);
  });
});

test.describe("capability gating", () => {
  test("a host without server tools gets a read-only rendering and no calls", async ({ page }) => {
    await page.goto("/tests/host.html?caps=none&pushResult&pushHandle");
    const app = appFrame(page);
    await expect(app.getByRole("button", { name: EVENT, exact: true })).toBeVisible();
    await expect(app.getByRole("button", { name: "Next week" })).toBeDisabled();
    await expect(app.getByRole("button", { name: "Create" })).toHaveCount(0);
    await page.waitForTimeout(1500);
    expect(await hostValue<string[]>(page, "calls")).toEqual([]);
    expect(await hostValue<unknown[]>(page, "cursors")).toEqual([]);
  });

  test("a reduced host hides chat and downloads and offers the link address instead of opening it", async ({ page }) => {
    const { app, panel } = await openEmail(page, "caps=reduced");
    await expect(panel.getByRole("button", { name: "Reply in chat" })).toHaveCount(0);
    await expect(panel.getByRole("button", { name: "Download" })).toHaveCount(0);
    await expect(panel.getByText("Download unavailable in this host", { exact: false }).first()).toBeVisible();
    await panel.getByRole("button", { name: "Close" }).click();

    await page.goto("/tests/host.html?discovery=unsupported&fixture=adversarial&caps=reduced");
    const adversarialApp = appFrame(page);
    await adversarialApp.locator("[data-open-event]").first().click();
    const eventPanel = adversarialApp.locator(".event-panel");
    await expect(eventPanel.locator("[data-download-attachment-url]")).toHaveCount(0);
    await eventPanel.getByRole("button", { name: "Open", exact: true }).click();
    await expect(adversarialApp.getByText("This host does not open links from the dashboard.", { exact: false })).toBeVisible();
    await expect(adversarialApp.locator("input[data-fallback-url]")).toHaveValue(SAFE_ATTACHMENT_URL);
    expect(await hostValue<string[]>(page, "openedLinks")).toEqual([]);
    expect(page.context().pages()).toHaveLength(1);
    void app;
  });

  for (const mode of ["decline", "reject"] as const) {
    test(`a host that ${mode}s openLink keeps the view in place and shows the address`, async ({ page }) => {
      await page.goto(`/tests/host.html?discovery=unsupported&fixture=adversarial&openLink=${mode}`);
      const app = appFrame(page);
      await app.locator("[data-open-event]").first().click();
      await app.locator(".event-panel").getByRole("button", { name: "Open", exact: true }).click();
      await expect(
        app.getByText(mode === "decline" ? "The host declined to open the attachment." : "The host could not open the attachment", {
          exact: false,
        }),
      ).toBeVisible();
      await expect(app.locator("input[data-fallback-url]")).toHaveValue(SAFE_ATTACHMENT_URL);
      expect(page.context().pages()).toHaveLength(1);
      expect((await viewFrame(page)).url()).toContain("/view/");
    });
  }

  test("email attachments download inline through the host, bounded in size", async ({ page }) => {
    const { app, panel } = await openEmail(page, "");
    await panel.getByRole("button", { name: "Download" }).first().click();
    await expect(app.getByText("Download started: plan.pdf")).toBeVisible();
    const downloads = await hostValue<Array<{ contents: Array<{ type: string; resource: { blob: string; mimeType: string } }> }>>(
      page,
      "downloads",
    );
    expect(downloads).toHaveLength(1);
    expect(downloads[0].contents[0].type).toBe("resource");
    expect(downloads[0].contents[0].resource.mimeType).toBe("application/pdf");

    await panel.getByRole("button", { name: "Download" }).nth(1).click();
    await expect(app.getByText(/huge\.zip is too large to download here/)).toBeVisible();
    const attachmentCalls = (await hostValue<string[]>(page, "calls")).filter((name) => name.endsWith("get_email_attachment"));
    expect(attachmentCalls).toHaveLength(1);
    expect(await hostValue<unknown[]>(page, "downloads")).toHaveLength(1);
  });

  test("a declined download is final: no second attempt, no forced alternative", async ({ page }) => {
    const { app, panel } = await openEmail(page, "download=decline");
    await panel.getByRole("button", { name: "Download" }).first().click();
    await expect(app.getByText("The host declined the download of plan.pdf.")).toBeVisible();
    expect(await hostValue<unknown[]>(page, "downloads")).toHaveLength(1);
    expect(await hostValue<string[]>(page, "openedLinks")).toEqual([]);
  });

  test("a declined linked download offers opening the link as the user's choice", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&fixture=adversarial&download=decline");
    const app = appFrame(page);
    await app.locator("[data-open-event]").first().click();
    await app.locator(".event-panel [data-download-attachment-url]").click();
    await expect(app.getByText("The host declined the download", { exact: false })).toBeVisible();
    expect(await hostValue<string[]>(page, "openedLinks")).toEqual([]);
    await app.getByRole("button", { name: "Open link instead" }).click();
    await expect.poll(() => hostValue<string[]>(page, "openedLinks")).toEqual([SAFE_ATTACHMENT_URL]);
  });

  test("Reply in chat sends a user-triggered message only when the host supports it", async ({ page }) => {
    const { app, panel } = await openEmail(page, "");
    await panel.getByRole("button", { name: "Reply in chat" }).click();
    await expect(app.getByText("Sent to the chat.")).toBeVisible();
    expect(await hostValue<unknown[]>(page, "messages")).toEqual([
      { role: "user", content: [{ type: "text", text: "Reply to Alice <alice@example.com> about: Quarterly plan" }] },
    ]);
  });

  test("a declined chat message is reported", async ({ page }) => {
    const { app, panel } = await openEmail(page, "message=decline");
    await panel.getByRole("button", { name: "Reply in chat" }).click();
    await expect(app.getByText("The host declined the chat message.")).toBeVisible();
  });

  test("opening a detail shares minimal model context when the host accepts it", async ({ page }) => {
    await openEmail(page, "");
    await expect.poll(() => hostValue<unknown[]>(page, "modelContexts")).toHaveLength(1);
    const [context] = await hostValue<Array<{ content: Array<{ text: string }> }>>(page, "modelContexts");
    expect(context.content[0].text).toContain('The user opened the email "Quarterly plan"');
  });

  test("no model context update without the capability", async ({ page }) => {
    await openEmail(page, "caps=reduced");
    await page.waitForTimeout(500);
    expect(await hostValue<unknown[]>(page, "modelContexts")).toEqual([]);
  });

  test("full screen is offered only when the host lists it", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported");
    const app = appFrame(page);
    await expect(app.getByRole("button", { name: EVENT, exact: true })).toBeVisible();
    await expect(app.getByRole("button", { name: "Full screen" })).toHaveCount(0);

    await page.goto("/tests/host.html?discovery=unsupported&fullscreen");
    await appFrame(page).getByRole("button", { name: "Full screen" }).click();
    await expect(appFrame(page).getByRole("button", { name: "Exit full screen" })).toBeVisible();
    expect(await hostValue<string[]>(page, "displayModeRequests")).toEqual(["fullscreen"]);
  });
});

test.describe("layout, keyboard and theme", () => {
  test("fixed container: the view fills it and scrolls inside", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&sizing=fixed");
    await expect(appFrame(page).getByRole("button", { name: EVENT, exact: true })).toBeVisible();
    const frame = await viewFrame(page);
    const layout = await frame.evaluate(() => ({
      sizing: document.documentElement.dataset.sizing,
      height: document.documentElement.getBoundingClientRect().height,
      scrolls: document.body.scrollHeight > document.body.clientHeight,
    }));
    expect(layout).toEqual({ sizing: "fixed", height: 420, scrolls: true });
  });

  test("flexible container: the view reports its size to the host", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&sizing=flexible");
    await expect(appFrame(page).getByRole("button", { name: EVENT, exact: true })).toBeVisible();
    await expect.poll(async () => (await hostValue<Array<{ height?: number }>>(page, "sizeChanges")).length).toBeGreaterThan(0);
    const sizes = await hostValue<Array<{ width?: number; height?: number }>>(page, "sizeChanges");
    expect(sizes.at(-1)!.width).toBe(1200);
    expect(sizes.at(-1)!.height).toBeGreaterThan(200);
  });

  test("narrow container: no page-level horizontal overflow", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&inbox&frameWidth=360");
    await expect(appFrame(page).getByRole("button", { name: EVENT, exact: true })).toBeVisible();
    const frame = await viewFrame(page);
    const overflow = await frame.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });

  test("keyboard: open a detail with Enter, focus moves in, Escape returns focus", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported&inbox");
    const app = appFrame(page);
    const row = app.getByRole("button", { name: "Open email: Quarterly plan" });
    await row.focus();
    await page.keyboard.press("Enter");
    await expect(app.locator(".email-panel")).toBeVisible();
    await expect(app.locator(".email-panel [data-close-email]").first()).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(app.locator(".email-panel")).toHaveCount(0);
    await expect(row).toBeFocused();

    const card = app.getByRole("button", { name: EVENT, exact: true });
    await card.focus();
    await page.keyboard.press("Enter");
    await expect(app.locator(".event-panel [data-close-event]").first()).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(card).toBeFocused();
  });

  test("keyboard: week navigation keeps focus on the pressed control", async ({ page }) => {
    await page.goto("/tests/host.html?discovery=unsupported");
    const app = appFrame(page);
    const next = app.getByRole("button", { name: "Next week" });
    await expect(next).toBeEnabled();
    await next.focus();
    await page.keyboard.press("Enter");
    await expect.poll(() => hostValue<string[]>(page, "calls")).toContain("apps_get_weekly_calendar_view");
    await expect(next).toBeFocused();
  });

  for (const theme of ["dark", "light"] as const) {
    test(`${theme} host theme is applied and follows host changes`, async ({ page }) => {
      await page.goto(`/tests/host.html?discovery=unsupported&theme=${theme}`);
      const app = appFrame(page);
      await expect(app.getByRole("button", { name: EVENT, exact: true })).toBeVisible();
      const colors = { dark: "rgb(32, 33, 36)", light: "rgb(248, 250, 253)" };
      await expect(app.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(app.locator("body")).toHaveCSS("background-color", colors[theme]);
      const other = theme === "dark" ? "light" : "dark";
      await page.evaluate((next) => (window as any).bridge.setHostContext({ theme: next }), other);
      await expect(app.locator("html")).toHaveAttribute("data-theme", other);
      await expect(app.locator("body")).toHaveCSS("background-color", colors[other]);
    });
  }
});
