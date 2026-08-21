/**
 * The frame loop idles when nothing moves (grade report G3): after Stop the
 * stage, meters and chrome stop repainting — and a slider wakes them for a
 * frame or two, not forever. window.__castleDraws is main.ts's paint count.
 */

import { test, expect } from "./fixtures.js";

const draws = (page: import("@playwright/test").Page): Promise<number> =>
  page.evaluate(() => (window as unknown as { __castleDraws: { frames: number } })
    .__castleDraws.frames);

test("after Stop the loop stops painting; a slider repaints briefly", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#stage")).toBeVisible();
  await page.locator("#play").click();
  await expect(page.locator("#playLabel")).toHaveText("Pause");
  await page.waitForTimeout(300);
  const running = await draws(page);
  expect(running).toBeGreaterThan(5);                  // it paints while playing
  await page.locator("#stop").click();
  await expect(page.locator("#playLabel")).toHaveText("Play");
  await page.waitForTimeout(400);                       // settle frames land
  const idle0 = await draws(page);
  await page.waitForTimeout(500);
  expect(await draws(page)).toBe(idle0);                // half a second: nothing
  // A slider change must still reach the stage — once or twice, then quiet.
  await page.locator("#depth").fill("20");
  await page.waitForTimeout(300);
  const woke = await draws(page);
  expect(woke).toBeGreaterThan(idle0);
  expect(woke - idle0).toBeLessThanOrEqual(4);
  await page.waitForTimeout(400);
  expect(await draws(page)).toBe(woke);
});
