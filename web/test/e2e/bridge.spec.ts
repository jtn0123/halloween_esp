/**
 * The real relay, end to end: a real tools/studio.py bridged to a real
 * tools/castle_emu.py, driven from the desk UI. Every other castle spec
 * plays the castle with page.route, which is hermetic but can only ever
 * test the shapes the spec author imagined — the J2-1 "vundefined" panel
 * slipped through exactly that gap, because the studio's real answer for a
 * dead castle (200 {"studio":true}) was never what a stub returned.
 *
 * Its own ports, its own processes, torn down in afterAll; the sandbox env
 * (CASTLE_TRACKS/CASTLE_SCENES) is inherited from playwright.config.ts so
 * nothing here can touch the real library.
 */

import { spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test, expect } from "@playwright/test";

const ROOT = resolve(__dirname, "../../..");
const PY = join(ROOT, ".venv", "bin", "python");

/** Ports: CASTLE_E2E_BRIDGE_PORTS="<emu>,<studio>" pins them (a CI lane
 *  that wants determinism); otherwise each is whatever the OS has free right
 *  now, so two lanes — or a stale run — cannot collide. */
async function freePort(): Promise<number> {
  return new Promise((ok, fail) => {
    const srv = createServer();
    srv.once("error", fail);
    srv.listen(0, "127.0.0.1", () => {
      const addr = srv.address();
      const port = typeof addr === "object" && addr ? addr.port : 0;
      srv.close(() => ok(port));
    });
  });
}
async function pickPorts(): Promise<[number, number]> {
  const pinned = (process.env.CASTLE_E2E_BRIDGE_PORTS || "").split(",").map(Number);
  if (pinned.length === 2 && pinned.every((n) => n > 0)) return [pinned[0]!, pinned[1]!];
  return [await freePort(), await freePort()];
}

let EMU_PORT = 0;
let STUDIO = "";
let emu: ChildProcess | undefined;
let studio: ChildProcess | undefined;
/** Everything the studio printed — asserted quiet at the end. */
let studioLog = "";

async function waitFor(url: string, pred: (r: Response) => Promise<boolean>,
                       ms = 15000): Promise<void> {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try { if (await pred(await fetch(url))) return; } catch { /* not yet */ }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`gave up waiting for ${url}`);
}

function startEmu(): ChildProcess {
  const card = mkdtempSync(join(tmpdir(), "castle-e2e-card-"));
  return spawn(PY, [join(ROOT, "tools", "castle_emu.py"), String(EMU_PORT), "--dir", card],
               { stdio: "ignore" });
}

test.beforeAll(async () => {
  const [emuPort, studioPort] = await pickPorts();
  EMU_PORT = emuPort;
  STUDIO = `http://127.0.0.1:${studioPort}`;
  emu = startEmu();
  studio = spawn(PY, ["-u", join(ROOT, "tools", "studio.py"), String(studioPort), "--localhost"], {
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, CASTLE_HOST: `127.0.0.1:${EMU_PORT}` },
  });
  studio.stdout?.on("data", (d: Buffer) => { studioLog += d.toString(); });
  studio.stderr?.on("data", (d: Buffer) => { studioLog += d.toString(); });
  await waitFor(`${STUDIO}/api/status`,
                async (r) => r.ok && !("studio" in await r.json()));
});

test.afterAll(() => {
  emu?.kill();
  studio?.kill();
});

test("castle dies behind the real studio: masthead, chip and panel all say so", async ({ page }) => {
  await page.goto(`${STUDIO}/`);
  await expect(page.locator("#deviceChip")).toBeVisible();
  await expect(page.locator("#headTxt")).toContainText("castle v5.23");
  // A real scene pick reaches the emulator through the relay.
  await page.locator("button.scene", { hasText: "Storm" }).first().click();
  await expect.poll(async () =>
    ((await (await fetch(`${STUDIO}/api/status`)).json()) as { scene: string }).scene,
    { timeout: 5000 }).toBe("storm");

  emu?.kill();
  emu = undefined;
  // The studio now answers 200 {"studio":true} — the real down shape.
  await waitFor(`${STUDIO}/api/status`, async (r) => "studio" in await r.json());
  // A click while down → reason in the toast, and the re-poll flips the desk.
  await page.locator("#devStop").click();
  await expect(page.locator("#headTxt")).toContainText("castle not answering");
  await expect(page.locator("#devStop")).toBeDisabled();
  await expect(page.locator("#trkSync")).toBeHidden();
  await page.locator("#devMore").click();
  await expect(page.locator("#devicePanel")).toContainText("castle stopped answering");
  await expect(page.locator("#devicePanel")).not.toContainText("vundefined");

  // And it comes back: the desk recovers on its own.
  emu = startEmu();
  await waitFor(`${STUDIO}/api/status`, async (r) => !("studio" in await r.json()));
  await expect(page.locator("#headTxt")).toContainText("castle v5.23", { timeout: 20000 });
  await expect(page.locator("#devStop")).toBeEnabled();

  // The whole episode — castle up, down, up — must leave a QUIET studio:
  // no native-leg "Error resolving host:port" every 5 s, no tracebacks.
  // (J2-8 / J3-5: the log-noise class stays closed.)
  expect(studioLog).not.toMatch(/Error resolving|Traceback/);
  expect(studioLog).toContain("GET /api/status");
});
