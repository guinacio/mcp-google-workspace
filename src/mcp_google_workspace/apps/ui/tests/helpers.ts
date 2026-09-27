import { expect } from "@playwright/test";
import type { Frame, FrameLocator, Page } from "@playwright/test";

export const HOST_ORIGIN = "http://127.0.0.1:4173";
export const SANDBOX_ORIGIN = "http://localhost:4174";

/** The view, inside the sandbox proxy (#dashboard) and its inner iframe (#view). */
export function appFrame(page: Page): FrameLocator {
  return page.frameLocator("#dashboard").frameLocator("#view");
}

/** The view's Frame (served from the sandbox origin at /view/<id>). */
export async function viewFrame(page: Page): Promise<Frame> {
  await expect.poll(() => page.frames().some((frame) => isViewFrame(frame))).toBe(true);
  return page.frames().find((frame) => isViewFrame(frame))!;
}

function isViewFrame(frame: Frame): boolean {
  return frame.url().startsWith(`${SANDBOX_ORIGIN}/view/`);
}

/**
 * Abort (and record) every request that leaves the two test origins, from any
 * frame. The dashboard and the bundled picker must not need any of them.
 */
export async function blockExternalNetwork(page: Page): Promise<string[]> {
  const external: string[] = [];
  await page.context().route(
    (url) => url.origin !== HOST_ORIGIN && url.origin !== SANDBOX_ORIGIN && url.protocol !== "data:",
    (route) => {
      external.push(route.request().url());
      return route.abort();
    },
  );
  return external;
}

/** CSP violations reported inside the view (listener installed in the view frame). */
export async function watchCspViolations(frame: Frame): Promise<void> {
  await frame.evaluate(() => {
    const store: string[] = [];
    (window as unknown as { __cspViolations?: string[] }).__cspViolations = store;
    document.addEventListener("securitypolicyviolation", (event) => {
      store.push(`${event.effectiveDirective} ${event.blockedURI}`);
    });
  });
}

export async function cspViolations(frame: Frame): Promise<string[]> {
  return frame.evaluate(() => (window as unknown as { __cspViolations?: string[] }).__cspViolations ?? []);
}

/** Host-side recorded values (window globals set by tests/host.ts). */
export async function hostValue<T>(page: Page, name: string): Promise<T> {
  // host.ts publishes its globals after loading the server fixture.
  await page.waitForFunction(() => (window as unknown as { bridge?: unknown }).bridge !== undefined);
  return page.evaluate((key) => {
    const value = (window as unknown as Record<string, unknown>)[key];
    return (typeof value === "function" ? (value as () => unknown)() : value) as T;
  }, name);
}
