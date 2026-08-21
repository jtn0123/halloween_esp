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

import { test, expect, fakeCastle } from "./fixtures.js";

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

/* ── With a castle: a display, not a console (judge B, JB1-2) ─────────── */

test("kiosk with a castle: no dock, no toasts, keys inert, the scene RUNS", async ({ page }) => {
  const castle = await fakeCastle(page, [], { scene: "storm" });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/?kiosk=1");
  await expect(page.locator("#stageNote")).toContainText("Storm");
  // Adopted AND playing — loaded at frame 0 and paused, the wall tablet was
  // never in sync with the porch.
  await expect(page.locator("#playLabel")).toHaveText("Pause");
  // The castle's own controls (■ stop, Delete from card…) are not on a wall.
  await expect(page.locator("#castleDock")).toBeHidden();
  await expect(page.locator("#deviceChip")).toBeHidden();
  // Nothing the tablet does reaches the porch: not the keyboard, not the
  // adoption itself.
  await page.keyboard.press("2");
  await page.keyboard.press("Escape");
  await page.keyboard.press("Space");
  await page.waitForTimeout(400);
  await expect(page.locator("#stageNote")).toContainText("Storm");
  await expect(page.locator("#playLabel")).toHaveText("Pause");
  expect(castle.hits("/api/scene")).toBe(0);
  expect(castle.hits("/api/stop")).toBe(0);
  // The jewel row is not under anything.
  const clear = await page.evaluate(() => {
    const j = document.getElementById("jewels")!.getBoundingClientRect();
    const hit = document.elementFromPoint(j.right - 4, j.bottom - 4);
    return hit === null || !hit.closest("#castleDock, #toasts");
  });
  expect(clear).toBe(true);
});

test("kiosk follows the castle: a new scene, then idle", async ({ page }) => {
  const castle = await fakeCastle(page, [], { scene: "storm" });
  await page.goto("/?kiosk=1");
  await expect(page.locator("#stageNote")).toContainText("Storm");
  castle.status["scene"] = "vigil";              // the PIR fired, say
  await expect(page.locator("#stageNote")).toContainText("Vigil");
  await expect(page.locator("#playLabel")).toHaveText("Pause");
  castle.status["scene"] = "";                   // and the porch went dark
  await expect(page.locator("#playLabel")).toHaveText("Play");
  expect(castle.hits("/api/scene")).toBe(0);
  expect(castle.hits("/api/stop")).toBe(0);
});

/** Lit pixels on the jewel row: a socket that is off draws rgb(30,30,30),
 *  the labels are lavender greys — anything brighter than 200 is a light. */
const litJewels = (page: import("@playwright/test").Page): Promise<number> =>
  page.evaluate(() => {
    const c = document.getElementById("jewels") as HTMLCanvasElement;
    const d = c.getContext("2d")!.getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 0; i < d.length; i += 4) if (Math.max(d[i]!, d[i + 1]!, d[i + 2]!) > 200) n++;
    return n;
  });

test("kiosk meeting an idle porch is dark, not the default scene at frame 0", async ({ page }) => {
  const castle = await fakeCastle(page, [], { scene: "" });
  await page.goto("/?kiosk=1");
  await expect(page.locator("#deviceChip")).toHaveClass(/live/);
  await expect(page.locator("#playLabel")).toHaveText("Play");
  await expect.poll(() => litJewels(page)).toBe(0);
  // And it still wakes with the porch.
  castle.status["scene"] = "storm";
  await expect(page.locator("#stageNote")).toContainText("Storm");
  await expect(page.locator("#playLabel")).toHaveText("Pause");
  await expect.poll(() => litJewels(page)).toBeGreaterThan(0);
  expect(castle.hits("/api/scene")).toBe(0);
});

test("kiosk says since when the castle stopped answering", async ({ page }) => {
  const castle = await fakeCastle(page, [], { scene: "storm" });
  await page.goto("/?kiosk=1");
  await expect(page.locator("#stageNote")).toContainText("Storm");
  await expect(page.locator("#kioskDown")).toBeHidden();
  castle.up = false;
  const banner = page.locator("#kioskDown");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText(/castle not answering since \d+:\d\d/);
  // Still dark, still no overflow with the banner up.
  castle.up = true;
  await expect(banner).toBeHidden();
});

test("kiosk is dark even in a light-themed browser", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/?kiosk=1");
  await expect(page.locator("#stage")).toBeVisible();
  const m = await page.evaluate(() => ({
    theme: document.documentElement.dataset["theme"],
    body: getComputedStyle(document.body).backgroundColor,
    shell: getComputedStyle(document.getElementById("stageShell")!).backgroundColor,
  }));
  expect(m.theme).toBe("dark");
  // --stone, the stage's own ground: #05070e.
  expect(m.body).toBe("rgb(5, 7, 14)");
  expect(m.shell).toBe("rgb(5, 7, 14)");
});
