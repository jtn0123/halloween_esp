/**
 * The desk WITH a castle: the operator flows that cross the audio ↔ light ↔
 * castle seam. device.spec.ts covers the chip's own controls; these cover
 * what the rest of the desk does differently once a castle is answering —
 * adopting its scene, deciding where sound comes out, mirroring Stop, and
 * staying honest on a phone.
 */

import { test, expect, sounding, playing, fakeCastle, realBytes } from "./fixtures.js";
import { MP3_ID } from "./global-setup.js";

const MP3_ROW = `.trk[data-id="${MP3_ID}"]`;

test("on load the desk adopts the castle's scene without re-firing it", async ({ page }) => {
  // The castle is mid-Storm when the page opens. The desk must show Storm —
  // and must NOT POST the scene back, or opening the desk restarts the
  // porch's audio from the top.
  const castle = await fakeCastle(page, [], { scene: "storm" });
  await page.goto("/");
  await expect(page.locator("#deviceChip")).toBeVisible();
  await expect(page.locator("button.scene", { hasText: "Storm" }).first())
    .toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#stageNote")).toContainText("Storm");
  await expect(page.locator("#devNow")).toContainText("storm");
  // Adopting does not start the desk's own audio either.
  await expect(page.locator("#playLabel")).toHaveText("Play");
  expect(await sounding(page)).toBe(0);

  // A real pick afterwards does go to the castle — and is the ONLY scene
  // call in the log: the adoption before it posted nothing.
  await page.locator("button.scene", { hasText: "Vigil" }).first().click();
  await expect.poll(() => castle.hits("/api/scene?s=vigil")).toBe(1);
  expect(castle.hits("/api/scene")).toBe(1);
});

