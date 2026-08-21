/**
 * Failure injection: a castle that is slow, that breaks mid-sync, that
 * acks short, that never acks — and the desk's own polling discipline.
 * The honest answer in every case is the one asserted: the right words in
 * the note, no ✓ badge that was not earned, and no redraw that nothing
 * asked for.
 */

import { test, expect, fakeCastle, realBytes } from "./fixtures.js";
import { MP3_ID, WAV_ID } from "./global-setup.js";

const row = (id: string) => `.trk[data-id="${id}"]`;

/** The real library with the chosen tracks marked as in the show. */
async function inShow(page: import("@playwright/test").Page, ids: string[]): Promise<void> {
  await page.route("**/api/tracks", async (route) => {
    const real = await (await route.fetch()).json() as { tracks: unknown[] };
    return route.fulfill({ json: { ...real, scenes: ids } });
  });
}

test("a slow castle still gets its chip, and the desk never waits on it", async ({ page }) => {
  const castle = await fakeCastle(page);
  castle.delay = 1200;                       // inside the 2.5 s probe budget
  await page.goto("/");
  await expect(page.locator("#stage")).toBeVisible();
  // The stage is live long before the castle answers.
  await expect(page.locator("#deviceChip")).toBeHidden();
  await expect(page.locator("#deviceChip")).toBeVisible({ timeout: 5000 });
  // A scene pick returns to the operator at once; the toast arrives when
  // the castle does.
  const t = Date.now();
  await page.locator("button.scene", { hasText: "Storm" }).first().click();
  await expect(page.locator("button.scene", { hasText: "Storm" }).first())
    .toHaveAttribute("aria-pressed", "true");
  expect(Date.now() - t).toBeLessThan(1000);
  await expect(page.locator("#toasts")).toContainText("scene storm", { timeout: 5000 });
});

test("a 5xx on the second file stops the sync and says which one", async ({ page }) => {
  const castle = await fakeCastle(page, []);
  await inShow(page, [MP3_ID, WAV_ID]);
  castle.putBytes = (name, real) => (name.startsWith(WAV_ID) ? -1 : real);
  await page.goto("/");
  const sync = page.locator("#trkSync");
  await expect(sync).toHaveText("Sync show → castle (2)");
  await sync.click();
  const note = page.locator("#trkNote");
  await expect(note).toContainText(`Send of “${WAV_ID}” failed — is the castle awake?`);
  await expect(note).toHaveClass(/err/);
  // The first went; the second did not; Sync says one is still owed.
  await expect(page.locator(row(MP3_ID)).locator(".trk__badge", { hasText: "on castle ✓" })).toBeVisible();
  await expect(page.locator(row(WAV_ID)).locator("button[data-act='send']")).toHaveText("→ Castle");
  await expect(sync).toHaveText("Sync show → castle (1)");
  await expect(sync).toBeEnabled();
});

test("a short ack is 'landed short', and the row reads stale — never ✓", async ({ page }) => {
  const castle = await fakeCastle(page, []);
  castle.putBytes = (_n, real) => real - 7;
  await page.goto("/");
  await page.locator(`${row(MP3_ID)} button[data-act='send']`).click();
  const note = page.locator("#trkNote");
  await expect(note).toContainText(`Send of “${MP3_ID}” landed short — the castle wrote`);
  await expect(note).toContainText("Send it again.");
  await expect(note).toHaveClass(/err/);
  const r = page.locator(row(MP3_ID));
  await expect(r.locator(".trk__badge", { hasText: "stale on castle" })).toBeVisible();
  await expect(r.locator(".trk__badge", { hasText: "on castle ✓" })).toHaveCount(0);
  await expect(r.locator("button[data-act='send']")).toHaveText("Update castle");
});

test("a 504 says the castle may have it, and does not re-send on its own", async ({ page }) => {
  const castle = await fakeCastle(page, []);
  castle.putBytes = () => -2;
  await page.goto("/");
  await page.locator(`${row(MP3_ID)} button[data-act='send']`).click();
  await expect(page.locator("#trkNote")).toContainText("did not confirm in time");
  await expect(page.locator("#trkNote")).toContainText("do not re-send until it does");
  await page.waitForTimeout(400);
  expect(castle.hits(`PUT /api/files/${MP3_ID}`)).toBe(1);
  await expect(page.locator(`${row(MP3_ID)} button[data-act='send']`)).toHaveText("→ Castle");
});

