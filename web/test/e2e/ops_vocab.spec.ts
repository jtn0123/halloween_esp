/**
 * The operator's view of the desk's words and states (judge B, JB1-9/11):
 * plain labels where a hand goes during a show, developer vocabulary one
 * fold away and named as such, keyboard help that lists every live key,
 * a solo that looks soloed, a card size that is measured when a castle is
 * there to measure it, and a footer that agrees with the Rig panel.
 */

import { test, expect, fakeCastle } from "./fixtures.js";
import { MP3_ID } from "./global-setup.js";

test("the keyboard help names every live key and which ones reach the castle",
  async ({ page }) => {
    await page.goto("/");
    const hint = page.locator("#keyHint");
    for (const k of ["Space", "K", "Home", "R", "M", "Esc", "1", "9"]) {
      await expect(hint.locator("kbd", { hasText: new RegExp(`^${k}$`) }).first()).toBeVisible();
    }
    await expect(hint).toContainText(/mirroring[\s\S]*drive the porch/);
  });

test("operator panels use operator words; the engine A/B is a developer fold",
  async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("#rigYaml")).toHaveText("Copy settings for the castle");
    await expect(page.locator("#rigYaml")).toHaveAttribute("title", /make generate/);
    await expect(page.locator("#rigPanel")).not.toContainText("make generate");
    await page.locator("#zdFold summary").click();
    await expect(page.locator("#zdYaml")).toHaveText("Copy these zone settings");
    await expect(page.locator("#zoneDesigner")).not.toContainText("YAML");
    // The studio strip names the host it is actually served on.
    await expect(page.locator(".trk-srvtxt b")).toHaveText(/^127\.0\.0\.1:\d+$/);

    await page.locator(`.trk[data-id="${MP3_ID}"] .trk__nm`).click();
    const lab = page.locator(".stylelab");
    await expect(lab.locator("> summary")).toHaveText(/^Light style/);
    await expect(lab.locator("> summary")).not.toContainText(/A\/B|engine|knobs/);
    const dev = lab.locator(".stylelab__dev");
    await expect(dev.locator("> summary")).toContainText(/^developer/);
    await expect(dev.locator(".stylelab__ab")).toBeHidden();
    await expect(dev.getByRole("button", { name: "Copy as TS" })).toBeHidden();
    await dev.locator("> summary").click();
    await expect(dev.locator(".stylelab__ab")).toBeVisible();
  });

test("band rows: windows named as on the porch, dots explained, solo shows as solo",
  async ({ page }) => {
    await page.goto("/");
    await page.locator(`.trk[data-id="${MP3_ID}"] .trk__nm`).click();
    const rows = page.locator(".bandcfg__row");
    await expect(rows).toHaveCount(3);
    await expect(rows.first().locator(".bandcfg__zone option")).toHaveText(
      ["Tower L", "Tower R", "Doorway"]);
    for (let i = 0; i < 3; i++) {
      await expect(rows.nth(i).locator(".bandcfg__dot")).toHaveAttribute("title", /band/);
    }
    // Wide enough that "Tower L" and "Tower R" are not both "Tower".
    const sel = rows.first().locator(".bandcfg__zone");
    const fits = await sel.evaluate((el) => (el as HTMLSelectElement).scrollWidth
      <= (el as HTMLSelectElement).clientWidth + 1);
    expect(fits).toBe(true);

    const mid = rows.nth(1);
    await mid.locator(".bandcfg__solo").click();
    await expect(mid.locator(".bandcfg__solo")).toHaveClass(/\bon\b/);
    await expect(mid.locator(".bandcfg__solo")).toHaveAttribute("aria-pressed", "true");
    // …and the OTHER bands' mute buttons do not pretend to be pressed.
    await expect(rows.nth(0).locator(".bandcfg__mute")).not.toHaveClass(/\bon\b/);
    await expect(rows.nth(2).locator(".bandcfg__mute")).not.toHaveClass(/\bon\b/);
    await mid.locator(".bandcfg__solo").click();
    await expect(mid.locator(".bandcfg__solo")).not.toHaveClass(/\bon\b/);
  });

test("the SD budget measures the card when a castle reports it", async ({ page }) => {
  // 29.5 GB, as the firmware's sd_total_kb would say.
  const castle = await fakeCastle(page, [], { sd_total_kb: 31_000_000 });
  await page.goto("/");
  await expect(page.locator("#deviceChip")).toBeVisible();
  await page.locator('#budTabs button[data-bud="sd"]').click();
  await expect(page.locator("#budHead")).toContainText("of 29.56 GB");
  await page.locator('#budRows .budget__row[data-key="free"]').click();
  await expect(page.locator(".budget__pickmeta")).toContainText("as the castle reports it");
  await expect(page.locator(".budget__pickmeta")).not.toContainText("assumed");
  // Castle gone: back to the stated assumption, and it says so.
  castle.up = false;
  await page.locator("#devStop").click({ force: true }).catch(() => undefined);
  await expect(page.locator("#budHead")).toContainText("of 32.00 GB");
  await expect(page.locator(".budget__pickmeta")).toContainText("assumed");
});

test("the SD budget says 'assumed' with no castle at all", async ({ page }) => {
  await page.goto("/");
  await page.locator('#budTabs button[data-bud="sd"]').click();
  await expect(page.locator("#budHead")).toContainText("of 32.00 GB");
  await page.locator('#budRows .budget__row[data-key="free"]').click();
  await expect(page.locator(".budget__pickmeta")).toContainText("assumed");
});

test("the castle panel links the phone remote; the footer agrees with the Rig",
  async ({ page }) => {
    await fakeCastle(page);
    await page.goto("/");
    await page.locator("#devMore").click();
    const link = page.locator("#dpRemote");
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", "/remote");
    await expect(link).toHaveText(/phone remote/);
    await expect(page.locator(".foot")).not.toContainText("three NeoPixel Jewels");
    await expect(page.locator(".foot")).toContainText("Rig panel says which");
  });
