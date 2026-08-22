/**
 * The Tracks panel's row operations after judge B's pass 1.
 *
 * Every test here reproduces a finding: the Options row's START/LENGTH
 * leaking between tracks (JB1-1), Re-import counting from the wrong origin
 * and forwarding half the options (JB1-4/6), a dropped file with no source
 * left to re-import from (JB1-3), deleting a track the show still uses
 * (JB1-6), raw tracebacks and overwritten status lines (JB1-10), and a
 * library that said EMPTY while it was still loading (JB1-5). The server
 * side of each is covered in tests/test_studio_tracks_api.py; this is the
 * wiring.
 */

import { type Locator, type Page, type Route } from "@playwright/test";
import { test, expect } from "./fixtures.js";

const MP3 = "e2e_beats";
const WAV = "e2e_lossless";

const row = (page: Page, id: string) => page.locator(`.trk[data-id="${id}"]`);
const act = (page: Page, id: string, action: string) =>
  row(page, id).locator(`button[data-act="${action}"]`);

/** Drag a region out on the waveform so the editor writes START/LENGTH. */
/** An element's box once it has stopped moving.
 *
 *  Opening the clip editor scrolls it into view with `behavior: "smooth"`, so
 *  for a few frames the canvas is somewhere between where it was and where it
 *  will be. Measuring then and pressing the mouse at those coordinates put the
 *  gesture outside the canvas: no selection, and the clip stayed at its full
 *  length — which is what the offset test saw, intermittently, depending on
 *  how the scroll animation lined up with the test.
 */
async function settledBox(loc: Locator): Promise<{ x: number; y: number; width: number; height: number }> {
  let last = await loc.boundingBox();
  await expect.poll(async () => {
    const now = await loc.boundingBox();
    const still = !!now && !!last && now.x === last.x && now.y === last.y;
    last = now;
    return still;
  }).toBe(true);
  return last!;
}

async function dragClip(page: Page, from = 0.3, to = 0.6): Promise<void> {
  const canvas = page.locator("#trkWave canvas:not(.stems-strip)");
  // The canvas is on screen BEFORE the audio is decoded, and a drag then maps
  // every pixel to 0 s: #trkStart fills in with 0:00.0, this helper's old
  // "not empty" wait was satisfied, and the caller's arithmetic quietly used
  // a zero (CI, slower than a warm laptop, hit it on the offset test).
  // Audition enables only once the waveform has data — the same signal
  // tracks.spec waits on before ITS drag.
  await expect(page.getByRole("button", { name: "Audition" })).toBeEnabled();
  const box = await settledBox(canvas);
  // hover({position}) rather than mouse.move(absolute): it runs Playwright's
  // actionability checks first, so the press waits until the canvas is where
  // it will stay AND is the thing under that point. Absolute coordinates
  // pressed whatever happened to be on top — a toast, the dock — and the
  // canvas never saw the gesture, leaving the clip at its full length.
  const y = box.height / 2;
  await canvas.hover({ position: { x: box.width * from, y } });
  await page.mouse.down();
  await canvas.hover({ position: { x: box.width * to, y } });
  await page.mouse.up();
  // Wait on the READOUT, which is written from the clip and from nothing else.
  // #trkStart was the wrong thing to watch: a track whose remembered options
  // already carry a start shows that number before anything is dragged, so
  // "not empty" — and even "not zero" — passed on the old value and the caller
  // asserted against a clip that had not landed (CI, and once locally).
  await expect(page.locator(".wave__readout")).toHaveText(/start 0:0*[1-9]/);
}

/** /studio/tracks with each entry passed through `patch`; the real list otherwise. */
async function patchTracks(page: Page, patch: (t: Record<string, unknown>) => Record<string, unknown>,
                           scenes?: (s: string[]) => string[]): Promise<void> {
  await page.route("**/studio/tracks", async (route: Route) => {
    if (route.request().method() !== "GET") return route.fallback();
    const res = await route.fetch();
    const body = await res.json() as { tracks: Record<string, unknown>[]; scenes: string[] };
    body.tracks = body.tracks.map(patch);
    if (scenes) body.scenes = scenes(body.scenes);
    await route.fulfill({ json: body });
  });
}

