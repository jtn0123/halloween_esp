/**
 * The desk reached from a phone on the LAN — any hostname that is not the
 * loopback. Judge B, JB2-1: hiding Restart/Stop-server from phones was done
 * by REMOVING the element, initTracks then threw on its id, and every LAN
 * device read "read-only · studio not running" with an empty library. The
 * whole reason `--lan` exists, broken by the commit that polished it.
 *
 * The browser is told that `studio.lan` is 127.0.0.1 — a real non-loopback
 * hostname in the address bar, against the same studio the rest of the
 * suite uses (lan.ts: how, per browser).
 */

import { test, expect } from "./fixtures.js";
import { LAN, PORT, lanLaunch, reachAsLan } from "./lan.js";

test.use({ launchOptions: lanLaunch });

test("a LAN phone gets the library, and no server buttons", async ({ page, browserName }) => {
  await reachAsLan(page, browserName);
  await page.goto(`${LAN}/`);
  expect(await page.evaluate(() => location.hostname)).toBe("studio.lan");
  await expect(page.locator("#trkMode")).toHaveText(/studio · connected/);
  await expect(page.locator(".trk").first()).toBeVisible();
  await expect(page.locator("#trkOffline")).toBeHidden();
  await expect(page.locator("#trkUrl")).toBeEnabled();
  // The studio strip names the host it is reached by, not a constant.
  await expect(page.locator(".trk-srvtxt b")).toHaveText(`studio.lan:${PORT}`);
  // Restart / Stop-server are for the laptop running the studio only.
  await expect(page.locator("#trkServer")).toBeHidden();
  await expect(page.locator("#srvStop")).toBeHidden();
  await expect(page.locator("#srvRestart")).toBeHidden();
});
