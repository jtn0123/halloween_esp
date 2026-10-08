/**
 * The owner's guide's pictures — the browser half of tools/guide_shots.py.
 *
 * `make guide-shots` starts the stand-in servers (emulated castles, Castle
 * Radio on a pretend LAN, the captive portal page and the flasher), writes
 * their addresses as a JSON plan on this script's stdin and runs it, bundled
 * by esbuild. Nothing it is handed is a path: the raw PNGs go to
 * web/dist/guide-shots/, beside the bundle, where guide_shots.py RAW reads them. Each function below opens one screen as
 * an owner would, waits for the words that prove it finished drawing, and
 * saves raw PNGs named after tools/guide_shots.py's SHOTS; the Python side
 * compresses them into docs/guide/. A screen that never says its words is a
 * failure here, not a picture of a half-drawn page.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, type Browser, type Locator, type Page } from "@playwright/test";

type Plan = {
  owner: { songs: string; empty: string; trouble: string };
  radio: string;
  portal: string;
  flasher: string;
};

const plan = JSON.parse(readFileSync(0, "utf8")) as Plan; // stdin
const OUT = fileURLToPath(new URL("guide-shots/", import.meta.url));
const file = (name: string): string => join(OUT, `${name}.png`);
const WAIT = 15_000;

/** Wait until the element's text holds `words` (with `gone`, until it no longer does). */
async function says(page: Page, selector: string, words: string, gone = false): Promise<void> {
  await page.waitForFunction(
    ([sel, w, negate]) => (document.querySelector(sel)?.textContent ?? "").includes(w) !== negate,
    [selector, words, gone] as const,
    { timeout: WAIT },
  );
}

/** Where `target` starts, in page coordinates; null is the page's top or end. */
async function edge(page: Page, target: Locator | null, end: boolean): Promise<number> {
  if (target) return target.first().evaluate((el) => el.getBoundingClientRect().top + window.scrollY);
  return end ? page.evaluate(() => document.documentElement.scrollHeight) : 0;
}

/** The band of the page from `from` down to `to`, the full width. */
async function band(page: Page, name: string, from: Locator | null, to: Locator | null): Promise<void> {
  const y = Math.max(0, (await edge(page, from, false)) - 10);
  const bottom = (await edge(page, to, true)) - 6;
  const width = page.viewportSize()?.width ?? 400;
  await page.screenshot({ path: file(name), fullPage: true, clip: { x: 0, y, width, height: bottom - y } });
}

async function phone(browser: Browser, acceptDownloads = false): Promise<Page> {
  const context = await browser.newContext({
    viewport: { width: 400, height: 800 }, deviceScaleFactor: 2, acceptDownloads,
  });
  return context.newPage();
}

async function portal(browser: Browser): Promise<void> {
  const page = await phone(browser);
  await page.goto(plan.portal);
  await page.locator("#net .network").first().waitFor({ timeout: WAIT });
  await page.screenshot({ path: file("portal"), fullPage: true });
}

async function flasher(browser: Browser): Promise<void> {
  const context = await browser.newContext({ viewport: { width: 860, height: 900 }, deviceScaleFactor: 1.5 });
  const page = await context.newPage();
  await page.goto(plan.flasher);
  await says(page, "#ver", "Installs release");
  // esp-web-tools comes from unpkg; until it is defined the button's three
  // slots (connect, unsupported, not-allowed) all show at once.
  await page.waitForFunction(() => !!customElements.get("esp-web-install-button"), undefined, { timeout: WAIT });
  await band(page, "flasher", null, page.locator("h2", { hasText: "can't find the castle" }));
}

async function owner(browser: Browser): Promise<void> {
  const page = await phone(browser, true);
  const h2 = (text: string): Locator => page.locator("h2", { hasText: text });

  await page.goto(`${plan.owner.songs}/owner`);
  await page.locator("#files li").first().waitFor({ timeout: WAIT });
  await says(page, "#facts", "Last restart");
  await band(page, "owner-show", null, h2("Settings"));
  await band(page, "owner-settings", h2("Settings"), page.locator("#msg"));
  const saved = page.waitForEvent("download");
  await page.locator("#rep").click();
  await saved;
  await says(page, "#msg", "Report saved.");
  await band(page, "owner-report", page.locator("#msg"), null);

  await page.goto(`${plan.owner.empty}/owner`);
  await says(page, "#files", "No songs on the card yet");
  await band(page, "owner-empty", h2("Songs on the card"), h2("Settings"));

  await page.goto(`${plan.owner.trouble}/owner`);
  await says(page, "#alerts", "No SD card");
  await says(page, "#facts", "Last restart");
  await band(page, "owner-trouble", null, h2("Show"));
}

/**
 * One card of Castle Radio, scrolled to the top of a tall window: the player
 * bar is fixed to the window's foot and would otherwise sit across a card
 * near the end of a page. A passing notice (#toast, gone by itself after
 * three seconds) and the card's own fades (a button that has just been
 * enabled) are waited out rather than photographed half-way.
 */
async function card(page: Page, selector: string, name: string): Promise<void> {
  const target = page.locator(selector).first();
  await target.evaluate((el) => el.scrollIntoView({ block: "start" }));
  await page.waitForFunction(() => !document.getElementById("toast")?.classList.contains("visible"),
    undefined, { timeout: WAIT });
  await target.evaluate((el) => Promise.all(el.getAnimations({ subtree: true })
    .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
    .map((a) => a.finished.catch(() => undefined))));  // a cancelled one rejects
  await target.screenshot({ path: file(name) });
}

async function radio(browser: Browser): Promise<void> {
  const context = await browser.newContext({ viewport: { width: 1280, height: 1400 }, deviceScaleFactor: 1.5 });
  const page = await context.newPage();
  const go = (where: string): Promise<void> => page.locator(`nav button[data-page="${where}"]`).click();

  await page.goto(plan.radio);
  await page.locator("#first-run").waitFor({ state: "visible", timeout: WAIT });
  await card(page, "#tracks >> xpath=..", "radio-first-run");

  await go("import");
  await card(page, ".import-grid > article", "radio-import");

  await go("device");
  await page.locator("#find-run").click();
  const found = page.locator("#find-results li").first();
  await found.waitFor({ timeout: WAIT });
  await card(page, "#castle-find", "radio-find");
  await found.getByRole("button").click();
  await says(page, "#find-message", "This is your castle now");
  // Adopting a castle has the firmware card ask it (castle-find.js adopt()).
  await says(page, "#fw-update", "Update castle to");
  await card(page, "#castle-update", "radio-update");
  await says(page, "#help-owner-note", "straight from the castle");
  await card(page, "#castle-help", "radio-help");

  await go("settings");
  await says(page, "#key-state", "Checking", true);
  await card(page, "#castle-key", "radio-key");
}

const browser = await chromium.launch();
try {
  await portal(browser);
  await flasher(browser);
  await owner(browser);
  await radio(browser);
} finally {
  await browser.close();
}
