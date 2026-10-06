/**
 * The frame loop idles when nothing moves (grade report 2026-08-21 G3): after Stop the
 * stage, meters and chrome stop repainting — and a slider wakes them for a
 * frame or two, not forever. window.__castleDraws is main.ts's paint count.
 *
 * Every wait here is counted in the page's own animation frames, never in
 * wall-clock time. How often a frame comes is the browser's business, not
 * the loop's: WebKit on Linux composites the whole page in software, and in
 * the Playwright container held to half a CPU (2026-10-06) it made one frame
 * of the running show every ~4 s. The old test asked for six paints inside
 * expect's 10 s, which measured the compositor rather than the loop and
 * failed there 3 runs in 9 (and 4 in 30 on 2026-10-03); and it took "no
 * paint for 500 ms" for quiet, which a browser four seconds into its next
 * frame passes as well. Here a running show must paint every frame it is
 * given, and quiet is five frames in a row that painted nothing.
 */

import { test, expect } from "./fixtures.js";

type Page = import("@playwright/test").Page;

type Clock = { frames: number; clicks: Record<string, [frame: number, paints: number]> };

/** The page's own frame count, and — at every button click, in the capture
 *  phase, before the desk has handled it — that count beside main.ts's paint
 *  count. Installed before the desk's scripts. Both counts move only inside a
 *  frame and a click lands between frames, so between two clicks the
 *  difference is exact: how many frames ran, and how many of them painted. */
const clockScript = (): void => {
  const w = window as unknown as { __clock: Clock; __castleDraws?: { frames: number } };
  const clock: Clock = { frames: 0, clicks: {} };
  w.__clock = clock;
  const tick = (): void => { clock.frames++; requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
  addEventListener("click", (e) => {
    const id = (e.target as Element | null)?.closest("button")?.id;
    if (id) clock.clicks[id] = [clock.frames, w.__castleDraws?.frames ?? 0];
  }, true);
};

/** Wait `n` animation frames. */
const frames = (page: Page, n: number): Promise<void> =>
  page.evaluate((count) => new Promise<void>((done) => {
    let left = count;
    const tick = (): void => { if (--left > 0) requestAnimationFrame(tick); else done(); };
    requestAnimationFrame(tick);
  }), n);

/** main.ts's paint count once `quiet` frames in a row have painted nothing —
 *  so the settle frame after the last change has landed first — or -1 when
 *  the loop is still painting `limit` frames on. A loop that has stopped
 *  paints nothing, so five quiet frames in a row is one that has. */
const quietAt = (page: Page, quiet = 5, limit = 240): Promise<number> =>
  page.evaluate(([q, lim]) => new Promise<number>((done) => {
    const d = (window as unknown as { __castleDraws: { frames: number } }).__castleDraws;
    let last = d.frames;
    let still = 0;
    let seen = 0;
    const tick = (): void => {
      seen++;
      if (d.frames === last) still++;
      else { still = 0; last = d.frames; }
      if (still >= q) done(last);
      else if (seen >= lim) done(-1);
      else requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }), [quiet, limit] as const);

test("a running show paints every frame; after Stop the loop stops painting", async ({ page }) => {
  // Sized to the frames, as the paint-budget test below is to its sample:
  // this sits through about eight painted ones (the load's, three of the
  // running show, Stop's two), and the half-CPU container took ~3.5 s over
  // each — 23 s on average and 28.5 s at worst in 30 runs (2026-10-03),
  // against the default 30 s. Nothing here is asserted against the clock.
  test.setTimeout(60_000);
  await page.addInitScript(clockScript);
  await page.goto("/");
  await expect(page.locator("#stage")).toBeVisible();
  await page.locator("#play").click();
  await expect(page.locator("#playLabel")).toHaveText("Pause");
  // One frame of the running show, then Stop — whose click waits two more
  // for the button to hold still (Playwright's actionability check), so at
  // least three frames run between the two clicks without the test sitting
  // through any twice. Every one of them must paint: the first for Play's
  // click (dirty), the second as its settle frame, and the third only because
  // the show is running. vigil loops, so it still is.
  await frames(page, 1);
  await page.locator("#stop").click();
  await expect(page.locator("#playLabel")).toHaveText("Play");
  const run = await page.evaluate(() => {
    const none: [number, number] = [0, 0];
    const { play = none, stop = none } = (window as unknown as { __clock: Clock }).__clock.clicks;
    return { frames: stop[0] - play[0], paints: stop[1] - play[1] };
  });
  expect(run.frames, "fewer than three frames between Play and Stop").toBeGreaterThanOrEqual(3);
  expect(run.paints, "a running show paints every frame").toBe(run.frames);
  expect(await quietAt(page), "the frame loop never went idle after Stop").not.toBe(-1);
});

test("a slider wakes a stopped desk for a paint or two, then it idles again", async ({ page }) => {
  // On a desk that has not played: main.ts's rule for a stopped desk is the
  // same either way (`active` is false), and behind a Play and a Stop this
  // also sat through every painted frame of the test above — at seconds
  // apiece on a starved software compositor, against one test's 30 s.
  await page.goto("/");
  await expect(page.locator("#stage")).toBeVisible();
  const idle0 = await quietAt(page);
  expect(idle0, "the loaded desk never went idle").not.toBe(-1);
  // Counted against idle0, not "the next frame", so it holds however many
  // frames pass between the fill and the count.
  await page.locator("#depth").fill("20");
  const woke = await quietAt(page);
  expect(woke, "the frame loop never went idle after the slider").not.toBe(-1);
  expect(woke - idle0, "the slider never reached the stage").toBeGreaterThanOrEqual(1);
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
