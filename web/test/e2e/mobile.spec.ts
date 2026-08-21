/**
 * The desk on a phone — 375x812, the smallest thing that will realistically
 * open http://castle/ on the porch.
 *
 * The invariant that matters: the PAGE never scrolls sideways. Anything wide
 * (cue sheet, YAML source) scrolls inside its own box instead. Everything
 * else here is tap ergonomics and the device chrome staying inside the
 * viewport.
 */

import { test, expect, fakeCastle } from "./fixtures.js";
import { MP3_ID } from "./global-setup.js";

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

/* ── 44px everywhere a thumb goes (judge B, JB1-7) ────────────────────── */

/** Smallest height among the matched, visible controls. */
const minHeight = (page: import("@playwright/test").Page, sel: string): Promise<number> =>
  page.evaluate((s) => Math.min(...Array.from(document.querySelectorAll<HTMLElement>(s))
    .filter((el) => el.getClientRects().length)
    .map((el) => el.getBoundingClientRect().height)), sel);

test("chip, castle panel, selects and tabs meet the 44px floor", async ({ page }) => {
  await fakeCastle(page, [{ name: "phantom_waltz.mp3", size: 512000, dir: false }]);
  await page.goto("/");
  await expect(page.locator("#deviceChip")).toBeVisible();
  expect(await minHeight(page, "#deviceChip .chip__btn")).toBeGreaterThanOrEqual(44);
  await page.locator("#devMore").click();
  await expect(page.locator("#devicePanel")).toBeVisible();
  expect(await minHeight(page, "#devicePanel button, #devicePanel select")).toBeGreaterThanOrEqual(44);
  expect(await minHeight(page, "#budTabs button")).toBeGreaterThanOrEqual(44);
  expect(await minHeight(page, "#rigPanel select")).toBeGreaterThanOrEqual(44);
  expect(await minHeight(page, ".viewsel button")).toBeGreaterThanOrEqual(44);
});

test("the clip editor's band rows stack instead of clipping", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#trkMode")).toHaveText(/studio/);
  await page.locator(`.trk[data-id="${MP3_ID}"] .trk__nm`).click();
  await expect(page.locator(".bandcfg")).toBeVisible();
  expect(await minHeight(page, ".bandcfg__zone, .bandcfg__mute, .bandcfg__solo"))
    .toBeGreaterThanOrEqual(44);
  const m = await page.evaluate(() => {
    const box = document.querySelector<HTMLElement>(".bandcfg")!;
    const hits = document.querySelector<HTMLElement>(".bandcfg__hits")!;
    return {
      over: box.scrollWidth - box.clientWidth,
      hitsVisible: hits.getBoundingClientRect().right <= box.getBoundingClientRect().right + 1,
      pageOver: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    };
  });
  expect(m.over).toBeLessThanOrEqual(0);
  expect(m.hitsVisible).toBe(true);
  expect(m.pageOver).toBeLessThanOrEqual(0);
  // The flavour note is a paragraph, not a one-word-per-line column.
  const hint = page.locator(".stylelab__flavhint");
  await expect(hint).toBeVisible();
  const hb = (await hint.boundingBox())!;
  expect(hb.width).toBeGreaterThan(250);
  expect(hb.height).toBeLessThan(90);
});

test("no caption drops under 12px on a phone", async ({ page }) => {
  await fakeCastle(page);
  await page.goto("/");
  await expect(page.locator("#deviceChip")).toBeVisible();
  await page.locator(`.trk[data-id="${MP3_ID}"] .trk__nm`).click();
  await expect(page.locator(".bandcfg")).toBeVisible();
  const small = await page.evaluate(() => {
    const out: string[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (!n.textContent?.trim()) continue;
      const el = n.parentElement!;
      if (!el.getClientRects().length) continue;
      if (el.closest("script, style, #stageShell canvas")) continue;
      const px = parseFloat(getComputedStyle(el).fontSize);
      if (px < 12) out.push(`${el.tagName.toLowerCase()}.${String(el.className).split(" ")[0]} ${px}px "${n.textContent.trim().slice(0, 20)}"`);
    }
    return out;
  });
  expect(small).toEqual([]);
});
