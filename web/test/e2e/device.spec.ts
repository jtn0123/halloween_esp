/**
 * Device mode — the chrome the desk grows when the castle itself serves it.
 *
 * No hardware in CI, so the castle is played by page.route: stub /api/status
 * and the probe in device.ts believes, exactly as it would on the porch. The
 * important inverse is tested too — with no stub the chip must never appear,
 * because every laptop user of the desk lives in that world.
 */

import { test, expect } from "./fixtures.js";

const STATUS = {
  version: "5.3",
  compiled: "test",
  uptime_s: 4210,
  sd_mounted: true,
  psram_free_kb: 1500,
  heap_free_kb: 70,
  volume: 40,
};

const FILES = [
  { name: "logs", size: 0, dir: true },
  { name: "wicked_winds.mp3", size: 287744, dir: false },
  { name: "ghostbusters.mp3", size: 985088, dir: false },
];

/** Wire up a pretend castle and remember what the desk asks of it. */
async function stubCastle(page: import("@playwright/test").Page): Promise<string[]> {
  const calls: string[] = [];
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    calls.push(`${route.request().method()} ${url.pathname}${url.search}`);
    if (url.pathname === "/api/status")
      return route.fulfill({ json: STATUS });
    if (url.pathname === "/api/files")
      return route.fulfill({ json: FILES });
    if (url.pathname === "/api/bootlog")
      return route.fulfill({ body: "boot log: 2 lines, 0 dropped\n[I][app] up\n" });
    return route.fulfill({ json: { queued: true } });
  });
  return calls;
}

test("without a castle answering, no device chrome appears", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#stage")).toBeVisible();
  // The probe times out at 1.5 s; give it room to have failed.
  await page.waitForTimeout(2000);
  await expect(page.locator("#deviceChip")).toBeHidden();
});

test("served by the castle, the chip appears and mirrors scenes", async ({ page }) => {
  const calls = await stubCastle(page);
  await page.goto("/");
  const chip = page.locator("#deviceChip");
  await expect(chip).toBeVisible();
  await expect(chip).toContainText("v5.3");
  await expect(chip).toContainText("SD ok");

  // Picking a scene fires it on the hardware too.
  await page.locator("button.scene", { hasText: "Storm" }).first().click();
  await expect.poll(() => calls.filter((c) => c.includes("/api/scene")).length)
    .toBeGreaterThan(0);
  expect(calls.some((c) => c.includes("s=storm"))).toBe(true);
});

test("the masthead names the castle and tracks mirroring", async ({ page }) => {
  // In simulator mode the line says "simulator"; served by the castle it has
  // to say so, because that is the difference between rehearsing and
  // performing on a porch someone is standing on.
  await stubCastle(page);
  await page.goto("/");
  await expect(page.locator("#deviceChip")).toBeVisible();
  await expect(page.locator("#headTxt")).toHaveText(/^castle v5\.3 · SD ok · mirroring · /);

  await page.locator("#devMirror").uncheck();
  await expect(page.locator("#headTxt")).toContainText("not mirroring");
  // Refreshing the line must not rebuild the chip underneath the hand that
  // is using it — a rebuild resets the volume slider to the last poll.
  await expect(page.locator("#devMirror")).not.toBeChecked();
});

test("mirroring off means scene picks stay local", async ({ page }) => {
  const calls = await stubCastle(page);
  await page.goto("/");
  await expect(page.locator("#deviceChip")).toBeVisible();
  await page.locator("#devMirror").uncheck();
  await page.locator("button.scene", { hasText: "Storm" }).first().click();
  await page.waitForTimeout(300);
  expect(calls.filter((c) => c.includes("/api/scene"))).toHaveLength(0);
});

test("the panel lists the card and plays a track on the castle", async ({ page }) => {
  const calls = await stubCastle(page);
  await page.goto("/");
  await page.locator("#devMore").click();
  const panel = page.locator("#devicePanel");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("wicked_winds.mp3");
  await expect(panel).toContainText("ghostbusters.mp3");
  await expect(panel).not.toContainText("logs");   // directories are not tracks

  await panel.locator("[data-play]").first().click();
  await expect.poll(() => calls.filter((c) => c.includes("/api/play")).length)
    .toBeGreaterThan(0);
  expect(calls.some((c) => c.includes("f=wicked_winds.mp3"))).toBe(true);
});