/** A drop of a small WAV onto the drop zone. */
async function dropFile(page: Page, name = "b.wav"): Promise<void> {
  const dt = await page.evaluateHandle((n) => {
    const dt = new DataTransfer();
    dt.items.add(new File([new Uint8Array(64)], n, { type: "audio/wav" }));
    return dt;
  }, name);
  await page.dispatchEvent("#trkDrop", "drop", { dataTransfer: dt });
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#trkMode")).toHaveText(/studio/);
  await expect(row(page, MP3)).toBeVisible();
});

test("a drop does not inherit the clip editor's trim from another track",
  async ({ page }) => {
    let opts: Record<string, string> | null = null;
    await page.route("**/studio/import", async (route) => {
      opts = JSON.parse(route.request().headers()["x-import-opts"] ?? "{}");
      await route.fulfill({ json: { ok: true, tracks: [] } });
    });
    await row(page, MP3).locator(".trk__nm").click();
    await dragClip(page);
    await expect(page.locator("#trkStart")).toHaveAttribute("data-owner", MP3);

    await dropFile(page);
    await expect.poll(() => opts).not.toBeNull();
    expect(opts!["start"]).toBe("");
    expect(opts!["take"]).toBe("");
    // Consumed: the next import starts from a clean row.
    await expect(page.locator("#trkStart")).toHaveValue("");
  });

test("a typed trim DOES reach a drop, and is consumed by it", async ({ page }) => {
  let opts: Record<string, string> | null = null;
  await page.route("**/studio/import", async (route) => {
    opts = JSON.parse(route.request().headers()["x-import-opts"] ?? "{}");
    await route.fulfill({ json: { ok: true, tracks: [] } });
  });
  await page.locator("#trkOpts summary").click();
  await page.locator("#trkStart").fill("0:05");
  await page.locator("#trkTake").fill("3");
  await dropFile(page);
  await expect.poll(() => opts).not.toBeNull();
  expect(opts!["start"]).toBe("0:05");
  expect(opts!["take"]).toBe("3");
  await expect(page.locator("#trkTake")).toHaveValue("");
});

test("Re-import on the open track offsets START by the remembered start and forwards every option",
  async ({ page }) => {
    // The fixtures are seeded without a manifest; give this one a remembered
    // source (so the row offers Re-import) and a remembered start.
    await patchTracks(page, t => t["id"] === MP3
      ? { ...t, source: "https://example.com/x",
          opts: { start: "0:30", take: "10", channels: 1, fade_in: 0.5 } } : t);
    let body: Record<string, unknown> | null = null;
    await page.route("**/studio/refresh", async (route) => {
      body = route.request().postDataJSON();
      await route.fulfill({ json: { ok: true, tracks: [] } });
    });
    await page.reload();
    await expect(row(page, MP3)).toBeVisible();
    await row(page, MP3).locator(".trk__nm").click();
    await dragClip(page, 0.5, 0.8);            // ~2.0 s into a 4 s file
    await act(page, MP3, "refresh").click();
    await expect.poll(() => body).not.toBeNull();
    const b = body!;
    // 0:30 + the clip's start, as m:ss.s — counted from the source.
    expect(String(b["start"])).toMatch(/^0:3[12]\.\d$/);
    expect(Number(b["take"])).toBeGreaterThan(0.5);
    expect(b["format"]).toBe("mp3");
    expect(b["fade_in"]).toBe("0.5");
    expect(b["fade_out"]).toBe("0");
    expect(typeof b["normalize"]).toBe("boolean");
    await expect(page.locator("#trkNote")).toContainText("keeping");
  });

test("Re-import on another row ignores the editor's trim", async ({ page }) => {
  await patchTracks(page, t => ({ ...t, source: "https://example.com/x" }));
  let body: Record<string, unknown> | null = null;
  await page.route("**/studio/refresh", async (route) => {
    body = route.request().postDataJSON();
    await route.fulfill({ json: { ok: true, tracks: [] } });
  });
  await page.reload();
  await expect(row(page, MP3)).toBeVisible();
  await row(page, MP3).locator(".trk__nm").click();
  await dragClip(page);
  await act(page, WAV, "refresh").click();
  await expect.poll(() => body).not.toBeNull();
  expect(body!["id"]).toBe(WAV);
  expect(body!["start"]).toBe("");
  expect(body!["take"]).toBe("");
});

test("a failed import shows the studio's one-line reason, never a traceback",
  async ({ page }) => {
    await page.route("**/studio/import", (route) => route.fulfill({
      status: 500, json: { ok: false, reason: "ffmpeg failed (exit 1)",
        log: "Traceback (most recent call last):\nsubprocess.CalledProcessError: …" },
    }));
    await dropFile(page, "junk.wav");
    const note = page.locator("#trkNote");
    await expect(note).toContainText("junk.wav failed — ffmpeg failed (exit 1)");
    await expect(note).not.toContainText("Traceback");
    await expect(note).toHaveClass(/err/);
  });

