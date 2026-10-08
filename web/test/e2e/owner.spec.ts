/**
 * The owner's page (firmware/sd_web_owner.h, v5.75) in a real browser: the
 * emulator serves kOwnerPage byte for byte (castle_emu_flash.py), so this is
 * the page a buyer's castle serves, its script run for the first time
 * anywhere but a C string. The emulator is the hard case on purpose — the
 * buyer build, no card, and a boot that was a brownout — because that is the
 * castle someone opens this page about.
 *
 * Straight to the castle, no studio: /owner is the castle's own page and
 * the studio does not relay it. Own port (the lane's CASTLE_E2E_PORT +5),
 * own process, killed in afterAll.
 */

import { spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test, expect } from "./fixtures.js";
import { lanePort } from "./ports.js";

const ROOT = resolve(__dirname, "../../..");
const PY = join(ROOT, ".venv", "bin", "python");

let CASTLE = "";
let emu: ChildProcess | undefined;

type Status = { vol_max: number; quiet: string; tz: string; volume: number };
const status = async (): Promise<Status> =>
  (await (await fetch(`${CASTLE}/api/status`)).json()) as Status;

test.beforeAll(async () => {
  const port = await lanePort(5);
  CASTLE = `http://127.0.0.1:${port}`;
  const card = mkdtempSync(join(tmpdir(), "castle-e2e-owner-card-"));
  emu = spawn(PY, [join(ROOT, "tools", "castle_emu.py"), String(port), "--dir", card,
                   "--variant", "buyer", "--no-sd", "--reset", "9"], { stdio: "ignore" });
  const end = Date.now() + 15000;
  while (Date.now() < end) {
    try { if ((await fetch(`${CASTLE}/api/status`)).ok) return; } catch { /* not yet */ }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`the emulator never answered on ${CASTLE}`);
});

test.afterAll(() => {
  emu?.kill();
});

test("a buyer's castle with no card, after a brownout, says so in plain words", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`${CASTLE}/owner`);
  await expect(page).toHaveTitle("Castle");
  const alerts = page.locator("#alerts");
  await expect(alerts).toContainText("No SD card — the show is on the card");
  await expect(alerts).toContainText("a brownout");
  await expect(page.locator("#facts")).toContainText("Last restart: the power dipped");
  await expect(page.locator("#v")).toContainText("buyer");
  await expect(page.locator("#pir")).toHaveText("Motion sensor: not fitted on this castle.");
  await expect(page.locator("#files")).toContainText("No card to read.");
  expect(errors).toEqual([]);
});

test("every setting the page saves is the castle's setting", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`${CASTLE}/owner`);
  await expect(page.locator("#facts")).toContainText("Last restart");

  await page.locator("#vm").fill("40");
  await page.locator("#vm").locator("xpath=..").getByRole("button", { name: "Save" }).click();
  await expect.poll(async () => (await status()).vol_max).toBe(40);
  await expect(page.locator("#volv")).toContainText("(limit 40%)", { timeout: 8000 });

  await page.locator("#qon").check();
  await page.locator("#qon").locator("xpath=..").getByRole("button", { name: "Save" }).click();
  await expect.poll(async () => (await status()).quiet).toBe("22:00-07:00");

  await page.locator("#tzs").selectOption({ label: "US Eastern" });
  await expect(page.locator("#tzc")).toHaveValue("EST5EDT,M3.2.0,M11.1.0");
  await page.locator("#tzc").locator("xpath=..").getByRole("button", { name: "Save" }).click();
  await expect.poll(async () => (await status()).tz).toBe("EST5EDT,M3.2.0,M11.1.0");
  await expect(page.locator("#msg")).toHaveText("Saved.");

  // A refusal is the castle's own words, not a silent no.
  await page.locator("#vm").fill("0");
  await page.locator("#vm").locator("xpath=..").getByRole("button", { name: "Save" }).click();
  await expect(page.locator("#msg")).toHaveText("The castle said: bad vol_max");
  expect((await status()).vol_max).toBe(40);
  expect(errors).toEqual([]);
});

test("Report a problem downloads one file with everything the castle knows", async ({ page }) => {
  await page.goto(`${CASTLE}/owner`);
  await expect(page.locator("#facts")).toContainText("Last restart");
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.locator("#rep").click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/^castle-report-\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d\.txt$/);
  const text = readFileSync(await download.path(), "utf-8");
  expect(text).toMatch(/^Castle problem report\nmade \d{4}-/);
  expect(text).toContain("· board feather-s3-4m2p · build buyer");
  for (const part of ["/api/status", "/api/health", "/api/events", "/api/bootlog"]) {
    expect(text).toContain(`== ${part} ==`);
  }
  expect(text).toContain('"last_reset":"BROWNOUT"');
  await expect(page.locator("#msg")).toHaveText("Report saved.");
});