test("the volume slider starts where the amp actually is", async ({ page }) => {
  // Route castle: the chip's slider (the only one now — the panel's twin
  // slider is gone on purpose) has to open at the device's real level.
  await page.addInitScript(() => localStorage.setItem("castleSoundRoute", "castle"));
  await stubCastle(page);
  await page.goto("/");
  await expect(page.locator("#devVol")).toHaveValue("40");
  await expect(page.locator("#devVol")).toBeEnabled();
});

test("♪ Mac hushes the castle and locks its volume controls", async ({ page }) => {
  // Default route is Mac: on first contact the desk turns the castle's amp
  // to 0, and the controls for the speaker it just silenced go inert — a
  // live slider here could silently un-hush the porch (route-aware volume).
  const calls = await stubCastle(page);
  await page.goto("/");
  await expect(page.locator("#devVol")).toBeDisabled();
  await expect(page.locator("#devMute")).toBeDisabled();
  await expect.poll(() => calls.filter((c) => c.includes("/api/volume?v=0")).length)
    .toBeGreaterThan(0);
});

test("the ♪ switch lives next to Play and flips the route both ways", async ({ page }) => {
  // Dogfood 004/006: where sound comes out is decided when Play is pressed,
  // so the switch has to be in the transport, not only down in the corner.
  const calls = await stubCastle(page);
  await page.goto("/");
  const route = page.locator(".transport #sndRoute");
  await expect(route).toHaveText("♪ Mac");
  await route.click();
  await expect(route).toHaveText("♪ Castle");
  // Flipping to castle restores the amp to the remembered level (40)…
  await expect.poll(() => calls.filter((c) => c.includes("/api/volume?v=40")).length)
    .toBeGreaterThan(0);
  await expect(page.locator("#devVol")).toBeEnabled();
  // …and the chip's own ♪ button says the same thing (one switch, two homes).
  await expect(page.locator("#devSnd")).toHaveText("♪ Castle");
});

test("an action re-polls status instead of waiting out the slow cycle", async ({ page }) => {
  const calls = await stubCastle(page);
  await page.goto("/");
  await expect(page.locator("#deviceChip")).toBeVisible();
  const polls = (): number => calls.filter((c) => c.startsWith("GET /api/status")).length;
  const before = polls();
  await page.locator("#devStop").click();
  // act() schedules a refresh ~0.9 s after the POST lands — far inside the
  // 15 s poll, which is the whole point.
  await expect.poll(polls, { timeout: 5000 }).toBeGreaterThan(before);
});

test("a bare track play shows on the chip, not 'idle'", async ({ page }) => {
  // /api/play sets a track and no scene (the card rows, the panel's ▶). The
  // chip keyed on scene alone and called this "idle" — caught live against
  // the emulator.
  await page.route("**/api/status", (route) =>
    route.fulfill({ json: { ...STATUS, scene: "", track: "ghostbusters.mp3" } }));
  await page.route("**/api/files", (route) => route.fulfill({ json: FILES }));
  await page.goto("/");
  await expect(page.locator("#devNow")).toHaveText("▶ ghostbusters.mp3");
});

test("the light override parks a colour and hands the show back", async ({ page }) => {
  const calls = await stubCastle(page);
  await page.goto("/");
  await page.locator("#devMore").click();
  await page.locator("#dpColor").fill("#00ff80");
  await expect.poll(() => calls.filter((c) => c.includes("/api/light?c=00ff80")).length)
    .toBeGreaterThan(0);
  await page.locator("#dpShow").click();
  await expect.poll(() => calls.filter((c) => c.includes("c=show")).length)
    .toBeGreaterThan(0);
  await page.locator("#dpOff").click();
  await expect.poll(() => calls.filter((c) => c.includes("c=off")).length)
    .toBeGreaterThan(0);
});

test("the boot log is one tap away", async ({ page }) => {
  await stubCastle(page);
  await page.goto("/");
  await page.locator("#devMore").click();
  await page.locator("#dpLog").click();
  await expect(page.locator("#dpLogOut")).toContainText("boot log: 2 lines");
});

/* ── Pass-1 judge findings: the states that used to lie ─────────────────── */

/** A castle that can be switched off mid-test: status (and, when `deadPosts`
 *  is set, every POST) answers the studio's 502 once `up` is false. */
