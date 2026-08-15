/**
 * The desk itself — transport, cue sheet, scene list, import options.
 *
 * The theme running through these: the page should never surprise you. It
 * should not make noise, it should not push its own panels off the screen,
 * and a control that has no meaning should not accept a value.
 */

import { test, expect, sounding } from "./fixtures.js";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#stage")).toBeVisible();
});

test("the show starts muted and stopped", async ({ page }) => {
  await expect(page.locator("#mute")).toHaveText("Muted");
  await expect(page.locator("#playLabel")).toHaveText("Play");
  expect(await sounding(page)).toBe(0);
});

test("play runs the clock, and stop blacks it out", async ({ page }) => {
  await page.locator("#play").click();
  await expect(page.locator("#playLabel")).toHaveText("Pause");
  await expect.poll(async () => {
    const tc = await page.locator("#tc").textContent();
    return Number.parseFloat(tc ?? "0");
  }).toBeGreaterThan(0.2);

  await page.locator("#stop").click();
  await expect(page.locator("#playLabel")).toHaveText("Play");
  await expect(page.locator("#tc")).toContainText("0.00 /");
});

test("the cue sheet is collapsed, counted and capped", async ({ page }) => {
  // A four-minute import generates a couple of thousand cues; rendering them
  // inline pushed the whole console off the bottom of the page.
  const fold = page.locator("#sheetFold");
  await expect(fold).not.toHaveAttribute("open", /.*/);
  await expect(page.locator("#sheetCount")).toHaveText(/(none|\d+ cues?, \d+ light)/);

  await fold.locator("summary").click();
  await expect(page.locator("#sheetWrap")).toBeVisible();
  const capped = await page.locator("#sheetWrap").evaluate(el =>
    el.scrollHeight <= el.clientHeight || el.clientHeight <= 340);
  expect(capped).toBe(true);
});

test("scenes carry their size and total against the flash budget",
  async ({ page }) => {
    await expect(page.locator("#sceneCount")).toHaveText(/\d+ loaded/);
    const first = page.locator(".scene").first();
    await expect(first.locator(".scene__sz")).toHaveText(/\d+s/);
    // The budget line only appears once something has been rendered, which is
    // the normal state of a built checkout.
    const text = await page.locator("#sceneCount").textContent();
    if (/flash/.test(text ?? "")) {
      expect(text).toMatch(/of [\d.]+ MB flash \(\d+%\)/);
    }
  });

test("picking a scene loads it without starting audio", async ({ page }) => {
  const scenes = page.locator(".scene");
  const n = await scenes.count();
  test.skip(n < 2, "needs at least two scenes");
  await scenes.nth(1).click();
  await expect(scenes.nth(1)).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#playLabel")).toHaveText("Play");
  expect(await sounding(page)).toBe(0);
});

test("the import options summarise themselves while collapsed", async ({ page }) => {
  const opts = page.locator("#trkOpts");
  await expect(opts).not.toHaveAttribute("open", /.*/);
  await expect(page.locator("#trkOptsHint")).toHaveText(/MP3 \d+k/);

  await opts.locator("summary").click();
  await page.locator("#trkTake").fill("24");
  await page.locator("#trkStart").fill("0:30");
  // "stereo" is in the hint because stereo is now the default channel count.
  await expect(page.locator("#trkOptsHint")).toHaveText("— 24s from 0:30, MP3 96k, stereo");
});

test("bitrate is refused for the containers that have none", async ({ page }) => {
  await page.locator("#trkOpts").locator("summary").click();
  const bitrate = page.locator("#trkBitrate");
  await expect(bitrate).toBeEnabled();

  for (const fmt of ["wav", "flac"]) {
    await page.locator("#trkFormat").selectOption(fmt);
    await expect(bitrate).toBeDisabled();
    await expect(page.locator("#trkOptsHint")).toHaveText(`— ${fmt.toUpperCase()}, stereo`);
  }
  await page.locator("#trkFormat").selectOption("mp3");
  await expect(bitrate).toBeEnabled();
});

test("an out-of-range bitrate never renders negative time", async ({ page }) => {
  // The bug: -5 is truthy, so `+raw || 96` let it through and the capacity
  // readout formatted it as "-47:-47".
  await page.locator("#trkOpts").locator("summary").click();
  for (const bad of ["-5", "0", "9999"]) {
    await page.locator("#trkBitrate").fill(bad);
    await expect(page.locator("#trkCap")).not.toContainText("-");
  }
});
