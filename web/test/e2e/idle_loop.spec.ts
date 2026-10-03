/**
 * The frame loop idles when nothing moves (grade report 2026-08-21 G3): after Stop the
 * stage, meters and chrome stop repainting — and a slider wakes them for a
 * frame or two, not forever. window.__castleDraws is main.ts's paint count.
 */

import { test, expect } from "./fixtures.js";

type Page = import("@playwright/test").Page;

const draws = (page: Page): Promise<number> =>
  page.evaluate(() => (window as unknown as { __castleDraws: { frames: number } })
    .__castleDraws.frames);

/** The paint count once it has held still for `quietMs` — retried, not
 *  slept: the loop is allowed to land its settle frames first, and a loop
 *  that never goes quiet fails the assertion rather than a flaky diff.
 *  At 60 fps a running loop would add ~25 frames inside the window. */
async function settled(page: Page, quietMs = 400): Promise<number> {
  let last = 0;
  await expect.poll(async () => {
    const a = await draws(page);
    await new Promise((r) => setTimeout(r, quietMs));
    last = await draws(page);
    return last - a;
  }, { message: "the frame loop never went idle" }).toBe(0);
  return last;
}

test("after Stop the loop stops painting; a slider repaints briefly", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#stage")).toBeVisible();
  await page.locator("#play").click();
  await expect(page.locator("#playLabel")).toHaveText("Pause");
  await expect.poll(() => draws(page)).toBeGreaterThan(5);   // it paints while playing
  await page.locator("#stop").click();
  await expect(page.locator("#playLabel")).toHaveText("Play");
  const idle0 = await settled(page, 500);               // settle frames land, then nothing
  // A slider change must still reach the stage — once or twice, then quiet.
  await page.locator("#depth").fill("20");
  await expect.poll(() => draws(page)).toBeGreaterThan(idle0);
  const woke = await settled(page);
  expect(woke - idle0).toBeLessThanOrEqual(4);
});

const samples = (page: Page): Promise<number> =>
  page.evaluate(() => (window as unknown as { __castleDraws: { ms: number[] } })
    .__castleDraws.ms.length);

test("while a scene runs, the 95th-percentile paint stays under budget", async ({ page }) => {
  // The sample is 60 paints of the running scene — not the page's first
  // paint, which builds the stonework mask — however long the browser takes
  // to make them: the budget is what a paint costs, not how often one comes
  // (main.ts `draws`). Linux WebKit has no GPU in CI and composites the
  // whole page in software at a few frames a second; on 2026-10-03 its 60
  // paints took ~20 s and each still cost ~4 ms. So the wait is sized to the
  // sample rather than to expect's default 10 s.
  test.setTimeout(90_000);
  await page.goto("/");
  await expect(page.locator("#stage")).toBeVisible();
  const before = await samples(page);
  await page.locator("#play").click();
  await expect(page.locator("#playLabel")).toHaveText("Pause");
  await expect.poll(() => samples(page), { timeout: 60_000 })
    .toBeGreaterThanOrEqual(before + 60);
  const p95 = await page.evaluate((from) => {
    const ms = (window as unknown as { __castleDraws: { ms: number[] } })
      .__castleDraws.ms.slice(from).sort((a, b) => a - b);
    return ms[Math.floor(ms.length * 0.95)] ?? 0;
  }, before);
  // One 60 Hz frame is 16.7 ms and the paint (stage, insets, meters,
  // chrome, wave mirror) must fit inside it with room for the browser's
  // own work. Raise this deliberately if the rig grows — the test guards
  // regressions, not the absolute number.
  expect(p95).toBeLessThan(16);
});
