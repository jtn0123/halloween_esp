/**
 * The stems panel — the voice/background split audit inside the clip editor.
 *
 * The real Demucs run is a 25-second GPU job, so the ready state is mocked at
 * the network edge; what these tests pin is the wiring around it: the unsplit
 * state offers the split, the ready state draws three layers, and the channel
 * selector actually changes the caption from "the pipeline's picture" to a
 * count of what the mono analysis missed.
 */

import { type Page } from "@playwright/test";
import { test, expect } from "./fixtures.js";

const MP3 = "e2e_beats";

const openEditor = async (page: Page): Promise<void> => {
  await page.locator(`.trk[data-id="${MP3}"]`).click();
  await expect(page.locator("#trkWave")).toBeVisible();
};

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#trkMode")).toHaveText(/studio/);
  await expect(page.locator(`.trk[data-id="${MP3}"]`)).toBeVisible();
});

test("an unsplit track offers the split and promises the castle is untouched",
    async ({ page }) => {
  await openEditor(page);
  const panel = page.locator("#trkWave .stems");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("Not split yet");
  // The sentence that stops "will this change what the device plays?" from
  // ever needing to be asked.
  await expect(panel).toContainText("combined file");
  await expect(panel.getByRole("button", { name: "Split voices" })).toBeEnabled();
});

/** A ready analysis: one low hit at 1 s everywhere, plus a hit at 5 s that
 *  exists ONLY in the left channel — the thing the mono pipeline missed. */
const chan = (times: number[]) => ({
  peaks: Array.from({ length: 700 }, (_, i) => (i % 7 ? 0.2 : 0.9)),
  level: 0.9,
  onsets: { onset_low: times.map(t => [t, 1]) },
});
const READY = {
  ok: true, id: MP3, duration: 10, stale: false,
  layers: Object.fromEntries(["vocals", "backing", "combined"].map(l => [l, {
    left: chan([1, 5]), right: chan([1]), both: chan([1]),
  }])),
};

test("the ready panel draws three layers and Left exposes what mono missed",
    async ({ page }) => {
  await page.route(`**/api/stems/${MP3}`, r =>
    r.fulfill({ json: READY }));
  await openEditor(page);

  const panel = page.locator("#trkWave .stems");
  await expect(panel.locator(".stems-row")).toHaveCount(3);
  await expect(panel).toContainText("Voice");
  await expect(panel).toContainText("Background");
  await expect(panel).toContainText("Combined");
  // Default view is the pipeline's own mono picture.
  await expect(panel.locator(".stems-cap").first())
    .toContainText("the pipeline's own picture");

  await panel.getByRole("button", { name: "Left", exact: true }).click();
  await expect(panel.locator(".stems-cap").first())
    .toContainText("1 the mono analysis missed");
  await panel.getByRole("button", { name: "Right", exact: true }).click();
  await expect(panel.locator(".stems-cap").first())
    .toContainText("all seen by mono analysis");
});

test("L / R stacks the channels and counts one-sided hits", async ({ page }) => {
  await page.route(`**/api/stems/${MP3}`, r =>
    r.fulfill({ json: READY }));
  await openEditor(page);
  const panel = page.locator("#trkWave .stems");
  await panel.getByRole("button", { name: "L / R" }).click();
  // The fixture's 5 s hit lives only in the left channel.
  await expect(panel.locator(".stems-cap").first())
    .toContainText("1 left-only · 0 right-only");
  // The stacked strips get taller to hold the two mirrored halves.
  const box = await panel.locator(".stems-strip").first().boundingBox();
  expect(box!.height).toBeGreaterThan(70);
});

test("a stale split says so instead of quietly showing old audio",
    async ({ page }) => {
  await page.route(`**/api/stems/${MP3}`, r =>
    r.fulfill({ json: { ...READY, stale: true } }));
  await openEditor(page);
  await expect(page.locator("#trkWave .stems")).toContainText("re-imported");
  await expect(page.locator("#trkWave .stems"))
    .toContainText(/Re-split/i);
});
