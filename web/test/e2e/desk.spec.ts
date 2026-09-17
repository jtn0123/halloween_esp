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

test("scenes carry their size and their own colour", async ({ page }) => {
  await expect(page.locator("#sceneCount")).toHaveText(/\d+ loaded/);
  const first = page.locator(".scene").first();
  await expect(first.locator(".scene__sz")).toHaveText(/\d+s/);
  // The tile's bar is sampled from the effects the scene runs (scene_tint.ts).
  // Two scenes sharing a base still have to come out different, or the grid is
  // eight grey tiles again and you are back to reading the words.
  const tints = await page.locator(".scene__tint").evaluateAll(
    els => els.map(e => (e as HTMLElement).style.background));
  expect(tints.length).toBeGreaterThan(1);
  expect(tints.every(t => t !== "")).toBe(true);
  expect(new Set(tints).size).toBeGreaterThan(1);
});

test("the budget card opens on the card and keeps the flash counterfactual",
  async ({ page }) => {
  // What used to be a parenthesised percentage next to the scene count. It
  // opens on the CARD since 2026-09-01 (PROJECT_NOTES §12.15): the card is
  // the castle, and the flash view is the retired build's arithmetic kept as
  // the reason the card exists. It used to be the other way round.
  await expect(page.locator("#budHead")).toContainText("of 32.00 GB");
  await expect(page.locator("#budRows")).toContainText("/sd/scenes");
  await expect(page.locator("#budNote")).toContainText("make publish");
  await expect(page.locator("#budNote")).not.toContainText("Experimental");
  await expect(page.locator("#budPick")).toBeHidden();

  // Every band is the scene behind it, and so is every legend row — on the
  // card view the used bands are hairlines, so the rows have to select too.
  await page.locator("#budRows .budget__row").nth(1).click();
  await expect(page.locator("#budPick")).toBeVisible();
  await expect(page.locator(".budget__pickhd b")).toHaveText(/[\d.]+ [KMG]B · /);

  await page.locator('#budTabs button[data-bud="flash"]').click();
  await expect(page.locator("#budHead"))
    .toContainText(/of 3\.88 MB · \d+%/);
  // And it says outright that it is not a build any more, so nobody reads
  // the "over budget" it now shows as something to go and fix.
  await expect(page.locator("#budNote")).toContainText("Not a build any more");
  // The selection does not survive the switch — the keys mean different things.
  await expect(page.locator("#budPick")).toBeHidden();
});

test("the stage view toggle shows the castle, the pixels, or both",
  async ({ page }) => {
    const stage = page.locator(".stage");
    const jewels = page.locator("#jewels");
    await expect(stage).toBeVisible();
    await expect(jewels).toBeVisible();

    await page.locator('#viewSel button[data-view="pixels"]').click();
    await expect(stage).toBeHidden();
    await expect(jewels).toBeVisible();

    await page.locator('#viewSel button[data-view="castle"]').click();
    await expect(stage).toBeVisible();
    await expect(jewels).toBeHidden();

    // The choice is remembered, the way the trim sliders are.
    await page.reload();
    await expect(page.locator("#jewels")).toBeHidden();
    await page.locator('#viewSel button[data-view="both"]').click();
  });

test("the masthead says which machine is listening and what it is doing",
  async ({ page }) => {
    // No castle answers /api/status in the test rig, so the desk is honest
    // about being a simulator — and it starts muted, always.
    await expect(page.locator("#headTxt")).toHaveText(/^simulator · .* · muted$/);
    await page.locator("#mute").click();
    await expect(page.locator("#headTxt")).toContainText("sounding");
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
  // "stereo" and "loudness matched" are in the hint because both are now
  // the defaults.
  await expect(page.locator("#trkOptsHint"))
    .toHaveText("— 24s from 0:30, MP3 96k, stereo, loudness matched");
});

test("bitrate is refused for the containers that have none", async ({ page }) => {
  await page.locator("#trkOpts").locator("summary").click();
  const bitrate = page.locator("#trkBitrate");
  await expect(bitrate).toBeEnabled();

  for (const fmt of ["wav", "flac"]) {
    await page.locator("#trkFormat").selectOption(fmt);
    await expect(bitrate).toBeDisabled();
    await expect(page.locator("#trkOptsHint"))
      .toHaveText(`— ${fmt.toUpperCase()}, stereo, loudness matched`);
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
