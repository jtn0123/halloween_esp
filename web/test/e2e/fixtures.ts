/**
 * A page that can actually be asked what is making noise.
 *
 * The obvious check — `document.querySelectorAll("audio")` — finds nothing in
 * this app, because every player is a detached `new Audio()`: the row preview,
 * the clip audition and the rendered-scene players are all created in script
 * and never appended. So a test written that way passes whatever the page
 * does, which is worse than no test at all. It reports "silent" for a page
 * that is blasting.
 *
 * This registers every media element as it is constructed, so `sounding()`
 * asks a question with a real answer.
 *
 * Note that Chromium's --mute-audio (see playwright.config.ts) silences the
 * *output* but does not touch `element.muted`. That separation is deliberate:
 * the browser guarantees the run is silent, while these assertions still test
 * the app's own muting rather than the flag that is hiding it. WebKit has no
 * such flag; webkit.ts does the same job from just under the page, on the
 * context, so a popup or second page is as quiet as the first. That is why
 * every spec imports `test` from here (web/tools/check-suites.mjs holds it).
 */

import { test as base, expect, type Page, type Request } from "@playwright/test";
import { silenceOutput, tellBlobSizes } from "./webkit.js";

export const test = base.extend({
  context: async ({ context, browserName }, use) => {
    if (browserName !== "chromium") {
      await context.addInitScript(silenceOutput);
      await context.addInitScript(tellBlobSizes);
    }
    await use(context);
  },
  page: async ({ page }, use) => {
    await page.addInitScript(() => {
      const seen: HTMLMediaElement[] = [];
      (window as unknown as { __media: HTMLMediaElement[] }).__media = seen;

      const NativeAudio = window.Audio;
      const Patched = function (this: unknown, src?: string): HTMLAudioElement {
        const el = new NativeAudio(src);
        seen.push(el);
        return el;
      } as unknown as typeof Audio;
      Patched.prototype = NativeAudio.prototype;
      window.Audio = Patched;

      // Anything built the long way round gets caught here.
      const create = document.createElement.bind(document);
      document.createElement = function <K extends keyof HTMLElementTagNameMap>(
        tag: K, opts?: ElementCreationOptions,
      ) {
        const el = create(tag, opts);
        if (/^(audio|video)$/i.test(String(tag))) seen.push(el as HTMLMediaElement);
        return el;
      } as typeof document.createElement;
    });
    await use(page);
  },
});

export { expect };

/** How many bytes a request carried: its body, or — where the browser hands
 *  a route no Blob body (WebKit; webkit.ts) — the size sent beside it. */
export const bodyBytes = (request: Request): number =>
  request.postDataBuffer()?.length ?? Number(request.headers()["x-e2e-blob-bytes"] ?? 0);

/** Every media element the page has ever made, plus any in the markup. */
const ALL = `[...new Set([
  ...((window.__media) || []),
  ...document.querySelectorAll("audio,video"),
])]`;

/** How many players are unmuted and running — i.e. would be audible. */
export const sounding = (page: Page): Promise<number> =>
  page.evaluate(`${ALL}.filter(a => !a.paused && !a.muted).length`) as Promise<number>;

/** How many players are running at all, muted or not. */
export const playing = (page: Page, urlPart = ""): Promise<number> =>
  page.evaluate(
    `${ALL}.filter(a => !a.paused && a.src.includes(${JSON.stringify(urlPart)})).length`,
  ) as Promise<number>;

/* ── A pretend castle, shared by the castle-facing specs ─────────────── */

export interface SdFile { name: string; size: number; dir: boolean }

/** What /api/status answers. Specs override fields per test. */
export const CASTLE_STATUS = {
  version: "5.23", compiled: "test", uptime_s: 4210, sd_mounted: true,
  psram_free_kb: 1500, heap_free_kb: 70, sd_free_kb: 10_000_000, volume: 40,
  scene: "", track: "",
  pir: { armed: false, cooldown_s: 60, scene: "approach" },
  show_on: false,
};

export interface FakeCastle {
  /** Every request the desk made: "METHOD /path?query". */
  calls: string[];
  /** Mutable status — the next poll answers with it. */
  status: Record<string, unknown>;
  /** Mutable card listing; PUT/DELETE edit it unless `putBytes` says otherwise. */
  files: SdFile[];
  /** Answer PUTs with this many bytes instead of the true count (-1 = 500,
   *  -2 = 504 "took it but never acked"). Reset to null for honesty. */
  putBytes: number | null | ((name: string, real: number) => number | null);
  /** Extra latency on every castle answer, ms. */
  delay: number;
  /** Castle gone: status becomes the studio's {studio:true}, the rest 502. */
  up: boolean;
  /** Go down (`up = false`) the moment a request for exactly this path
   *  arrives, before answering it: the castle dies under the hand. Setting
   *  `up` before a click instead races the desk's own re-poll — first
   *  contact hushes the amp and re-polls ~1 s later — and in Linux WebKit
   *  in CI that poll can win, find the castle gone and disable the very
   *  control (■) the test is about to press. */
  dieOn: string | null;
  /** A keyed castle (firmware v5.74) the studio holds no right key for:
   *  every change — the POST /api/key probe included — is 401. Whether the
   *  status SAYS `locked` is the spec's to set, so a castle locked after its
   *  last status can be played too. */
  keyed: boolean;
  /** How many calls mention `part`. */
  hits(part: string): number;
}

/**
 * Route every castle-shaped /api/* call to an in-memory castle; the studio's
 * own endpoints (/studio/tracks, /studio/track/<id>, imports, scenes) fall through
 * to the real server, so a Sync moves real bytes the real server serves.
 */
