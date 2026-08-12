/**
 * Device mode — the chrome the desk grows when the castle itself serves it.
 *
 * No hardware in CI, so the castle is played by page.route: stub /api/status
 * and the probe in device.ts believes, exactly as it would on the porch. The
 * important inverse is tested too — with no stub the chip must never appear,
 * because every laptop user of the desk lives in that world.
 */

import { test, expect } from "./fixtures.js";

const STATUS = {
  version: "5.3",
  compiled: "test",
  uptime_s: 4210,
  sd_mounted: true,
  psram_free_kb: 1500,
  heap_free_kb: 70,
};

const FILES = [
  { name: "logs", size: 0, dir: true },
  { name: "wicked_winds.mp3", size: 287744, dir: false },
  { name: "ghostbusters.mp3", size: 985088, dir: false },
];

/** Wire up a pretend castle and remember what the desk asks of it. */
async function stubCastle(page: import("@playwright/test").Page): Promise<string[]> {
  const calls: string[] = [];
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    calls.push(`${route.request().method()} ${url.pathname}${url.search}`);
    if (url.pathname === "/api/status")
      return route.fulfill({ json: STATUS });
    if (url.pathname === "/api/files")
      return route.fulfill({ json: FILES });
    if (url.pathname === "/api/bootlog")
      return route.fulfill({ body: "boot log: 2 lines, 0 dropped\n[I][app] up\n" });
    return route.fulfill({ json: { queued: true } });
  });
  return calls;
}

test("without a castle answering, no device chrome appears", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#stage")).toBeVisible();
  // The probe times out at 1.5 s; give it room to have failed.
  await page.waitForTimeout(2000);
  await expect(page.locator("#deviceChip")).toBeHidden();
});

test("served by the castle, the chip appears and mirrors scenes", async ({ page }) => {
  const calls = await stubCastle(page);
  await page.goto("/");
  const chip = page.locator("#deviceChip");
  await expect(chip).toBeVisible();
  await expect(chip).toContainText("v5.3");
  await expect(chip).toContainText("SD ok");

  // Picking a scene fires it on the hardware too.
  await page.locator("button.scene", { hasText: "Storm" }).first().click();
  await expect.poll(() => calls.filter((c) => c.includes("/api/scene")).length)
    .toBeGreaterThan(0);
  expect(calls.some((c) => c.includes("s=storm"))).toBe(true);
});

test("mirroring off means scene picks stay local", async ({ page }) => {
  const calls = await stubCastle(page);
  await page.goto("/");
  await expect(page.locator("#deviceChip")).toBeVisible();
  await page.locator("#devMirror").uncheck();
  await page.locator("button.scene", { hasText: "Storm" }).first().click();
  await page.waitForTimeout(300);
  expect(calls.filter((c) => c.includes("/api/scene"))).toHaveLength(0);
});

test("the panel lists the card and plays a track on the castle", async ({ page }) => {
  const calls = await stubCastle(page);
  await page.goto("/");
  await page.locator("#devMore").click();
  const panel = page.locator("#devicePanel");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("wicked_winds.mp3");
  await expect(panel).toContainText("ghostbusters.mp3");
  await expect(panel).not.toContainText("logs");   // directories are not tracks

  await panel.locator("[data-play]").first().click();
  await expect.poll(() => calls.filter((c) => c.includes("/api/play")).length)
    .toBeGreaterThan(0);
  expect(calls.some((c) => c.includes("f=wicked_winds.mp3"))).toBe(true);
});

test("the boot log is one tap away", async ({ page }) => {
  await stubCastle(page);
  await page.goto("/");
  await page.locator("#devMore").click();
  await page.locator("#dpLog").click();
  await expect(page.locator("#dpLogOut")).toContainText("boot log: 2 lines");
});