test("a track whose original is gone offers no Re-import and the mono badge says so",
  async ({ page }) => {
    await patchTracks(page, t => t["id"] === WAV
      ? { ...t, source: "file:/somewhere/gone.wav", source_missing: true,
          opts: { channels: 1 } } : t);
    await page.reload();
    await expect(row(page, WAV)).toBeVisible();
    await expect(act(page, WAV, "refresh")).toHaveCount(0);
    await expect(row(page, WAV).locator(".trk__mono"))
      .toHaveAttribute("title", /original file is gone/);
    // And never an absolute path in front of the operator.
    await expect(row(page, WAV)).not.toContainText("/somewhere/");
  });

test("deleting a track the show uses asks about its scene and takes it out",
  async ({ page }) => {
    await patchTracks(page, t => t, s => [...s, WAV]);
    let deleted = "";
    await page.route(`**/studio/tracks/${WAV}?scene=1`, async (route) => {
      deleted = route.request().url();
      await route.fulfill({ json: { ok: true, removed: WAV, scene_removed: true,
                                    scenes: [], log: "" } });
    });
    await page.reload();
    await expect(row(page, WAV).locator(".trk__badge")).toHaveText("in the show");
    let asked = "";
    page.on("dialog", d => { asked = d.message(); void d.accept(); });
    await act(page, WAV, "del").click();
    await expect.poll(() => asked).toContain("IN THE SHOW");
    await expect.poll(() => deleted).toContain("scene=1");
    await expect(page.locator("#trkNote")).toContainText("removed its scene");
    await expect(page.locator("#trkNote .trk-reload")).toBeVisible();
  });

test("Make scene refuses a track with no playable audio instead of writing NaN",
  async ({ page }) => {
    let wrote = false;
    await page.route("**/studio/scene", (route) => { wrote = true; return route.fulfill({ json: { ok: true } }); });
    await patchTracks(page, t => t["id"] === MP3 ? { ...t, dur: undefined } : t);
    await act(page, MP3, "scene").click();
    await expect(page.locator("#trkNote")).toContainText("no playable audio");
    expect(wrote).toBe(false);
    await expect(act(page, MP3, "scene")).toBeEnabled();
  });

test("a row with a job in flight cannot be deleted or re-imported, and other work keeps its own line",
  async ({ page }) => {
    await patchTracks(page, t => ({ ...t, source: "https://example.com/x" }));
    await page.route("**/studio/scene", async (route) => {
      await new Promise(r => setTimeout(r, 900));
      await route.fulfill({ json: { ok: true, id: MP3, replaced: false, scenes: [MP3], log: "" } });
    });
    await page.reload();
    await expect(row(page, MP3)).toBeVisible();
    await act(page, MP3, "scene").click();
    await expect(act(page, MP3, "scene")).toHaveText("Working…");
    await expect(act(page, MP3, "del")).toBeDisabled();
    await expect(act(page, MP3, "refresh")).toBeDisabled();
    // The scene write has its own status line; the headline stays free.
    await expect(page.locator("#trkNote [data-op^='scene:']")).toContainText("Writing scene");
    await expect(act(page, WAV, "del")).toBeEnabled();
    await expect(page.locator("#trkNote")).toContainText("added in scenes.yaml");
    await expect(page.locator("#trkNote [data-op^='scene:']")).toHaveCount(0);
    await expect(act(page, MP3, "del")).toBeEnabled();
  });

test("the library says it is loading, not EMPTY, before the first answer",
  async ({ page }) => {
    await page.route("**/studio/tracks", async (route) => {
      await new Promise(r => setTimeout(r, 800));
      await route.continue();
    });
    await page.reload();
    await expect(page.locator("#trkCount")).toHaveText("loading library…");
    await expect(page.locator("#trkCount")).toHaveText(/\d+ imported/);
  });

test("the clip editor says what the selection is for", async ({ page }) => {
  await row(page, MP3).locator(".trk__nm").click();
  await expect(page.locator("#trkWave")).toContainText(/start 0:00/);
  await expect(page.locator(".wave__trimhint")).toBeHidden();
  await dragClip(page);
  await expect(page.locator(".wave__trimhint")).toContainText("press Re-import");
});