function flakyCastle(page: import("@playwright/test").Page,
                     status: Record<string, unknown> = STATUS):
    { calls: string[]; set: (up: boolean, deadPosts?: boolean) => void } {
  const calls: string[] = [];
  let up = true;
  let deadPosts = false;
  void page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    calls.push(`${method} ${url.pathname}${url.search}`);
    const down = () => route.fulfill({ status: 502, json: { error: "castle not reachable" } });
    if (url.pathname === "/api/status") return up ? route.fulfill({ json: status }) : down();
    if (url.pathname === "/api/files") return up ? route.fulfill({ json: FILES }) : down();
    if (method === "POST" && deadPosts) return down();
    return route.fulfill({ json: { queued: true } });
  });
  return { calls, set: (u, d = false) => { up = u; deadPosts = d; } };
}

test("a castle that boots after the page loads still gets its chip", async ({ page }) => {
  // J1-3: the probe used to run once; a castle absent at load meant
  // simulator for the whole session while the library grew castle buttons.
  const castle = flakyCastle(page);
  castle.set(false);
  await page.goto("/");
  await page.waitForTimeout(1500);
  await expect(page.locator("#deviceChip")).toBeHidden();
  await expect(page.locator("#headTxt")).toContainText("simulator");
  castle.set(true);
  // The desk re-probes every 5 s while no castle answers.
  await expect(page.locator("#deviceChip")).toBeVisible({ timeout: 9000 });
  await expect(page.locator(".transport #sndRoute")).toBeVisible();
  await expect(page.locator("#headTxt")).toContainText("castle v5.3");
});

test("the masthead keeps saying 'not answering' across a ♪ toggle", async ({ page }) => {
  // J1-4: toggling ♪ (or the mirror box) re-said the LAST GOOD status with
  // a green dot while the castle was dead and the volume POST had failed.
  const castle = flakyCastle(page);
  await page.goto("/");
  await expect(page.locator("#headTxt")).toContainText("castle v5.3");
  castle.set(false);
  // A successful action re-polls ~1 s later — that poll finds the castle gone.
  await page.locator("#devStop").click();
  await expect(page.locator("#headTxt")).toContainText("castle not answering");
  await expect(page.locator("#devStop")).toBeDisabled();
  castle.set(false, true);
  await page.locator(".transport #sndRoute").click();
  await expect(page.locator("#headTxt")).toContainText("castle not answering");
  await expect(page.locator("#headTxt")).toContainText("sound: castle");
  await expect(page.locator("#headTxt")).not.toContainText("v5.3");
});

test("a failed action says why, in the castle's words", async ({ page }) => {
  // J1-6: "scene seance failed" could not tell a typo from a dead castle.
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/status") return route.fulfill({ json: STATUS });
    if (url.pathname === "/api/files") return route.fulfill({ json: FILES });
    if (url.pathname === "/api/scene")
      return route.fulfill({ status: 404, contentType: "text/plain", body: "unknown scene" });
    return route.fulfill({ json: { queued: true } });
  });
  await page.goto("/");
  await expect(page.locator("#deviceChip")).toBeVisible();
  await page.locator("button.scene", { hasText: "Storm" }).first().click();
  await expect(page.getByText(/scene storm failed — unknown scene/)).toBeVisible();
});

test("Play on a card-only row moves the chip off 'idle'", async ({ page }) => {
  // J1-7: the library's Play on castle and the panel's ▶ bypassed the
  // chip's re-poll, so it said "idle" while the castle played.
  let track = "";
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/status")
      return route.fulfill({ json: { ...STATUS, scene: "", track } });
    if (url.pathname === "/api/files") return route.fulfill({ json: FILES });
    if (url.pathname === "/api/play") {
      track = url.searchParams.get("f") ?? "";
      return route.fulfill({ json: { queued: true } });
    }
    return route.fallback();
  });
  await page.goto("/");
  await expect(page.locator("#devNow")).toHaveText("idle");
  await page.locator(".trk--card[data-card='ghostbusters.mp3'] [data-cardact='play']").click();
  await expect(page.locator("#devNow")).toHaveText("▶ ghostbusters.mp3");
  // The panel's own ▶ goes the same way.
  track = "";
  await page.locator("#devMore").click();
  await page.locator("#devicePanel [data-play]").first().click();
  await expect(page.locator("#devNow")).toHaveText("▶ wicked_winds.mp3");
});
