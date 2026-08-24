/**
 * Accessibility and layout discipline: every control can be named, the
 * transport can be walked with Tab, a row's own button keeps focus while
 * its label changes, and the page never scrolls sideways — at phone, tablet
 * and desk widths, with the castle dock and panel open.
 */

import { test, expect, fakeCastle } from "./fixtures.js";
import { MP3_ID } from "./global-setup.js";

/** Visible interactive elements that no assistive tech could name. */
const UNNAMED = `(() => {
  const seen = [];
  const visible = (el) => !!(el.offsetParent || el.getClientRects().length)
    && getComputedStyle(el).visibility !== "hidden";
  const nameOf = (el) => {
    const aria = el.getAttribute("aria-label");
    if (aria && aria.trim()) return aria;
    const by = el.getAttribute("aria-labelledby");
    if (by && document.getElementById(by)?.textContent?.trim()) return by;
    if (el.title && el.title.trim()) return el.title;
    if (el.id) {
      const lab = document.querySelector('label[for="' + el.id + '"]');
      if (lab && lab.textContent.trim()) return lab.textContent;
    }
    const wrap = el.closest("label");
    if (wrap && wrap.textContent.trim()) return wrap.textContent;
    if (el.placeholder && el.placeholder.trim()) return el.placeholder;
    if (/^(button|a|summary)$/i.test(el.tagName) && el.textContent.trim()) return el.textContent;
    if (el.tagName === "SELECT" && el.options.length && el.options[0].text.trim()) return "";
    return "";
  };
  for (const el of document.querySelectorAll(
      "button, a[href], input:not([type=hidden]), select, textarea, summary, [role=slider], [role=button]")) {
    if (!visible(el)) continue;
    if (!nameOf(el)) {
      const tag = el.tagName.toLowerCase();
      seen.push(tag + (el.id ? "#" + el.id : "") + (el.className ? "." + String(el.className).split(" ")[0] : "")
        + (el.dataset.act ? "[data-act=" + el.dataset.act + "]" : "")
        + (el.dataset.cardact ? "[data-cardact=" + el.dataset.cardact + "]" : ""));
    }
  }
  return seen;
})()`;

test("every visible control has an accessible name — desk, chip and panel", async ({ page }) => {
  await fakeCastle(page, [{ name: "phantom_waltz.mp3", size: 512000, dir: false }]);
  await page.goto("/");
  await expect(page.locator("#deviceChip")).toBeVisible();
  await expect(page.locator(`.trk[data-id="${MP3_ID}"]`)).toBeVisible();
  await expect(page.locator(".trk--card")).toHaveCount(1);
  expect(await page.evaluate(UNNAMED)).toEqual([]);

  await page.locator("#devMore").click();
  await expect(page.locator("#devicePanel")).toBeVisible();
  await page.locator("#sheetFold summary").click();
  await page.locator("#trkOpts summary").click();
  await page.locator(`.trk[data-id="${MP3_ID}"] .trk__nm`).click();
  await expect(page.locator("#trkWave")).toBeVisible();
  expect(await page.evaluate(UNNAMED)).toEqual([]);
});

test("toasts and the masthead line are live regions", async ({ page }) => {
  await fakeCastle(page);
  await page.goto("/");
  await expect(page.locator("#deviceChip")).toBeVisible();
  // The masthead's status line is the one line that changes under a screen
  // reader's feet without a button press of its own.
  await expect(page.locator("#headTxt")).toHaveAttribute("aria-live", "polite");
  await page.locator("#devStop").click();                  // a success toast
  const host = page.locator("#toasts");
  await expect(host).toHaveAttribute("role", "status");
  await expect(host).toHaveAttribute("aria-live", "polite");
  await expect(host.locator(".toast").first()).toBeVisible();
  // A failure interrupts: the castle refuses, the toast is an alert.
  await page.route("**/api/stop", (route) =>
    route.fulfill({ status: 502, body: "castle not reachable" }));
  await page.locator("#devStop").click();
  await expect(host.locator(".toast--err").first()).toHaveAttribute("role", "alert");
});

test("Tab walks the transport in reading order, ♪ switch included", async ({ page }) => {
  await fakeCastle(page);
  await page.goto("/");
  await expect(page.locator(".transport #sndRoute")).toBeVisible();
  await page.locator("#play").focus();
  const order: string[] = [];
  for (let i = 0; i < 6; i++) {
    order.push(await page.evaluate(() => document.activeElement?.id ?? ""));
    await page.keyboard.press("Tab");
  }
  expect(order.slice(0, 6)).toEqual(["play", "restart", "stop", "mute", "sndRoute", "scrub"]);
  // Space on the focused scrub must not start the show; Space on Play does.
  await page.keyboard.press("Space");
  await expect(page.locator("#playLabel")).toHaveText("Pause");   // the global key, by design
  await page.keyboard.press("Escape");
  await expect(page.locator("#playLabel")).toHaveText("Play");
});

test("the play button on a row keeps focus while its label flips", async ({ page }) => {
  await page.goto("/");
  const btn = page.locator(`.trk[data-id="${MP3_ID}"] button[data-act='play']`);
  await btn.focus();
  await page.keyboard.press("Enter");
  await expect(btn).toHaveText("Stop");
  expect(await page.evaluate(() => document.activeElement?.textContent)).toBe("Stop");
  await page.keyboard.press("Enter");
  await expect(btn).toHaveText("Play");
  expect(await page.evaluate(() => document.activeElement?.textContent)).toBe("Play");
});

for (const width of [375, 768, 1280]) {
  test(`nothing overflows sideways at ${width}px with the dock open`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await fakeCastle(page, [{ name: "a_rather_long_file_name_for_the_card_only_row.mp3",
                              size: 512000, dir: false }]);
    await page.goto("/");
    await expect(page.locator("#deviceChip")).toBeVisible();
    await page.locator("#devMore").click();
    await expect(page.locator("#devicePanel")).toBeVisible();
    await page.locator("#sheetFold summary").click();
    await page.locator("#devStop").click();                 // a toast too
    await expect(page.locator("#toasts > div").first()).toBeVisible();
    const m = await page.evaluate(() => {
      const d = document.documentElement;
      const dock = document.getElementById("castleDock")!.getBoundingClientRect();
      return { over: d.scrollWidth - d.clientWidth,
               dockRight: dock.right, dockLeft: dock.left, vw: window.innerWidth };
    });
    expect(m.over).toBeLessThanOrEqual(0);
    expect(m.dockLeft).toBeGreaterThanOrEqual(0);
    expect(m.dockRight).toBeLessThanOrEqual(m.vw + 1);
  });
}
