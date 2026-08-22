/**
 * The lean page (grade report G4): served by the studio, the desk's scene
 * audio is NOT inlined — each player points at /studio/scene-audio/<id>,
 * which the studio answers from the rendered files, Range and all. The
 * portable build on disk keeps its data URIs; this is purely what a phone
 * on the LAN downloads: the page without 1.9 MB of base64 it may never play.
 *
 * Same stubbed non-loopback host as lan_host.spec.ts (`studio.lan` →
 * 127.0.0.1): the case the rewrite exists for. The loopback desk gets the
 * same page — the rule is "served by the studio", not "served to a phone".
 */

import { test, expect, playing } from "./fixtures.js";

const PORT = Number(process.env.CASTLE_E2E_PORT || 8799);
const LAN = `http://studio.lan:${PORT}`;

test.use({
  launchOptions: {
    args: [
      "--mute-audio",
      "--autoplay-policy=no-user-gesture-required",
      "--host-resolver-rules=MAP studio.lan 127.0.0.1",
    ],
  },
});

test("a LAN phone's scene audio is linked, not inlined — and plays", async ({ page }) => {
  const res = await page.goto(`${LAN}/`);
  expect(res?.ok()).toBe(true);
  // The page itself carries no base64 audio any more.
  expect(await res!.text()).not.toContain("data:audio/mpeg");
  // Every rendered-scene player points at the route — and nothing else.
  const srcs = await page.evaluate(() =>
    Object.values((window as unknown as { CASTLE_GEN: { audio: Record<string, string> } })
      .CASTLE_GEN.audio));
  expect(srcs.length).toBeGreaterThan(0);
  for (const s of srcs) expect(s).toMatch(/^\/studio\/scene-audio\/[A-Za-z0-9_]+$/);
  const players = await page.evaluate(() =>
    [...(window as unknown as { __media: HTMLMediaElement[] }).__media]
      .map((a) => a.src).filter((s) => s.includes("/studio/scene-audio/")));
  expect(players).toHaveLength(srcs.length);

  // The route serves real audio with Range support, from the LAN hostname
  // (fetched in the page: the browser is what knows studio.lan).
  const head = await page.evaluate(async (url) => {
    const r = await fetch(url, { headers: { Range: "bytes=0-3" } });
    return { status: r.status, type: r.headers.get("content-type"),
             ranges: r.headers.get("accept-ranges"), len: (await r.arrayBuffer()).byteLength };
  }, srcs[0]!);
  expect(head).toEqual({ status: 206, type: "audio/mpeg", ranges: "bytes", len: 4 });

  // And Play actually plays through it: the first scene's element runs and
  // has data (readyState > 0 only after the route answered).
  await page.locator("#play").click();
  await expect(page.locator("#playLabel")).toHaveText("Pause");
  await expect.poll(() => playing(page, "/studio/scene-audio/")).toBeGreaterThan(0);
  await expect.poll(() => page.evaluate(() =>
    [...(window as unknown as { __media: HTMLMediaElement[] }).__media]
      .some((a) => !a.paused && a.src.includes("/studio/scene-audio/") && a.readyState > 0)),
  ).toBe(true);
});
