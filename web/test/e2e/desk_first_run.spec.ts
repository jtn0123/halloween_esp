/**
 * The desk on an owner's first evening (docs/PRODUCTION-TODO.md §3 and §7):
 * a library with nothing in it says how to add the first song instead of
 * "empty" beside a blank; a castle with no motion sensor (v5.75 `pir.fitted`
 * false — the buyer build, which refuses /api/pir) is drawn without the
 * controls it would refuse; and the castle panel links the castle's own page
 * (/owner) at the address the studio reached it on, and nowhere else.
 */

import { test, expect, fakeCastle } from "./fixtures.js";

test("an empty library leads with how to add the first song", async ({ page }) => {
  await page.route("**/studio/tracks", (route) =>
    route.fulfill({ json: { tracks: [], scenes: [] } }));
  await page.goto("/");
  await expect(page.locator("#trkMode")).toHaveText(/studio/);
  const first = page.locator("#trkFirst");
  await expect(first).toBeVisible();
  await expect(first).toContainText("Add your first song");
  await expect(first).toContainText("press Import");
  await expect(page.locator("#trkCount")).toHaveText("empty");
});

test("a library with songs in it has no first-run row", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#trkCount")).toHaveText(/\d+ imported/);
  await expect(page.locator("#trkFirst")).toHaveCount(0);
});

test("a castle with no motion sensor shows none of its controls, and says so", async ({ page }) => {
  const castle = await fakeCastle(page, [], {
    pir: { fitted: false, armed: false, cooldown_s: 60, scene: "" },
  });
  await page.goto("/");
  await page.locator("#devMore").click();
  const panel = page.locator("#devicePanel");
  await expect(panel.locator("#dpPirNone")).toHaveText("Motion sensor: not fitted on this castle.");
  await expect(panel.locator("#dpPirArm, #dpPirScene, #dpPirCool")).toHaveCount(0);
  // The rest of the panel is still wired: the boot log below the sensor opens.
  await panel.locator("#dpLog").click();
  await expect(page.locator("#dpLogOut")).toContainText("boot log: 1 line");
  expect(castle.hits("/api/pir")).toBe(0);
});

test("the panel links the castle's own page where the studio reached it", async ({ page }) => {
  const castle = await fakeCastle(page, [], { bridged: "192.168.1.5" });
  await page.goto("/");
  await page.locator("#devMore").click();
  const owner = page.locator("#dpOwner");
  await expect(owner).toHaveAttribute("href", "http://192.168.1.5/owner");
  await expect(owner).toHaveAttribute("target", "_blank");
  await expect(owner).toContainText("castle's own page");
  // A status that did not come through the studio names no address.
  delete castle.status["bridged"];
  await page.locator("#dpClose").click();
  await page.locator("#devMore").click();
  await expect(page.locator("#dpLog")).toBeVisible();
  await expect(page.locator("#dpOwner")).toHaveCount(0);
});
