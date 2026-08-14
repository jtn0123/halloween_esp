/**
 * The style lab and band mixer, end to end.
 *
 * The panel's one hard promise is honesty: what you audition is what the
 * export writes — knobs and flavours ship, the B engine and the mute
 * buttons never do. Every test here intercepts the real /api/scene POST
 * and reads the YAML the browser actually sent, because that string is the
 * contract with the Python generators the fuzz suite guards.
 */

import { type Page, type Route } from "@playwright/test";
import { test, expect } from "./fixtures.js";

const MP3 = "e2e_beats";

const row = (page: Page, id: string) => page.locator(`.trk[data-id="${id}"]`);
const lab = (page: Page) => page.locator(".stylelab");

/** Open the clip editor and unfold the style lab. */
async function openLab(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.locator("#trkMode")).toHaveText(/studio/);
  await row(page, MP3).locator(".trk__nm").click();
  await expect(page.locator("#trkWave")).toContainText(/start 0:00/);
  await lab(page).locator("summary").click();
  await expect(lab(page).locator(".stylelab__ab")).toBeVisible();
}

/** Click "Make scene" with the write stubbed; resolve the YAML it posted. */
async function exportedYaml(page: Page): Promise<string> {
  let yaml = "";
  await page.route("**/api/scene", async (route: Route) => {
    yaml = (route.request().postDataJSON() as { yaml: string }).yaml;
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ ok: true, id: MP3, replaced: false,
                             scenes: ["vigil", MP3], log: "" }),
    });
  });
  await row(page, MP3).locator('button[data-act="scene"]').click();
  await expect(page.locator("#trkNote")).toContainText("scenes.yaml");
  await page.unroute("**/api/scene");
  expect(yaml).not.toBe("");
  return yaml;
}

test("flavour toggles ship in the export", async ({ page }) => {
  await openLab(page);
  await lab(page).locator(".stylelab__flav-drift").check();
  await lab(page).locator(".stylelab__flav-takeover").check();
  const yaml = await exportedYaml(page);
  // Every pulse stream carries the flags the generators expand (#1, #2).
  expect(yaml).toContain("drift: true");
  expect(yaml).toContain("takeover: true");
});

test("the B engine is comparison only — exports always use A", async ({ page }) => {
  await openLab(page);
  const ab = lab(page).locator(".stylelab__ab");
  await ab.click();
  await expect(ab).toHaveText(/B · classic/);
  const yaml = await exportedYaml(page);
  // The current engine's signatures, not the flat baseline's: the mid band's
  // attack swell and its violet triad exist only in A.
  expect(yaml).toContain("attack_ms: 90");
  expect(yaml).toContain("[0.55, 0, 1, 0]");
  expect(yaml).toContain("pixels_by_vel: true");
});

test("a trimmed knob lands in the exported YAML at the trimmed value",
  async ({ page }) => {
    await openLab(page);
    // Low band intensity ×0.5: BAND_STYLE 0.85 → 0.425 in the pulse block.
    await lab(page).locator(".stylelab__knob").first().fill("0.5");
    await expect(lab(page).locator(".stylelab__val").first()).toHaveText("×0.50");
    const yaml = await exportedYaml(page);
    expect(yaml).toMatch(/synth: onset_low[^\n]*intensity: 0\.425/);
  });

test("Copy as TS shows the effective style literal", async ({ page }) => {
  await openLab(page);
  await lab(page).locator(".stylelab__knob").first().fill("0.5");
  await lab(page).getByRole("button", { name: "Copy as TS" }).click();
  const out = lab(page).locator(".stylelab__ts");
  await expect(out).toBeVisible();
  await expect(out).toContainText("export const BAND_STYLE");
  await expect(out).toContainText("intensity: 0.425");
});

test("Reset returns engine A, neutral knobs and no flavours", async ({ page }) => {
  await openLab(page);
  await lab(page).locator(".stylelab__ab").click();
  await lab(page).locator(".stylelab__flav-drift").check();
  await lab(page).locator(".stylelab__knob").first().fill("0.5");

  await lab(page).getByRole("button", { name: "Reset" }).click();
  await expect(lab(page).locator(".stylelab__ab")).toHaveText(/A · current/);
  await expect(lab(page).locator(".stylelab__flav-drift")).not.toBeChecked();
  await expect(lab(page).locator(".stylelab__val").first()).toHaveText("×1.00");
  const yaml = await exportedYaml(page);
  expect(yaml).not.toContain("drift: true");
  expect(yaml).toMatch(/synth: onset_low[^\n]*intensity: 0\.85/);
});

test("solo thins the audition but never the export", async ({ page }) => {
  await openLab(page);

  // Full audition first, for the baseline cue count.
  const audition = page.locator("#trkWave button").first();
  await audition.click();
  const summary = page.locator("#sheetFold summary");
  await expect(summary).toContainText(/\d+ cues/);
  const cueCount = async (): Promise<number> => {
    const m = (await summary.textContent())?.match(/(\d+) cues?/);
    return m ? Number(m[1]) : NaN;   // NaN: not settled yet, keep polling
  };
  const full = await cueCount();
  expect(full).toBeGreaterThan(0);
  await audition.click();

  // Solo the mid band: the audition drops the other bands' strikes.
  await page.locator(".bandcfg__row").nth(1).locator(".bandcfg__solo").click();
  await audition.click();
  await expect.poll(cueCount).toBeLessThan(full);
  await audition.click();

  // The export still carries every band — mute is a listening tool (#13).
  const yaml = await exportedYaml(page);
  expect(yaml).toContain("synth: onset_low");
  expect(yaml).toContain("synth: onset_mid");
  expect(yaml).toContain("synth: onset_high");
});
