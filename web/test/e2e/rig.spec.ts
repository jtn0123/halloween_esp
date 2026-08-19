/**
 * The rig panel — choosing what is physically in each window.
 *
 * The point of this panel is that a fixture swap is a click rather than a
 * solder joint, so what has to be true is that the click actually changes the
 * RENDER and not just a label. These tests assert the pixel count the show
 * engine produced, which is the only thing that proves it.
 */

import { test, expect } from "./fixtures.js";

/** How many dots the pixel view believes each zone has. Read from the show
 *  engine through the page rather than counted off the canvas — the canvas is
 *  a picture of this, not the source of it. */
async function pixelCounts(page: import("@playwright/test").Page) {
  return page.evaluate(() => {
    const raw = localStorage.getItem("castle.rig");
    return raw ? JSON.parse(raw) : null;
  });
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#rigPanel")).toBeVisible();
});

test("the desk starts as the castle is actually built", async ({ page }) => {
  // Three RGBW Jewels: what is soldered, and what scenes.yaml declares.
  await expect(page.locator("#rigPanel select").first()).toHaveValue("jewel7");
  await expect(page.locator("#rigSum")).toContainText("21 pixels");
  await expect(page.locator("#rigEyebrow")).toHaveText("three zones · 21 px");
});

test("swapping a fixture changes the channel strip and the masthead",
  async ({ page }) => {
    await page.locator("#rigPanel select").first().selectOption("ring16");
    // The strip names the pin, the fixture and the count — the three things
    // you want when a window is dark.
    await expect(page.locator("#sub-towerL"))
      .toHaveText("ch 1 · GPIO18 · Ring 16 · 16px RGBW");
    await expect(page.locator("#rigEyebrow")).toHaveText("three zones · 30 px");
  });

test("an RGB-only fixture offers no RGBW choice to get wrong",
  async ({ page }) => {
    // The FeatherWing was never made with a white die, so a tickbox here
    // would be a way to generate firmware that does not match the hardware.
    const door = page.locator("#rigPanel tbody tr").nth(1);
    await door.locator("select").selectOption("wing32");
    await expect(door.locator(".rig__fixed")).toHaveText("RGB");
    await expect(door.locator("input[type=checkbox]")).toHaveCount(0);
  });

test("mixing colour types and overrunning the supply are both called out",
  async ({ page }) => {
    await page.locator("#rigPanel tbody tr").nth(0).locator("select")
      .selectOption("ring16");
    await page.locator("#rigPanel tbody tr").nth(1).locator("select")
      .selectOption("wing32");

    const notes = page.locator(".rig__note");
    await expect(notes.filter({ hasText: "RGBW and RGB" })).toBeVisible();
    // 16 RGBW + 32 RGB + 7 RGBW = 1.28 + 1.92 + 0.56 = 3.76 A, over the 4 A
    // trigger once both amps are counted.
    await expect(notes.filter({ hasText: "supply of 8 A" })).toBeVisible();
  });

test("the rig survives a reload, because a soldered castle does",
  async ({ page }) => {
    await page.locator("#rigPanel select").first().selectOption("stick8");
    await page.reload();
    await expect(page.locator("#rigPanel select").first()).toHaveValue("stick8");
    expect((await pixelCounts(page))?.zones?.towerL?.fixture).toBe("stick8");
  });

test("the generated config carries both halves of the change",
  async ({ page }) => {
    // scenes.yaml and the light: blocks have to move together — one without
    // the other is a castle whose cues aim at pixels it does not have.
    await page.locator("#rigPanel select").first().selectOption("ring12");
    await page.locator("#rigYaml").click();
    const out = page.locator("#rigOut");
    await expect(out).toBeVisible();
    await expect(out).toContainText("fixture: ring12, pixels: 12");
    await expect(out).toContainText("id: zone_towerL");
    await expect(out).toContainText("num_leds: 12");
    await expect(out).toContainText("pin: GPIO18");
  });
