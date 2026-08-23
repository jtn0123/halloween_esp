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
  // Two of the five speaker-test tones: the others must render disabled.
  { name: "test_sweep.mp3", size: 190000, dir: false },
  { name: "test_1k.mp3", size: 128000, dir: false },
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
  // The probe is one /api/status; the studio answers it castle-less. Once
  // that answer is in, the chip's fate is decided.
  const probed = page.waitForResponse((r) => r.url().includes("/api/status"));
  await page.goto("/");
  await expect(page.locator("#stage")).toBeVisible();
  await probed;
  await expect(page.locator("#deviceChip")).toBeHidden();
  await expect(page.locator("#headTxt")).toContainText("simulator");
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
  // The pick lands locally (the stage names it) and posts nothing.
  await expect(page.locator("#stageNote")).toContainText("Storm");
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
  // The strip test drives one data line: "<zone>:<colour>".
  await page.locator("[data-zl='door:00ff00']").click();
  await expect.poll(() => calls.filter((c) => c.includes("/api/light?c=door:00ff00@100")).length)
    .toBe(1);
  // 4 rows (towerL, door, towerR, all) × R G B W off, + 3 patterns.
  await expect(page.locator("[data-zl]")).toHaveCount(23);
  // The all-strips row carries no zone: every strip runs it.
  await expect(page.locator("[data-zl=':ff0000']")).toBeVisible();
  // Brightness applies to the strip test and the picker; "off" carries none.
  await page.locator("[data-pct='25']").click();
  await page.locator("[data-zl='towerL:white']").click();
  await expect.poll(() => calls.filter((c) => c.includes("c=towerL:white@25")).length).toBe(1);
  await page.locator("#dpColor").fill("#123456");
  await expect.poll(() => calls.filter((c) => c.includes("c=123456@25")).length).toBeGreaterThan(0);
  await page.locator("[data-zl='towerR:off']").click();
  await expect.poll(() => calls.filter((c) => c.includes("c=towerR:off")).length).toBe(1);
  expect(calls.some((c) => c.includes("towerR:off@"))).toBe(false);
  // A pattern carries no zone: every strip runs it, at the chosen brightness.
  await page.locator("[data-zl=':chase']").click();
  await expect.poll(() => calls.filter((c) => c.includes("c=chase@25")).length).toBe(1);
  expect(calls.some((c) => c.includes("c=:chase"))).toBe(false);
});

test("the speaker test plays a tone at a chosen level, spaced for the mailbox", async ({ page }) => {
  const calls = await stubCastle(page);
  await page.goto("/");
  await page.locator("#devMore").click();
  // Only the tones on the card are live; the rest say how to push them.
  await expect(page.locator("[data-tone='test_sweep']")).toBeEnabled();
  await expect(page.locator("[data-tone='test_200']")).toBeDisabled();
  await expect(page.locator(".dp__note--tight").first()).toContainText("tones not on the card");
  await page.locator("[data-tpct='80']").click();
  await page.locator("[data-tone='test_sweep']").click();
  await expect.poll(() => calls.filter((c) => c.includes("/api/play?f=test_sweep.mp3")).length).toBe(1);
  const vol = calls.findIndex((c) => c.includes("/api/volume?v=80"));
  const play = calls.findIndex((c) => c.includes("/api/play?f=test_sweep.mp3"));
  expect(vol).toBeGreaterThanOrEqual(0);
  expect(vol).toBeLessThan(play);                 // level lands before the tone
  await page.locator("#dpToneStop").click();
  await expect.poll(() => calls.filter((c) => c.includes("/api/stop")).length).toBe(1);
});

test("the panel lists what is actually on the card, and what each track is for", async ({ page }) => {
  await stubCastle(page);
  await page.goto("/");
  await page.locator("#devMore").click();
  // Every root track is listed by name — the panel used to show a count and
  // point at the Library, which is not "what is on the device".
  await expect(page.locator(".dp__file-nm")).toHaveCount(4);
  await expect(page.locator(".dp__file-nm").first()).toHaveText("wicked_winds.mp3");
  // ...and badged by what it is FOR, which its name does not say.
  await expect(page.locator(".dp__badge--tone")).toHaveCount(2);
  // The show's own tracks live in scenes/, which /api/files never lists;
  // the manifest's verdict from /api/status stands in for them.
  await expect(page.locator(".dp__sec").filter({ has: page.locator("#dpFiles") }))
    .toContainText("all 9 show tracks present");
});

test("a light sequence walks the channels and can be superseded", async ({ page }) => {
  const calls = await stubCastle(page);
  await page.goto("/");
  await page.locator("#devMore").click();
  await page.locator("[data-seq='cycle']").click();
  await expect.poll(() => calls.filter((c) => c.includes("c=ff0000@")).length).toBe(1);
  // A plain strip click supersedes the running walk rather than interleaving.
  await page.locator("[data-zl='door:0000ff']").click();
  const after = calls.length;
  await page.waitForTimeout(2000);
  expect(calls.filter((c) => c.includes("c=00ff00@")).length).toBe(0);
  expect(calls.length).toBeLessThanOrEqual(after + 1);
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
    // The real studio answers a castle-less probe 200 {"studio":true}, not
    // 502 — the shape the panel once rendered as "vundefined" (J2-1/J2-6).
    if (url.pathname === "/api/status")
      return route.fulfill({ json: up ? status : { studio: true } });
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
  const probed = page.waitForResponse((r) => r.url().includes("/api/status"));
  await page.goto("/");
  await probed;                                   // the first probe has answered "no"
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
  // J2-2: ♪ to Castle while down must not light the volume controls up.
  await page.locator(".transport #sndRoute").click();
  await page.locator(".transport #sndRoute").click();
  await expect(page.locator("#sndRoute").first()).toHaveText("♪ Castle");
  await expect(page.locator("#devVol")).toBeDisabled();
  await expect(page.locator("#devMute")).toBeDisabled();
  // The dimmed chip says when it last heard from the castle, not a live ▶.
  await expect(page.locator("#devNow")).toContainText("last seen");
  // J2-1: the panel must not invent a control panel from {"studio":true}.
  await page.locator("#devMore").click();
  await expect(page.locator("#devicePanel")).toContainText("castle stopped answering");
  await expect(page.locator("#devicePanel")).not.toContainText("vundefined");
});

test("error toasts stack instead of overprinting, and never repeat", async ({ page }) => {
  // J2-3: two failures a second apart used to land on the same pixels.
  const castle = flakyCastle(page);
  await page.goto("/");
  await expect(page.locator("#deviceChip")).toBeVisible();
  castle.set(true, true);
  await page.locator("button.scene", { hasText: "Storm" }).first().click();
  await page.locator("button.scene", { hasText: "Vigil" }).first().click();
  await page.locator("button.scene", { hasText: "Vigil" }).first().click();
  const toasts = page.locator("#toasts > div");
  await expect(toasts).toHaveCount(2);          // identical text folded
  const boxes = await toasts.evaluateAll((els) =>
    els.map((e) => e.getBoundingClientRect().top));
  expect(boxes[0]).not.toEqual(boxes[1]);
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
