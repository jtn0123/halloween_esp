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
 * the app's own muting rather than the flag that is hiding it.
 */

import { test as base, expect, type Page } from "@playwright/test";

export const test = base.extend({
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
