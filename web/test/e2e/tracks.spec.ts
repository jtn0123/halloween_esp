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

  const res = await page.request.get(`/studio/track/${WAV}`);
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

test("switching tracks before the first has started does not silence both",
  async ({ page }) => {
    // play() is asynchronous, and pausing an element whose play is still in
    // flight rejects that promise with AbortError. Handling the rejection
    // without checking whether it had been superseded meant the *new* track
    // got stopped and blamed.
    //
    // The first track's bytes are held back so that play() is guaranteed to
    // still be pending when the second click lands. Without this the local
    // server answers fast enough that the race usually does not open, and the
    // test passes against the broken code — which is how this arrived as a
    // one-in-three failure of the test above rather than as a real report.
    await page.route(`**/api/track/${MP3}`, async route => {
      await new Promise(r => setTimeout(r, 800));
      await route.continue();
    });

    await act(page, MP3, "play").click();
    await act(page, WAV, "play").click();

    await expect(row(page, WAV)).toHaveClass(/playing/);
    await expect(row(page, MP3)).not.toHaveClass(/playing/);
    await expect.poll(() => playing(page, "/api/track/")).toBe(1);
    // And no spurious failure was reported for the request that lost the race.
    await expect(page.locator("#trkNote")).not.toHaveClass(/err/);
  });

test("clicking a row opens the clip editor on it", async ({ page }) => {
  // The editor existed but nothing said the row was clickable, so for
  // practical purposes it did not exist.
  await expect(page.locator("#trkWave")).toBeHidden();
  await row(page, MP3).locator(".trk__nm").click();
  await expect(page.locator("#trkWave")).toBeVisible();
  await expect(page.locator("#trkWave canvas:not(.stems-strip)")).toBeVisible();
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
  expect((await page.request.get(`/studio/track/${DOOMED}`)).status()).toBe(404);
});