export async function fakeCastle(page: Page, files: SdFile[] = [],
                                 status: Record<string, unknown> = {}):
    Promise<FakeCastle> {
  const c: FakeCastle = {
    calls: [], status: { ...CASTLE_STATUS, ...status }, files,
    putBytes: null, delay: 0, up: true, dieOn: null, keyed: false,
    hits: (part) => c.calls.filter((x) => x.includes(part)).length,
  };
  const CASTLE = /^\/api\/(status|files|play|stop|volume|scene|light|pir|show|bootlog|card|key)\b/;
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    const method = route.request().method();
    if (!CASTLE.test(p)) return route.fallback();
    c.calls.push(`${method} ${p}${url.search}`);
    if (c.delay) await new Promise((r) => setTimeout(r, c.delay));
    if (p === c.dieOn) { c.up = false; c.dieOn = null; }
    if (p === "/api/status") {
      return route.fulfill({ json: c.up ? c.status : { studio: true } });
    }
    if (!c.up) return route.fulfill({ status: 502, json: { error: "castle not reachable" } });
    if (c.keyed && method !== "GET") return route.fulfill({ status: 401, body: "castle key required\n" });
    if (p === "/api/key") return route.fulfill({ status: 400, body: "need new=<key> or clear=1\n" });
    if (p === "/api/files" && method === "GET") return route.fulfill({ json: c.files });
    if (p.startsWith("/api/files/") && method === "PUT") {
      const name = decodeURIComponent(p.slice("/api/files/".length));
      const real = bodyBytes(route.request());
      // Either arm may answer null, and it means the same thing in both:
      // no opinion, report the length that actually arrived.
      const said = (typeof c.putBytes === "function"
        ? c.putBytes(name, real) : c.putBytes) ?? real;
      if (said === -1) return route.fulfill({ status: 500, body: "write failed" });
      if (said === -2) return route.fulfill({ status: 504, json: { error: "castle did not answer in time" } });
      const i = c.files.findIndex((f) => f.name === name);
      if (i >= 0) c.files.splice(i, 1);
      c.files.push({ name, size: said, dir: false });
      return route.fulfill({ json: { path: `/sd/${name}`, bytes: said } });
    }
    if (p.startsWith("/api/files/") && method === "DELETE") {
      const name = decodeURIComponent(p.slice("/api/files/".length));
      const i = c.files.findIndex((f) => f.name === name);
      if (i >= 0) c.files.splice(i, 1);
      return route.fulfill({ json: { deleted: true } });
    }
    if (p === "/api/bootlog") return route.fulfill({ body: "boot log: 1 line\n[I][app] up\n" });
    if (p === "/api/scene") c.status["scene"] = url.searchParams.get("s") ?? "";
    if (p === "/api/stop") { c.status["scene"] = ""; c.status["track"] = ""; }
    if (p === "/api/play") c.status["track"] = url.searchParams.get("f") ?? "";
    if (p === "/api/volume") c.status["volume"] = Number(url.searchParams.get("v"));
    if (p === "/api/show/start") c.status["show_on"] = true;
    if (p === "/api/show/stop") c.status["show_on"] = false;
    if (p === "/api/pir") {
      const pir = { ...(c.status["pir"] as Record<string, unknown>) };
      if (url.searchParams.has("armed")) pir["armed"] = url.searchParams.get("armed") === "1";
      if (url.searchParams.has("scene")) pir["scene"] = url.searchParams.get("scene");
      if (url.searchParams.has("cooldown")) pir["cooldown_s"] = Number(url.searchParams.get("cooldown"));
      c.status["pir"] = pir;
    }
    return route.fulfill({ json: { queued: true } });
  });
  return c;
}

/** A castle answer the TEST lets go of. */
export interface Held {
  /** Resolves when the first matching request has reached the castle. */
  arrived: Promise<void>;
  /** Let every held request (and any later one) through. */
  release(): void;
}

/**
 * Hold the castle's answer to every request matching `url` until the test
 * calls `release()`. Released, a request falls through to the route
 * registered before this one — fakeCastle, or a spec's own stub — which
 * records and answers it as usual.
 *
 * This is how a spec says "the desk did not wait for the castle": the
 * castle has not answered yet, and the desk has already moved. A stopwatch
 * round a click cannot say that in a browser that paints at 5 fps — Linux
 * WebKit in CI spends over a second on one Playwright click (actionability
 * waits for animation frames) — so the castle's answer is held instead,
 * and the order is asserted rather than the time.
 *
 * For actions, which wait 30 s for an answer (api.ts). Not for the status
 * probe: it gives up after 2.5 s (device_probe.ts), so a hold that outlasts
 * that turns a slow castle into a missing one.
 */
export async function holdCastle(page: Page,
                                 url: Parameters<Page["route"]>[0]): Promise<Held> {
  let release!: () => void;
  const gate = new Promise<void>((r) => { release = r; });
  let arrive!: () => void;
  const arrived = new Promise<void>((r) => { arrive = r; });
  await page.route(url, async (route) => {
    arrive();
    await gate;
    await route.fallback();
  });
  return { arrived, release };
}

/** The track's exact on-disk size from the real studio, so a card copy can
 *  be made current (same bytes) or stale (any other number) on purpose. */
export async function realBytes(page: Page, id: string): Promise<number> {
  const r = await (await page.request.get("/studio/tracks")).json() as
    { tracks: { id: string; bytes: number }[] };
  return r.tracks.find((t) => t.id === id)!.bytes;
}
