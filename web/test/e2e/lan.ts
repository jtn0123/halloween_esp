/**
 * The desk under a non-loopback hostname — `studio.lan`, standing in for the
 * address a phone on the LAN uses — served by the same studio the rest of
 * the suite runs against. lan_host.spec.ts and lean_page.spec.ts need it.
 *
 * Chromium maps the name itself, below the page (--host-resolver-rules), in
 * a browser of its own: `launchOptions` replaces the config's, so the mute
 * flags are repeated rather than lost. WebKit has no such switch, so there
 * the page's requests for studio.lan are answered by the studio through
 * page.route — the page still sees studio.lan in its address bar and in
 * every URL it builds, which is what the specs are about.
 */

import type {
  Page, PlaywrightWorkerArgs, PlaywrightWorkerOptions, WorkerFixture,
} from "@playwright/test";

export const PORT = Number(process.env.CASTLE_E2E_PORT || 8799);
export const LAN = `http://studio.lan:${PORT}`;

type Launch = PlaywrightWorkerOptions["launchOptions"];
type Worker = PlaywrightWorkerArgs & PlaywrightWorkerOptions;

/** For `test.use({ launchOptions })`: Chromium's mapping, nothing elsewhere. */
export const lanLaunch: [WorkerFixture<Launch, Worker>, { scope: "worker" }] = [
  async ({ browserName }, use) => {
    await use(browserName === "chromium" ? {
      args: [
        "--mute-audio",
        "--autoplay-policy=no-user-gesture-required",
        "--host-resolver-rules=MAP studio.lan 127.0.0.1",
      ],
    } : {});
  },
  { scope: "worker" },
];

/** Call before the first goto. A no-op where the browser maps the name. */
export async function reachAsLan(page: Page, browserName: string): Promise<void> {
  if (browserName === "chromium") return;
  await page.route(`${LAN}/**`, async (route) => {
    const url = route.request().url().replace("//studio.lan:", "//127.0.0.1:");
    const headers = { ...route.request().headers(), host: `studio.lan:${PORT}` };
    await route.fulfill({ response: await route.fetch({ url, headers }) });
  });
}
