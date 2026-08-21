/**
 * The merged library: one list that answers "what is WHERE" (dogfood
 * 005/006). Local tracks are real (the studio serves the seeded scratch
 * library); the castle is played by page.route so the card's contents are a
 * controlled fact. Only castle-shaped endpoints are stubbed — /api/tracks
 * and /api/track/<id> pass through to the real studio, so a Sync moves the
 * actual bytes the actual endpoint serves.
 */

import { test, expect, type Page } from "@playwright/test";
import { MP3_ID, WAV_ID } from "./global-setup.js";

const STATUS = {
  version: "5.22",
  uptime_s: 120,
  sd_mounted: true,
  psram_free_kb: 1800,
  heap_free_kb: 96,
  volume: 40,
};

interface SdFile { name: string; size: number; dir: boolean }

/** A pretend castle whose card starts with `files`; uploads and deletes
 *  mutate the list, so redraws after an action see their consequences. */
async function stubCard(page: Page, files: SdFile[],
                        inShow: string[] = []): Promise<string[]> {
  const calls: string[] = [];
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    const method = route.request().method();
    calls.push(`${method} ${p}${url.search}`);
    if (p === "/api/status") return route.fulfill({ json: STATUS });
    if (p === "/api/files" && method === "GET")
      return route.fulfill({ json: files });
    if (p.startsWith("/api/files/") && method === "PUT") {
      const name = decodeURIComponent(p.slice("/api/files/".length));
      files.push({ name, size: 1024, dir: false });
      return route.fulfill({ json: { path: `/sd/${name}`, bytes: 1024 } });
    }
    if (p.startsWith("/api/files/") && method === "DELETE") {
      const name = decodeURIComponent(p.slice("/api/files/".length));
      const i = files.findIndex((f) => f.name === name);
      if (i >= 0) files.splice(i, 1);
      return route.fulfill({ json: { deleted: true } });
    }
    if (["/api/play", "/api/stop", "/api/volume", "/api/scene"].includes(p)
        && method === "POST")
      return route.fulfill({ json: { queued: true } });
    if (p === "/api/tracks" && inShow.length > 0) {
      // The real library, with chosen tracks marked as in the show — scene
      // membership is what Sync sends, and the scratch scenes.yaml is empty.
      const real = await (await route.fetch()).json();
      return route.fulfill({ json: { ...real, scenes: inShow } });
    }
    return route.fallback();
  });
  return calls;
}

test("every local track says whether the castle has it", async ({ page }) => {
  await stubCard(page, [{ name: `${MP3_ID}.mp3`, size: 48000, dir: false }]);
  await page.goto("/");
  const has = page.locator(`.trk[data-id="${MP3_ID}"]`);
  const hasNot = page.locator(`.trk[data-id="${WAV_ID}"]`);
  await expect(has.locator(".trk__badge", { hasText: "on castle" })).toBeVisible();
  await expect(has.locator("button[data-act='send']")).toHaveText("Re-send");
  await expect(hasNot.locator(".trk__badge", { hasText: "on castle" })).toBeHidden();
  await expect(hasNot.locator("button[data-act='send']")).toHaveText("→ Castle");
});

test("without a castle, no badge claims anything", async ({ page }) => {
  // The studio's relay 502s when no castle is configured; the rows must stay
  // quiet rather than render every track as "not sent".
  await page.goto("/");
  await expect(page.locator(`.trk[data-id="${MP3_ID}"]`)).toBeVisible();
  await expect(page.locator(".trk__badge", { hasText: "on castle" })).toHaveCount(0);
  await expect(page.locator("button[data-act='send']")).toHaveCount(0);
  await expect(page.locator("#trkSync")).toBeHidden();
});

test("files only on the card get rows of their own", async ({ page }) => {
  const calls = await stubCard(page, [
    { name: "phantom_waltz.mp3", size: 512000, dir: false },
    { name: "logs", size: 0, dir: true },
  ]);
  await page.goto("/");
  await expect(page.locator(".trk-cardhd")).toHaveText(/card only/i);
  const row = page.locator(".trk--card[data-card='phantom_waltz.mp3']");
  await expect(row).toContainText("castle only");
  await expect(page.locator(".trk--card[data-card='logs']")).toHaveCount(0);

  await row.locator("button[data-cardact='play']").click();
  await expect.poll(() =>
    calls.filter((c) => c.includes("/api/play?f=phantom_waltz.mp3")).length)
    .toBeGreaterThan(0);
});

test("a card-only row can be deleted, after asking", async ({ page }) => {
  const calls = await stubCard(page,
    [{ name: "phantom_waltz.mp3", size: 512000, dir: false }]);
  await page.goto("/");
  const row = page.locator(".trk--card[data-card='phantom_waltz.mp3']");
  await expect(row).toBeVisible();
  page.on("dialog", (d) => void d.accept());
  await row.locator("button[data-cardact='del']").click();
  await expect.poll(() =>
    calls.filter((c) => c.startsWith("DELETE /api/files/phantom_waltz.mp3")).length)
    .toBeGreaterThan(0);
  await expect(page.locator(".trk--card")).toHaveCount(0);   // list re-read
});

test("sync sends exactly what the show is missing, then reports done", async ({ page }) => {
  const calls = await stubCard(page, [], [MP3_ID]);
  await page.goto("/");
  const sync = page.locator("#trkSync");
  await expect(sync).toHaveText(`Sync show → castle (1)`);

  await sync.click();
  await expect.poll(() =>
    calls.filter((c) => c.startsWith(`PUT /api/files/${MP3_ID}.mp3`)).length)
    .toBeGreaterThan(0);
  // Only the in-show track went — the rest of the library stays local.
  expect(calls.filter((c) => c.startsWith(`PUT /api/files/${WAV_ID}`))).toHaveLength(0);
  await expect(page.locator("#trkNote")).toContainText("Show synced — 1 track");
  // The card now has it, so the button stands down.
  await expect(sync).toHaveText("show is on the card ✓");
  await expect(sync).toBeDisabled();
});

test("a → Castle send updates the row to Re-send", async ({ page }) => {
  await stubCard(page, [{ name: "unrelated.mp3", size: 1000, dir: false }]);
  await page.goto("/");
  const btn = page.locator(`.trk[data-id="${MP3_ID}"] button[data-act='send']`);
  await expect(btn).toHaveText("→ Castle");
  await btn.click();
  await expect(page.locator("#trkNote")).toContainText("is on the castle's card");
  await expect(
    page.locator(`.trk[data-id="${MP3_ID}"] button[data-act='send']`))
    .toHaveText("Re-send");
});