test("Make scene reports what it did, and the row says so afterwards",
  async ({ page }) => {
    // The write itself is covered by tests/test_studio_api.py against a
    // scratch scenes.yaml. What is under test here is the half that made the
    // button look broken: no busy state, and no visible result.
    await page.route("**/studio/scene", async route => {
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

test("each band gets its own zone and its own threshold", async ({ page }) => {
  await row(page, MP3).locator(".trk__nm").click();
  const rows = page.locator(".bandcfg__row");
  await expect(rows).toHaveCount(3);
  await expect(page.locator("#trkWave")).toContainText(/start 0:00/);

  // The defaults, spelled out: this mapping is what a generated scene uses.
  await expect(rows.nth(0).locator(".bandcfg__zone")).toHaveValue("door");
  await expect(rows.nth(1).locator(".bandcfg__zone")).toHaveValue("towerL");
  await expect(rows.nth(2).locator(".bandcfg__zone")).toHaveValue("towerR");

  // Loosening one band must not silently re-detect the others. That is the
  // whole reason the threshold is per band rather than global.
  const hits = (i: number) => rows.nth(i).locator(".bandcfg__hits");
  await expect(hits(0)).toHaveText(/\d+ · [\d.]+\/s/);
  const lowBefore = await hits(0).textContent();
  const midBefore = await hits(1).textContent();

  await rows.nth(2).locator(".bandcfg__sens").fill("0.4");
  // Debounced, then a round trip to the analyser.
  await expect(rows.nth(2).locator(".bandcfg__val")).toHaveText("0.40");
  await expect(hits(0)).toHaveText(lowBefore ?? "");
  await expect(hits(1)).toHaveText(midBefore ?? "");

  // Reassigning a zone follows through to the summary line.
  await rows.nth(0).locator(".bandcfg__zone").selectOption("towerR");
  await expect(page.locator("#trkWave p:not(.stems-note):not(.wave__trimhint)")).toContainText("towerR");
});

test("Snap to beat moves the clip onto detected onsets", async ({ page }) => {
  await row(page, MP3).locator(".trk__nm").click();
  const snap = page.locator("#trkWave button", { hasText: "Snap to beat" });
  await expect(snap).toBeEnabled();

  // Drag out a region that deliberately does not start on a transient.
  const canvas = page.locator("#trkWave canvas:not(.stems-strip)");
  const box = (await canvas.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.31, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.63, box.y + box.height / 2);
  await page.mouse.up();
  await expect(page.locator("#trkStart")).not.toHaveValue("");

  await snap.click();
  // Either it moved the edit, or both ends were already on a beat. Both are
  // correct outcomes; silently doing nothing without saying so is not.
  await expect(page.locator("#trkWave p:not(.stems-note):not(.wave__trimhint)"))
    .toHaveText(/Snapped to the nearest onsets|Already on a beat/);
});

test("codec comparison encodes the clip and switches without losing position",
  async ({ page }) => {
    await row(page, MP3).locator(".trk__nm").click();
    await expect(page.locator("#trkWave")).toContainText(/start 0:00/);

    const compare = page.locator(".codecab__bar button");
    await expect(compare).toHaveText("Compare codecs");
    await compare.click();
    // Four ffmpeg passes; the button must say so rather than looking dead.
    await expect(compare).toHaveText("Encoding…");

    const picks = page.locator(".codecab__pick");
    await expect(picks).toHaveCount(4, { timeout: 60_000 });
    await expect(compare).toHaveText("Compare codecs");

    // The lossless reference is labelled as such, and the lossy encodes carry
    // a number. Both halves of the trade are on the button.
    await expect(picks.filter({ hasText: "WAV" })).toContainText("reference");
    await expect(picks.filter({ hasText: "MP3" })).toContainText(/[\d.]+ dB/);
    // Lossy has to be smaller than lossless, or the comparison is broken.
    // Read the size span itself. textContent() on the whole button glues the
    // label to the size — "MP3" + "48 KB" reads as "MP348 KB" — and a regex
    // over that happily returns 348.
    const size = async (codec: string) => {
      const t = await picks.filter({ hasText: codec }).locator("span").first()
        .textContent();
      const m = /^([\d.]+)\s*(KB|MB)/.exec((t ?? "").trim());
      expect(m, `no size on the ${codec} button: ${t}`).not.toBeNull();
      return Number(m![1]) * (m![2] === "MB" ? 1024 : 1);
    };
    expect(await size("MP3")).toBeLessThan(await size("WAV"));

    // Playing one, then switching, must keep the position — an A/B that
    // restarts from zero compares nothing.
    await picks.filter({ hasText: "MP3" }).click();
    await expect(picks.filter({ hasText: "MP3" })).toHaveClass(/on/);
    await expect.poll(() => playing(page, "/api/compare/")).toBe(1);
    await page.waitForTimeout(1200);

    const at = () => page.evaluate(`
      [...(window.__media || [])].filter(a => a.src.includes("/api/compare/"))
        .map(a => a.currentTime).pop() ?? 0`) as Promise<number>;
    const before = await at();
    expect(before).toBeGreaterThan(0.3);

    await picks.filter({ hasText: "FLAC" }).click();
    await expect(picks.filter({ hasText: "FLAC" })).toHaveClass(/on/);
    await expect(picks.filter({ hasText: "MP3" })).not.toHaveClass(/on/);
    await expect.poll(at).toBeGreaterThan(before - 0.35);

    // Pressing the one that is playing stops it.
    await picks.filter({ hasText: "FLAC" }).click();
    await expect(picks.filter({ hasText: "FLAC" })).not.toHaveClass(/on/);
    await expect.poll(() => sounding(page)).toBe(0);
  });

test("a URL import shows live progress and lands in the list", async ({ page }) => {
  // The polling loop against a mocked job runner: phases stream into the
  // status line, and the final poll's track list redraws the panel. The
  // real yt-dlp path can't run in a test; the wiring is what broke (A8 —
  // the async pipeline shipped with no caller at all).
  let polls = 0;
  await page.route("**/studio/import/async", (r) => r.fulfill({ json: {
    id: "j1", phase: "queued", percent: 0, detail: "", error: null,
    done: false, log: [],
  } }));
  await page.route("**/studio/job/j1", (r) => {
    polls += 1;
    const stages = [
      { phase: "fetching", percent: 40, detail: "12.4MB", done: false },
      { phase: "converting", percent: 80, detail: "", done: false },
      { phase: "done", percent: 100, detail: "", done: true },
    ];
    const s = stages[Math.min(polls - 1, 2)]!;
    return r.fulfill({ json: {
      id: "j1", error: null, log: [], ...s,
      ...(s.done ? { tracks: [{ id: "e2e_async", dur: 9, bytes: 1000,
                                ext: "mp3", onsets: {} }] } : {}),
    } });
  });
  await page.locator("#trkUrl").fill("https://example.com/x");
  await page.locator("#trkGet").click();
  await expect(page.locator("#trkNote")).toContainText("downloading 40%");
  await expect(page.locator("#trkNote")).toContainText("Imported",
    { timeout: 10_000 });
  await expect(row(page, "e2e_async")).toBeVisible();
});

test("an import the server refuses fails with its reason, not a spinner",
    async ({ page }) => {
  await page.route("**/studio/import/async", (r) => r.fulfill({
    status: 400, json: { error: "id: letters, digits and _ only" },
  }));
  await page.locator("#trkUrl").fill("https://example.com/x");
  await page.locator("#trkGet").click();
  await expect(page.locator("#trkNote")).toContainText("letters, digits");
  await expect(page.locator("#trkGet")).toBeEnabled();
});
