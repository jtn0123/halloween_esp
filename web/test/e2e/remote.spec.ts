/**
 * The phone remote, for real: firmware/sd_web_remote.h's kRemotePage as the
 * emulator serves it (byte for byte — see castle_emu_http.py), relayed by a
 * real tools/studio.py at GET /remote, and tapped. Until the emulator lifted
 * the page out of the C, the remote had zero e2e coverage and the studio's
 * relay in the test rig was a 118-byte placeholder (judge B, JB2-6).
 *
 * Same spawn pattern as bridge.spec.ts: own ports, own processes, torn down
 * in afterAll, the CASTLE_TRACKS/CASTLE_SCENES sandbox inherited.
 */

import { spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test, expect } from "@playwright/test";

const ROOT = resolve(__dirname, "../../..");
const PY = join(ROOT, ".venv", "bin", "python");

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

let STUDIO = "";
let emu: ChildProcess | undefined;
let studio: ChildProcess | undefined;

async function waitFor(url: string, pred: (r: Response) => Promise<boolean>,
                       ms = 15000): Promise<void> {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try { if (await pred(await fetch(url))) return; } catch { /* not yet */ }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`gave up waiting for ${url}`);
}

const castleScene = async (): Promise<string> =>
  ((await (await fetch(`${STUDIO}/api/status`)).json()) as { scene: string }).scene;

test.beforeAll(async () => {
  const emuPort = await freePort();
  const studioPort = await freePort();
  STUDIO = `http://127.0.0.1:${studioPort}`;
  const card = mkdtempSync(join(tmpdir(), "castle-e2e-remote-card-"));
  emu = spawn(PY, [join(ROOT, "tools", "castle_emu.py"), String(emuPort), "--dir", card],
              { stdio: "ignore" });
  studio = spawn(PY, ["-u", join(ROOT, "tools", "studio.py"), String(studioPort), "--localhost"], {
    stdio: "ignore",
    env: { ...process.env, CASTLE_HOST: `127.0.0.1:${emuPort}` },
  });
  await waitFor(`${STUDIO}/api/status`,
                async (r) => r.ok && !("studio" in await r.json()));
});

test.afterAll(() => {
  emu?.kill();
  studio?.kill();
});

test("the real remote page, relayed by the studio, drives the castle", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${STUDIO}/remote`);
  await expect(page).toHaveTitle("Castle Remote");
  // Four giant buttons for cold thumbs: each at least a finger tall.
  for (const id of ["show", "ambient", "scare", "black"]) {
    const btn = page.locator(`#${id}`);
    await expect(btn).toBeVisible();
    expect((await btn.boundingBox())!.height).toBeGreaterThan(120);
  }
  // Its status line is the emulator's /api/status through the relay.
  await expect(page.locator("#st")).toContainText("v5.25");
  await expect(page.locator("#showTxt")).toHaveText("start the show");

  await page.locator("#ambient").click();
  await expect.poll(castleScene, { timeout: 5000 }).toBe("vigil");
  await expect(page.locator("#st")).toContainText("vigil");

  await page.locator("#show").click();
  await expect(page.locator("#showTxt")).toHaveText("stop the show");
  await expect(page.locator("#show")).toHaveClass(/on/);

  await page.locator("#black").click();
  await expect.poll(castleScene, { timeout: 5000 }).toBe("");
});
