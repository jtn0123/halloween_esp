/**
 * Kiosk mode — the desk with everything but the show taken away.
 *
 * `?kiosk=1` is what a wall tablet on the porch loads: the castle and the 21
 * real pixels, in sync with the hardware, and nothing to press. Nobody looks
 * at this screen while developing, which is exactly why it needs tests — the
 * mode broke silently once already, hiding the console PANELS while leaving
 * their column wrapper in the grid, so a `minmax(320px,1fr)` track went on
 * reserving 40% of the tablet for nothing.
 *
 * What is checked here is layout arithmetic rather than behaviour, because
 * that is what keeps going wrong: the stage has to own the screen, and it has
 * to own it without cropping away its own foundations.
 */

import { test, expect } from "./fixtures.js";

/** The stage's design space (stage.ts). It scales by WIDTH alone, so a box of
 *  any other ratio cuts the castle's base off instead of letterboxing it. */
const STAGE_RATIO = 800 / 520;

const SCREENS = [
  { name: "a 1280 tablet", width: 1280, height: 800 },
  { name: "an iPad in landscape", width: 1024, height: 768 },
  { name: "a 1080p wall display", width: 1920, height: 1080 },
  { name: "a tablet stood on its end", width: 768, height: 1024 },
];

test("kiosk leaves the show and nothing else", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/?kiosk=1");
  await expect(page.locator("#stage")).toBeVisible();
  await expect(page.locator("#jewels")).toBeVisible();

  for (const chrome of ["header", ".foot", "#budget", ".trk-panel", "#scenes"]) {
    await expect(page.locator(chrome)).toBeHidden();
  }
});

for (const screen of SCREENS) {
  test(`the stage fills ${screen.name} whole`, async ({ page }) => {
    await page.setViewportSize({ width: screen.width, height: screen.height });
    await page.goto("/?kiosk=1");
    await expect(page.locator("#stage")).toBeVisible();

    const m = await page.evaluate(() => {
      const stage = document.querySelector(".stage")!.getBoundingClientRect();
      const jewels = document.getElementById("jewels")!.getBoundingClientRect();
      const d = document.documentElement;
      return {
        ratio: stage.width / stage.height,
        jewelsBottom: jewels.bottom,
        viewportHeight: window.innerHeight,
        scrollX: d.scrollWidth - d.clientWidth,
        scrollY: d.scrollHeight - d.clientHeight,
      };
    });

    expect(m.ratio).toBeCloseTo(STAGE_RATIO, 2);
    // The jewel row IS the kiosk — below the fold it may as well not exist,
    // and there is nobody there to scroll a wall tablet.
    expect(m.jewelsBottom).toBeLessThanOrEqual(m.viewportHeight + 1);
    expect(m.scrollX).toBeLessThanOrEqual(0);
    expect(m.scrollY).toBeLessThanOrEqual(0);
  });
}

test("no empty column is left holding a share of the width", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/?kiosk=1");
  const columns = await page.locator(".grid").evaluate(
    el => getComputedStyle(el).gridTemplateColumns.split(" ").length);
  expect(columns).toBe(1);

  const share = await page.locator(".stage").evaluate(
    el => el.getBoundingClientRect().width / window.innerWidth);
  expect(share).toBeGreaterThan(0.7);
});
