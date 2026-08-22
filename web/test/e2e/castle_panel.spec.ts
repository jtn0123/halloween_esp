/**
 * The castle panel's remaining controls: the evening show, the motion
 * sensor, drop-upload, and the SD-less state. device.spec.ts covers the
 * lights override, the boot log and the ▶ rows; this is the rest of the
 * panel, asserting both the query the castle gets and what the panel shows
 * once the castle has answered.
 */

import { test, expect, fakeCastle } from "./fixtures.js";

test("the show button starts and stops the evening playlist", async ({ page }) => {
  const castle = await fakeCastle(page, [], { show_on: false });
  await page.goto("/");
  await page.locator("#devMore").click();
  const btn = page.locator("#dpPlaylist");
  await expect(btn).toHaveText("▶ start the show");
  await btn.click();
  await expect.poll(() => castle.hits("POST /api/show/start")).toBe(1);
  // The stub flips show_on; the panel re-renders from the castle's truth.
  castle.status["scene"] = "vigil";
  await expect(page.locator("#dpPlaylist")).toHaveText("■ stop the show");
  await expect(page.locator("#devicePanel")).toContainText("now: vigil");
  await page.locator("#dpPlaylist").click();
  await expect.poll(() => castle.hits("POST /api/show/stop")).toBe(1);
  await expect(page.locator("#dpPlaylist")).toHaveText("▶ start the show");
});

test("the motion sensor controls post exactly their own field", async ({ page }) => {
  const castle = await fakeCastle(page);
  await page.goto("/");
  await page.locator("#devMore").click();
  const panel = page.locator("#devicePanel");
  await expect(panel.locator("#dpPirArm")).not.toBeChecked();
  await expect(panel.locator("#dpPirScene")).toHaveValue("approach");
  await expect(panel.locator("#dpPirCool")).toHaveValue("60");

  await panel.locator("#dpPirArm").check();
  await expect.poll(() => castle.hits("POST /api/pir?armed=1")).toBe(1);
  await expect(page.locator("#toasts")).toContainText("motion sensor armed");

  await panel.locator("#dpPirScene").selectOption("storm");
  await expect.poll(() => castle.hits("POST /api/pir?scene=storm")).toBe(1);

  await panel.locator("#dpPirCool").fill("120");
  await panel.locator("#dpPirCool").press("Tab");
  await expect.poll(() => castle.hits("POST /api/pir?cooldown=120")).toBe(1);
  // Each POST carried ONE field — never the others' values along for the ride.
  expect(castle.calls.filter((c) => c.includes("/api/pir?") && c.includes("&"))).toHaveLength(0);

  // The settings survive a close/open: the castle remembers, the panel re-reads.
  await panel.locator("#dpClose").click();
  await page.locator("#devMore").click();
  await expect(page.locator("#dpPirArm")).toBeChecked();
  await expect(page.locator("#dpPirScene")).toHaveValue("storm");
  await expect(page.locator("#dpPirCool")).toHaveValue("120");
});

test("dropping a file on the panel uploads it to the card root", async ({ page }) => {
  const castle = await fakeCastle(page, []);
  await page.goto("/");
  await page.locator("#devMore").click();
  const drop = page.locator("#dpDrop");
  await expect(drop).toContainText("drop audio files here");
  await expect(page.locator("#dpFiles")).toContainText("no tracks on the card yet");

  // A synthetic drop with a real File — the handler reads e.dataTransfer.files.
  await drop.evaluate((el) => {
    const dt = new DataTransfer();
    dt.items.add(new File([new Uint8Array(2048)], "dropped_song.mp3", { type: "audio/mpeg" }));
    el.dispatchEvent(new DragEvent("dragover", { dataTransfer: dt, bubbles: true, cancelable: true }));
    el.dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
  });
  await expect.poll(() => castle.hits("PUT /api/files/dropped_song.mp3")).toBe(1);
  expect(castle.files.find((f) => f.name === "dropped_song.mp3")?.size).toBe(2048);
  // The panel re-lists the card with the new file on it.
  await expect(page.locator("#dpFiles")).toContainText("dropped_song.mp3");
  await expect(page.locator("#dpFiles")).toContainText("2 KB");
  // …and the merged Library below learns about it too, as a card-only row.
  await expect(page.locator(".trk--card[data-card='dropped_song.mp3']")).toBeVisible();
  // The panel's ✕ goes the same way: gone from the card, gone from the list.
  page.on("dialog", (d) => void d.accept());
  await page.locator("#dpFiles [data-del]").first().click();
  await expect.poll(() => castle.hits("DELETE /api/files/dropped_song.mp3")).toBe(1);
  await expect(page.locator(".trk--card[data-card='dropped_song.mp3']")).toHaveCount(0);
  await expect(page.locator("#dpFiles")).toContainText("no tracks on the card yet");
});

test("a castle without its card says so, and the library makes no claims", async ({ page }) => {
  const castle = await fakeCastle(page, [], { sd_mounted: false, sd_free_kb: 0 });
  // No card: the castle's /api/files answers an error, not an empty list.
  await page.route("**/api/files", (r) =>
    r.fulfill({ status: 503, contentType: "text/plain", body: "no SD card" }));
  await page.goto("/");
  await expect(page.locator("#deviceChip")).toContainText("no SD");
  await expect(page.locator("#headTxt")).toContainText("no SD");
  await expect(page.locator(".trk__badge", { hasText: "on castle" })).toHaveCount(0);
  await expect(page.locator("button[data-act='send']")).toHaveCount(0);
  await expect(page.locator("#trkSync")).toBeHidden();
  await page.locator("#devMore").click();
  await expect(page.locator("#dpFiles")).toContainText("no SD card");
  // Sending is impossible too: the chip's empty card is not a destination.
  expect(castle.hits("PUT /api/files")).toBe(0);
});

test("what the castle says is printed, never executed", async ({ page }) => {
  // The chip and the panel are built with innerHTML, and everything in them
  // is the CASTLE's word: a version string, a scene id, and file names read
  // off an SD card that anyone with the card (or the unauthenticated PUT the
  // security note accepts) can write to. A name is a name, not markup.
  const bomb = `<img src=x onerror="window.__x=1">`;
  const castle = await fakeCastle(page,
    [{ name: `${bomb}.mp3`, size: 1024, dir: false }],
    { version: bomb, scene: bomb, track: bomb });
  await page.goto("/");
  await expect(page.locator("#deviceChip")).toBeVisible();
  await page.locator("#devMore").click();
  await expect(page.locator(".dp__file-nm")).toBeVisible();
  // The payload arrived — the panel and chip are showing the castle's strings…
  await expect(page.locator(".dp__file-nm")).toContainText("onerror");
  await expect(page.locator("#devNow")).toContainText("onerror");
  // …as text. No element was made from it, and nothing ran.
  expect(await page.locator("#castleDock img, #devicePanel img").count()).toBe(0);
  expect(await page.evaluate(() => (window as unknown as { __x?: number }).__x)).toBeUndefined();
  expect(castle.hits("/api/status")).toBeGreaterThan(0);
});
