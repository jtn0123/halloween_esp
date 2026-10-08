/**
 * The castle key (firmware v5.74) at the desk. The desk never holds the key:
 * the studio's relay sends the one its store remembers for the castle
 * (core/src/studio_key.rs, tests/test_studio_key_rs.py). What is asserted
 * here is the desk's half — a refusal says where the key goes, a send to a
 * castle that would refuse it is refused before a byte moves, and the 🏰
 * panel's settings section hands a typed key to the studio once, empties the
 * field, and never paints it.
 */

import type { Page } from "@playwright/test";

import { test, expect, fakeCastle } from "./fixtures.js";
import { MP3_ID } from "./global-setup.js";

const KEY_REQUIRED = "This castle has a key — enter it in Settings";
const BRIDGED = { bridged: "127.0.0.1:8093", locked: true };
const row = (id: string) => `.trk[data-id="${id}"]`;

/** The studio's /studio/castle-key, stubbed: the castle's key is `right`. */
async function keyStudio(page: Page, right = "s3cret", pinned = false):
    Promise<{ action: string; key: string }[]> {
  const posted: { action: string; key: string }[] = [];
  let remembered = false;
  await page.route("**/studio/castle-key", async (route) => {
    const base = { ok: true, host: BRIDGED.bridged, pinned };
    if (route.request().method() === "GET") {
      return route.fulfill({ json: { ...base, remembered } });
    }
    const body = route.request().postDataJSON() as { action: string; key: string };
    posted.push(body);
    if (body.action === "use" && body.key !== right) {
      return route.fulfill({ status: 401, json: { error: "that is not this castle's key" } });
    }
    remembered = body.action !== "clear";
    return route.fulfill({ json: { ...base, remembered } });
  });
  return posted;
}

test("a refused change says where the key goes — the panel's toggles and its drop zone", async ({ page }) => {
  const castle = await fakeCastle(page, [], BRIDGED);
  castle.keyed = true;
  await keyStudio(page);
  await page.goto("/");
  await page.locator("#devMore").click();
  await page.locator("#dpPirArm").check();
  await expect(page.locator("#toasts")).toContainText(`motion sensor armed failed — ${KEY_REQUIRED}`);

  const drop = page.locator("#dpDrop");
  await drop.evaluate((el) => {
    const dt = new DataTransfer();
    dt.items.add(new File([new Uint8Array(512)], "keyed.mp3", { type: "audio/mpeg" }));
    el.dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
  });
  await expect(drop).toHaveText(`✗ keyed.mp3 — ${KEY_REQUIRED}`);
  expect(castle.files).toHaveLength(0);
});

test("the settings section uses, sets and clears through the studio, and never shows the key", async ({ page }) => {
  await fakeCastle(page, [], BRIDGED);
  const posted = await keyStudio(page);
  await page.goto("/");
  await page.locator("#devMore").click();
  const state = page.locator("#dpKeyState");
  const msg = page.locator("#dpKeyMsg");
  const field = page.locator("#dpKey");
  await expect(state).toHaveText("This castle has a key. No key is remembered for it on this computer.");
  await expect(field).toHaveAttribute("type", "password");

  await field.fill("wrong");
  await page.locator("#dpKeyUse").click();
  await expect(msg).toHaveText("that is not this castle's key");
  await expect(field).toHaveValue("");             // a refused key does not linger

  await field.fill(" s3cret ");
  await page.locator("#dpKeyUse").click();
  await expect(msg).toHaveText("Key accepted · remembered for this castle");
  await expect(state).toHaveText("This castle has a key. A key is remembered for it on this computer.");

  await page.locator("#dpKeySet").click();
  await expect(msg).toHaveText("Type the key first");
  await field.fill("n3w-key");
  await page.locator("#dpKeySet").click();
  await expect(msg).toHaveText("The castle has its new key · remembered for this castle");

  await page.locator("#dpKeyClear").click();
  await expect(msg).toHaveText("The castle has no key now");
  await expect(state).toHaveText("This castle has no key. No key is remembered for it on this computer.");

  expect(posted).toEqual([
    { action: "use", key: "wrong" }, { action: "use", key: "s3cret" },
    { action: "set", key: "n3w-key" }, { action: "clear", key: "" },
  ]);
  expect(await page.locator("#devicePanel").innerHTML()).not.toMatch(/s3cret|n3w-key|wrong"/);
  await expect(page.locator("#toasts")).not.toContainText("s3cret");
});

test("a pinned key sends the owner to the settings file", async ({ page }) => {
  await fakeCastle(page, [], BRIDGED);
  await keyStudio(page, "s3cret", true);
  await page.goto("/");
  await page.locator("#devMore").click();
  await expect(page.locator("#dpKeyState")).toContainText("settings file (castle_key)");
});

test("not through the studio's relay, there is no store to hand a key to", async ({ page }) => {
  await fakeCastle(page, [], { locked: true });
  await page.goto("/");
  await page.locator("#devMore").click();
  await expect(page.locator("#dpPirArm")).toBeVisible();
  await expect(page.locator("#dpKey")).toHaveCount(0);
});

test("a send to a castle that would refuse it is refused before a byte moves", async ({ page }) => {
  const castle = await fakeCastle(page, [], { locked: true });
  castle.keyed = true;
  await page.goto("/");
  const send = page.locator(`${row(MP3_ID)} button[data-act='send']`);
  await send.click();
  const note = page.locator("#trkNote");
  await expect(note).toContainText(`Send of “${MP3_ID}” refused — ${KEY_REQUIRED}`);
  await expect(note).toHaveClass(/err/);
  expect(castle.hits("POST /api/key")).toBe(1);
  expect(castle.hits("PUT /api/files/")).toBe(0);

  // Locked since the castle last said so: the PUT's own 401 reads the same.
  delete castle.status["locked"];
  await expect(send).toHaveText("→ Castle");
  await send.click();
  await expect.poll(() => castle.hits("PUT /api/files/")).toBe(1);
  await expect(send).toHaveText("→ Castle");
  await expect(note).toContainText(`Send of “${MP3_ID}” refused — ${KEY_REQUIRED}`);
  expect(castle.hits("POST /api/key")).toBe(1);
});