test("a send the card cannot hold is refused before a byte moves", async ({ page }) => {
  const castle = await fakeCastle(page, [], { sd_free_kb: 1 });
  await page.goto("/");
  await page.locator(`${row(MP3_ID)} button[data-act='send']`).click();
  await expect(page.locator("#trkNote")).toContainText("No room on the card");
  expect(castle.hits("PUT /api/files")).toBe(0);
});

test("the card watcher redraws only on a real change, and never under a focused hand",
    async ({ page }) => {
  const castle = await fakeCastle(page,
    [{ name: `${MP3_ID}.mp3`, size: await realBytes(page, MP3_ID), dir: false }]);
  await page.clock.install();
  await page.goto("/");
  await expect(page.locator(row(MP3_ID)).locator(".trk__badge", { hasText: "on castle ✓" }))
    .toBeVisible();
  // Count list rebuilds, and park the keyboard on a row button.
  await page.evaluate(() => {
    const w = window as unknown as { __redraws: number };
    w.__redraws = 0;
    new MutationObserver(() => { w.__redraws++; })
      .observe(document.getElementById("trkList")!, { childList: true });
  });
  await page.locator(`${row(MP3_ID)} button[data-act='scene']`).focus();
  const redraws = () => page.evaluate(() => (window as unknown as { __redraws: number }).__redraws);
  const polls = () => castle.hits("GET /api/files");
  const before = await polls();

  // One watcher tick with nothing changed: a poll, no redraw, focus intact.
  await page.clock.fastForward(21_000);
  await expect.poll(polls).toBeGreaterThan(before);
  await page.waitForTimeout(200);
  expect(await redraws()).toBe(0);
  expect(await page.evaluate(() => document.activeElement?.getAttribute("data-act"))).toBe("scene");

  // The card changed under the desk (a file pulled on the porch): one redraw.
  castle.files.length = 0;
  await page.clock.fastForward(21_000);
  await expect(page.locator(row(MP3_ID)).locator(".trk__badge", { hasText: "on castle" }))
    .toHaveCount(0);
  expect(await redraws()).toBe(1);
});

test("the ♪ route survives a reload without re-hushing the castle", async ({ page }) => {
  const castle = await fakeCastle(page);
  await page.goto("/");
  const route = page.locator(".transport #sndRoute");
  await expect(route).toHaveText("♪ Mac");
  await expect.poll(() => castle.hits("/api/volume?v=0")).toBe(1);   // Mac route enforced
  await route.click();
  await expect(route).toHaveText("♪ Castle");
  await expect.poll(() => castle.hits("/api/volume?v=40")).toBe(1);

  castle.calls.length = 0;
  await page.reload();
  await expect(page.locator(".transport #sndRoute")).toHaveText("♪ Castle");
  await expect(page.locator("#devVol")).toBeEnabled();
  await expect(page.locator("#headTxt")).toContainText("sound: castle");
  await page.waitForTimeout(500);
  expect(castle.hits("/api/volume")).toBe(0);        // nothing to enforce
  await expect(page.locator("#devVol")).toHaveValue("40");
});

test("a castle that dies mid-session takes its badges and Sync with it", async ({ page }) => {
  const castle = await fakeCastle(page,
    [{ name: `${MP3_ID}.mp3`, size: await realBytes(page, MP3_ID), dir: false }]);
  await inShow(page, [WAV_ID]);
  await page.goto("/");
  await expect(page.locator("#trkSync")).toHaveText("Sync show → castle (1)");
  castle.up = false;
  // Any action's re-poll discovers the loss within a second; presence flips
  // and the library re-reads the card (and gets nothing).
  await page.locator("#devStop").click();
  await expect(page.locator("#headTxt")).toContainText("castle not answering");
  await expect(page.locator("#trkSync")).toBeHidden();
  await expect(page.locator(".trk__badge", { hasText: "on castle" })).toHaveCount(0);
  await expect(page.locator("button[data-act='send']")).toHaveCount(0);
});