test("kiosk mode follows the castle's scene and keeps the stage whole", async ({ page }) => {
  const castle = await fakeCastle(page, [], { scene: "storm" });
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto("/?kiosk=1");
  await expect(page.locator("#stage")).toBeVisible();
  await expect(page.locator("#stageNote")).toContainText("Storm");
  // Adopted AND running is the end of first contact; nothing was posted.
  await expect(page.locator("#playLabel")).toHaveText("Pause");
  expect(castle.hits("/api/scene")).toBe(0);
  // The dock is still there for a glance at the porch, but nothing about it
  // may push the wall tablet's page sideways.
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test("♪ Mac: pressing Play is the consent, and the browser unmutes", async ({ page }) => {
  await fakeCastle(page);
  await page.goto("/");
  await expect(page.locator("#deviceChip")).toBeVisible();
  await expect(page.locator("#mute")).toHaveText("Muted");
  await page.locator("#play").click();
  await expect(page.locator("#playLabel")).toHaveText("Pause");
  await expect(page.locator("#mute")).toHaveText("Mute");
  await expect(page.locator("#headTxt")).toContainText("sounding");
  await expect(page.locator("#headTxt")).toContainText("sound: Mac");
  await page.locator("#stop").click();
});

test("♪ Castle: Play runs the lights and the browser stays muted", async ({ page }) => {
  // The castle's speaker is the one that should sound. Play must NOT flip
  // the browser's mute the way it does on the Mac route — two speakers out
  // of step is the worst version of sound.
  await page.addInitScript(() => localStorage.setItem("castleSoundRoute", "castle"));
  await fakeCastle(page);
  await page.goto("/");
  await expect(page.locator(".transport #sndRoute")).toHaveText("♪ Castle");
  await page.locator("#play").click();
  await expect(page.locator("#playLabel")).toHaveText("Pause");
  await expect.poll(async () =>
    Number.parseFloat((await page.locator("#tc").textContent()) ?? "0")).toBeGreaterThan(0.2);
  await expect(page.locator("#mute")).toHaveText("Muted");
  await expect(page.locator("#headTxt")).toContainText("muted");
  await expect(page.locator("#headTxt")).toContainText("sound: castle");
  // The modelled latency starts the file a moment later; it starts muted.
  await expect.poll(() => playing(page)).toBeGreaterThan(0);
  expect(await sounding(page)).toBe(0);

  // Flipping to Mac mid-show is the consent: the browser unmutes; flipping
  // back hushes it again.
  await page.locator(".transport #sndRoute").click();
  await expect(page.locator("#mute")).toHaveText("Mute");
  await page.locator(".transport #sndRoute").click();
  await expect(page.locator("#mute")).toHaveText("Muted");
  expect(await sounding(page)).toBe(0);
  await page.locator("#stop").click();
});

test("Stop and Esc reach the castle while mirroring, and stay local when not",
    async ({ page }) => {
  // The desk fired Storm on the porch; ■ Stop on the same transport has to
  // end it there too, or Stop silences the laptop while the porch plays on.
  const castle = await fakeCastle(page);
  await page.goto("/");
  await expect(page.locator("#deviceChip")).toBeVisible();
  await page.locator("button.scene", { hasText: "Storm" }).first().click();
  await expect.poll(() => castle.hits("/api/scene?s=storm")).toBe(1);
  await page.locator("#play").click();
  await page.locator("#stop").click();
  await expect.poll(() => castle.hits("POST /api/stop")).toBe(1);
  await expect(page.locator("#playLabel")).toHaveText("Play");
  await expect(page.locator("#tc")).toContainText("0.00 /");
  // …and the chip learns the porch is idle within the action re-poll.
  await expect(page.locator("#devNow")).toHaveText("idle");

  await page.locator("#play").click();
  await page.keyboard.press("Escape");
  await expect.poll(() => castle.hits("POST /api/stop")).toBe(2);

  await page.locator("#devMirror").uncheck();
  await page.locator("#play").click();
  await expect(page.locator("#playLabel")).toHaveText("Pause");
  await page.locator("#stop").click();
  // Stop is handled in the click: the local transport flips and any mirror
  // POST is already in flight by the time the label reads Play.
  await expect(page.locator("#playLabel")).toHaveText("Play");
  expect(castle.hits("POST /api/stop")).toBe(2);
});

test("mirroring can be switched off and on mid-show without stopping it", async ({ page }) => {
  const castle = await fakeCastle(page);
  await page.goto("/");
  await expect(page.locator("#deviceChip")).toBeVisible();
  await page.locator("#play").click();
  await expect(page.locator("#playLabel")).toHaveText("Pause");

  await page.locator("#devMirror").uncheck();
  await page.locator("button.scene", { hasText: "Storm" }).first().click();
  // The pick lands locally (the stage names it) and posts nothing.
  await expect(page.locator("#stageNote")).toContainText("Storm");
  expect(castle.hits("/api/scene")).toBe(0);
  await expect(page.locator("#playLabel")).toHaveText("Pause");   // still running
  await expect(page.locator("#headTxt")).toContainText("not mirroring");

  await page.locator("#devMirror").check();
  await page.locator("button.scene", { hasText: "Vigil" }).first().click();
  await expect.poll(() => castle.hits("/api/scene?s=vigil")).toBe(1);
  await expect(page.locator("#playLabel")).toHaveText("Pause");
  await expect(page.locator("#headTxt")).toContainText("· mirroring");
  await page.locator("#stop").click();
});

test("the transport's Pause stops a sounding row preview instead of starting the scene",
    async ({ page }) => {
  await fakeCastle(page);
  await page.goto("/");
  await expect(page.locator(MP3_ROW)).toBeVisible();
  await page.locator(`${MP3_ROW} button[data-act="play"]`).click();
  await expect(page.locator(MP3_ROW)).toHaveClass(/playing/);
  // The button reflects the preview: it says Pause and means THAT.
  await expect(page.locator("#playLabel")).toHaveText("Pause");
  await page.locator("#play").click();
  await expect(page.locator(MP3_ROW)).not.toHaveClass(/playing/);
  await expect(page.locator("#playLabel")).toHaveText("Play");
  await expect(page.locator("#tc")).toContainText("0.00 /");
  await expect.poll(() => sounding(page)).toBe(0);

  // And the other way round: a preview started while the scene runs takes
  // the speakers over — the scene file pauses, one source at a time.
  await page.locator("#play").click();
  await expect(page.locator("#playLabel")).toHaveText("Pause");
  await page.locator(`${MP3_ROW} button[data-act="play"]`).click();
  await expect(page.locator(MP3_ROW)).toHaveClass(/playing/);
  await expect.poll(() => sounding(page)).toBe(1);
  await page.locator("#stop").click();
  await expect(page.locator(MP3_ROW)).not.toHaveClass(/playing/);
  await expect.poll(() => sounding(page)).toBe(0);
});

test("on a phone the dock opens its panel without burying the stage", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await fakeCastle(page, [{ name: "wicked_winds.mp3", size: 287744, dir: false }]);
  await page.goto("/");
  const chip = page.locator("#deviceChip");
  await expect(chip).toBeVisible();
  await page.locator("#devMore").click();
  const panel = page.locator("#devicePanel");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("wicked_winds.mp3");
  const box = (await panel.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(375 + 1);
  expect(box.height).toBeLessThan(812 * 0.5);       // the stage stays reachable
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  // A toast lands inside the viewport too.
  await page.locator("#devStop").click();
  const toast = page.locator("#toasts > div").first();
  await expect(toast).toBeVisible();
  const tb = (await toast.boundingBox())!;
  expect(tb.x + tb.width).toBeLessThanOrEqual(375 + 1);
  await panel.locator("#dpClose").click();
  await expect(panel).toBeHidden();
});

test("the SD budget counts the imported library, not what the card happens to hold",
    async ({ page }) => {
  // A castle-sent track is still ONE track in the library; a stranger on the
  // card is not in the library at all. Neither may move the card ledger.
  await fakeCastle(page, [
    { name: `${MP3_ID}.mp3`, size: await realBytes(page, MP3_ID), dir: false },
    { name: "phantom_waltz.mp3", size: 512000, dir: false },
  ]);
  await page.goto("/");
  await expect(page.locator(".trk--card")).toHaveCount(1);
  const n = Number(/(\d+) imported/.exec((await page.locator("#trkCount").textContent()) ?? "")?.[1]);
  expect(n).toBeGreaterThan(0);
  await page.locator('#budTabs button[data-bud="sd"]').click();
  await expect(page.locator("#budRows")).toContainText(`imported library (${n} tracks)`);
});
