/**
 * The Tracks panel, end to end.
 *
 * Every assertion here corresponds to something that actually shipped broken:
 * a Play button that did not exist, a WAV import that vanished from the list,
 * a "Make scene" that gave no sign of working, an editor nothing told you was
 * there. Unit tests could not have caught any of them — they are all wiring.
 */

import { type Page } from "@playwright/test";
import { test, expect, playing, sounding } from "./fixtures.js";

const MP3 = "e2e_beats";
const WAV = "e2e_lossless";
const DOOMED = "e2e_doomed";

const row = (page: Page, id: string) => page.locator(`.trk[data-id="${id}"]`);
const act = (page: Page, id: string, action: string) =>
  row(page, id).locator(`button[data-act="${action}"]`);

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  // The panel decides studio-vs-static from a fetch, so wait for the verdict
  // rather than for a timeout.
  await expect(page.locator("#trkMode")).toHaveText(/studio/);
  await expect(row(page, MP3)).toBeVisible();
});

test("nothing makes a sound until asked", async ({ page }) => {
  // The contract the whole app is built on. Asserted as the property — "no
  // player is both running and unmuted" — over every element the page has
  // made, including the detached ones. See fixtures.ts.
  expect(await sounding(page)).toBe(0);
  await expect(page.locator("#mute")).toHaveText("Muted");
});

test("a track lists its container, size and pulse rate", async ({ page }) => {
  const meta = row(page, MP3).locator("small").first();
  await expect(meta).toContainText("MP3");
  await expect(meta).toContainText("KB");
  // Rate per zone, not raw counts — the counts were the wrong number to show.
  await expect(row(page, MP3).locator("small").nth(1))
    .toHaveText(/(door|towerL|towerR) [\d.]+\/s/);
});

test("a WAV import is listed, typed and playable", async ({ page }) => {
  // The regression this exists for: globbing "*.mp3" made non-MP3 imports
  // land on disk and never appear.
  await expect(row(page, WAV)).toBeVisible();
  await expect(row(page, WAV).locator("small").first()).toContainText("WAV");
  // No bitrate for a lossless container; "?kbps" reads as a failed import.
  await expect(row(page, WAV).locator("small").first()).not.toContainText("kbps");

  const res = await page.request.get(`/api/track/${WAV}`);
  expect(res.status()).toBe(200);
  expect(res.headers()["content-type"]).toBe("audio/wav");
});

test("Play starts the track and Stop stops it", async ({ page }) => {
  await act(page, MP3, "play").click();
  await expect(act(page, MP3, "play")).toHaveText("Stop");
  await expect(row(page, MP3)).toHaveClass(/playing/);
  await expect.poll(() => playing(page, "/api/track/")).toBe(1);
  // This is also the control for every "nothing is sounding" assertion in the
  // suite: here the count must be 1, so a detector that always answered zero
  // — which is what querying the DOM for `audio` elements did — fails here.
  await expect.poll(() => sounding(page)).toBe(1);

  await act(page, MP3, "play").click();
  await expect(act(page, MP3, "play")).toHaveText("Play");
  await expect(row(page, MP3)).not.toHaveClass(/playing/);
  await expect.poll(() => playing(page, "/api/track/")).toBe(0);
  await expect.poll(() => sounding(page)).toBe(0);
});

test("only one track plays at a time", async ({ page }) => {
  await act(page, MP3, "play").click();
  await expect(row(page, MP3)).toHaveClass(/playing/);
  await act(page, WAV, "play").click();
  await expect(row(page, WAV)).toHaveClass(/playing/);
  await expect(row(page, MP3)).not.toHaveClass(/playing/);
});

test("clicking a row opens the clip editor on it", async ({ page }) => {
  // The editor existed but nothing said the row was clickable, so for
  // practical purposes it did not exist.
  await expect(page.locator("#trkWave")).toBeHidden();
  await row(page, MP3).locator(".trk__nm").click();
  await expect(page.locator("#trkWave")).toBeVisible();
  await expect(page.locator("#trkWave canvas")).toBeVisible();
  await expect(row(page, MP3)).toHaveClass(/sel/);
  // Analysis has landed when the readout names the selection.
  await expect(page.locator("#trkWave")).toContainText(/start 0:00/);
});

test("auditioning a clip drives the stage from that clip", async ({ page }) => {
  await row(page, MP3).locator(".trk__nm").click();
  const audition = page.locator("#trkWave button").first();
  await expect(audition).toBeEnabled();

  const stageBefore = await page.locator("#stageNote").textContent();
  await audition.click();
  await expect(audition).toHaveText("Stop");
  // The stage swaps to the track's own scene rather than seeking inside
  // whatever was loaded, which showed the wrong scene's lights.
  await expect(page.locator("#stageNote")).toHaveText(MP3);
  await expect(page.locator("#sheetFold summary")).toContainText(/\d+ cues?, \d+ light/);

  // And the lights actually move: sample the meter and require it to change.
  const meter = () => page.locator("#vl-door").textContent();
  const seen = new Set<string>();
  for (let i = 0; i < 12; i++) {
    seen.add((await meter()) ?? "");
    await page.waitForTimeout(120);
  }
  expect(seen.size).toBeGreaterThan(1);

  await audition.click();
  await expect(page.locator("#stageNote")).toHaveText(stageBefore ?? "");
});

test("Delete removes the track and closes the editor on it", async ({ page }) => {
  // Operates on its own fixture. Deleting one the other tests use would make
  // this suite order-dependent, which is a bug waiting for someone to add a
  // test above it.
  page.on("dialog", d => void d.accept());
  await row(page, DOOMED).locator(".trk__nm").click();
  await expect(page.locator("#trkWave")).toBeVisible();

  await act(page, DOOMED, "del").click();
  await expect(row(page, DOOMED)).toHaveCount(0);
  await expect(page.locator("#trkNote")).toContainText(`Deleted ${DOOMED}`);
  // The editor was open on it; leaving it up would show a track that is gone.
  await expect(page.locator("#trkWave")).toBeHidden();
  // Gone from the server too, not just from the DOM.
  expect((await page.request.get(`/api/track/${DOOMED}`)).status()).toBe(404);
});

test("Make scene reports what it did, and the row says so afterwards",
  async ({ page }) => {
    // The write itself is covered by tests/test_studio_api.py against a
    // scratch scenes.yaml. What is under test here is the half that made the
    // button look broken: no busy state, and no visible result.
    await page.route("**/api/scene", async route => {
      await new Promise(r => setTimeout(r, 400));   // a real one takes seconds
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, id: MP3, replaced: false,
                               scenes: ["vigil", MP3], log: "" }),
      });
    });

    const button = act(page, MP3, "scene");
    await expect(button).toHaveText("Make scene");
    await button.click();
    await expect(button).toHaveText("Working…");
    await expect(button).toBeDisabled();

    await expect(page.locator("#trkNote")).toContainText("added in scenes.yaml");
    await expect(page.locator("#trkNote .trk-reload")).toBeVisible();
    // The row itself now says the track is in the show — the visible proof
    // that the click did something.
    await expect(row(page, MP3).locator(".trk__badge")).toHaveText("in the show");
    await expect(act(page, MP3, "scene")).toHaveText("Update scene");
  });
