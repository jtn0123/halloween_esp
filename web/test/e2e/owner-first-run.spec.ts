/**
 * The owner's page (firmware/sd_web_owner.h) on the castle a buyer unboxes:
 * the buyer build, a card in the slot and nothing on it. The first thing it
 * must say is how to get a song there — and once a song is there, in any
 * format the castle can play, it must list it. v5.75 filtered the card by
 * mp3 and wav only, so a card of Castle Radio's opus imports read as empty;
 * v5.76 takes what castle_feather_s3.yaml decodes.
 *
 * And the card a castle is SOLD with (tools/buyer_card.py) carries the
 * firmware's notices and the GPLv3 written source offer under licenses/;
 * v5.77's page links each one the card holds, and only those.
 *
 * The emulator serves kOwnerPage byte for byte (castle_emu_flash.py). Own
 * port (the lane's CASTLE_E2E_PORT +6), own temp card, killed in afterAll.
 */

import { spawn, type ChildProcess } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test, expect } from "./fixtures.js";
import { lanePort } from "./ports.js";

const ROOT = resolve(__dirname, "../../..");
const PY = join(ROOT, ".venv", "bin", "python");

let CASTLE = "";
let CARD = "";
let emu: ChildProcess | undefined;

test.beforeAll(async () => {
  const port = await lanePort(6);
  CASTLE = `http://127.0.0.1:${port}`;
  CARD = mkdtempSync(join(tmpdir(), "castle-e2e-first-run-card-"));
  emu = spawn(PY, [join(ROOT, "tools", "castle_emu.py"), String(port), "--dir", CARD,
                   "--variant", "buyer"], { stdio: "ignore" });
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

test("an empty card says how to add the first song, and an opus song is listed once it lands", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`${CASTLE}/owner`);
  const files = page.locator("#files");
  await expect(files).toHaveText("No songs on the card yet — add some with Castle Tools.");
  await expect(page.locator("#alerts")).not.toContainText("No SD card");

  const put = await fetch(`${CASTLE}/api/files/first%20song.opus`, {
    method: "PUT",
    body: new Uint8Array(4096).fill(0x4f),
  });
  expect(put.ok).toBe(true);
  await page.reload();
  await expect(files.locator("li")).toHaveCount(1);
  await expect(files).toContainText("first song.opus");
  await expect(files.getByRole("button", { name: "▶" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("the foot links the card's licence files, and only the ones it holds", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`${CASTLE}/owner`);
  await expect(page.locator("#files li")).toHaveCount(1); // the page has read the card
  await expect(page.getByRole("link", { name: "Castle page" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Licences" })).toHaveCount(0);

  // The names tools/buyer_card.py writes (CARD_NOTICES, CARD_OFFER).
  mkdirSync(join(CARD, "licenses"));
  copyFileSync(join(ROOT, "licenses", "THIRD-PARTY-NOTICES-firmware.txt"),
               join(CARD, "licenses", "THIRD-PARTY-NOTICES.txt"));
  await page.reload();
  await expect(page.getByRole("link", { name: "Licences" })).toHaveAttribute(
    "href", "/sd/licenses/THIRD-PARTY-NOTICES.txt");
  await expect(page.getByRole("link", { name: "Source code" })).toHaveCount(0);

  writeFileSync(join(CARD, "licenses", "SOURCE-OFFER.txt"), "WRITTEN OFFER FOR SOURCE CODE\n");
  await page.reload();
  const offer = page.getByRole("link", { name: "Source code" });
  await expect(offer).toHaveAttribute("href", "/sd/licenses/SOURCE-OFFER.txt");
  // Shown as text, not handed to the downloads folder (.txt was octet-stream).
  const res = await page.request.get(`${CASTLE}/sd/licenses/SOURCE-OFFER.txt`);
  expect(res.headers()["content-type"]).toBe("text/plain; charset=utf-8");
  await offer.click();
  await expect(page.locator("body")).toContainText("WRITTEN OFFER FOR SOURCE CODE");
  // The licence files are not songs: the song list still holds the one opus.
  await page.goto(`${CASTLE}/owner`);
  await expect(page.locator("#files li")).toHaveCount(1);
  expect(errors).toEqual([]);
});
