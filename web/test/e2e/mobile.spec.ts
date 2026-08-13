/**
 * The desk on a phone — 375x812, the smallest thing that will realistically
 * open http://castle/ on the porch.
 *
 * The invariant that matters: the PAGE never scrolls sideways. Anything wide
 * (cue sheet, YAML source) scrolls inside its own box instead. Everything
 * else here is tap ergonomics and the device chrome staying inside the
 * viewport.
 */

import { test, expect } from "./fixtures.js";

test.use({ viewport: { width: 375, height: 812 } });

const STATUS = {
  version: "5.9", uptime_s: 60, sd_mounted: true, psram_free_kb: 1500,
  heap_free_kb: 70, volume: 45, scene: "vigil", track: "01_vigil",
  pir: { armed: true, cooldown_s: 60, scene: "approach" },
};

test("no horizontal overflow at phone width", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#stage")).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test("scene buttons meet the 44px tap floor", async ({ page }) => {
  await page.goto("/");
  const box = await page.locator("button.scene").first().boundingBox();
  expect(box).not.toBeNull();
  expect(box!.height).toBeGreaterThanOrEqual(44);
});

test("the device chip fits the viewport", async ({ page }) => {
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/status") return route.fulfill({ json: STATUS });
    if (url.pathname === "/api/files") return route.fulfill({ json: [] });
    return route.fulfill({ json: { queued: true } });
  });
  await page.goto("/");
  const chip = page.locator("#deviceChip");
  await expect(chip).toBeVisible();
  const box = await chip.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(375);
});

test("the cue sheet scrolls inside its own box, not the page", async ({ page }) => {
  await page.goto("/");
  await page.locator("#sheetFold summary").click();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
