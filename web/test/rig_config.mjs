/**
 * The rig panel's emitted config against the rig the castle actually runs.
 *
 *     node test/rig_config.mjs                     (from web/; dist built)
 *
 * rig_panel.emitConfig() writes the two blocks a rig change needs — the
 * scenes.yaml `zones:` and the castle.yaml `light:` strips. tests/
 * test_rig_config.py holds the Python generator to the same facts; this holds
 * the browser side, and checks the desk's DEFAULT_RIG really is the castle in
 * scenes.yaml (pins, fixtures, RGB vs RGBW), so a desk with no saved rig
 * previews the thing that exists.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  DEFAULT_RIG, FIXTURES, ZONE_DECL, ZONE_PIN, fixture, layoutOf, rigPower,
  zoneLayout, zoneRgbw,
} from "../dist/rig.mjs";
import { emitConfig } from "../dist/rig_panel.mjs";

const ROOT = join(import.meta.dirname, "..", "..");
let pass = 0;
const fails = [];
const ok = (cond, msg) => { if (cond) pass++; else fails.push(msg); };

/* scenes.yaml's zones, read the way the generator reads them */
const text = readFileSync(join(ROOT, "scenes", "scenes.yaml"), "utf8");
const block = text.split(/^zones:\s*$/m)[1].split(/^\S/m)[0];
const declared = [...block.matchAll(/\{([^}]*)\}/g)].map((m) => {
  const kv = {};
  for (const part of m[1].split(",")) {
    const i = part.indexOf(":");
    if (i < 0) continue;
    const k = part.slice(0, i).trim();
    const v = part.slice(i + 1).trim().replace(/^"|"$/g, "");
    kv[k] = v;
  }
  return kv;
});
ok(declared.length === 3, `scenes.yaml declares ${declared.length} zones`);
ok(declared.map((z) => z.id).join() === ZONE_DECL.join(),
   `scenes.yaml zone order ${declared.map((z) => z.id)} vs ZONE_DECL ${ZONE_DECL}`);

/* the desk's default rig IS the castle */
for (const z of declared) {
  const id = z.id;
  ok(Number(z.pin) === ZONE_PIN[id], `${id}: scenes.yaml pin ${z.pin}, desk ZONE_PIN ${ZONE_PIN[id]}`);
  ok(DEFAULT_RIG.zones[id]?.fixture === z.fixture,
     `${id}: scenes.yaml fixture ${z.fixture}, DEFAULT_RIG ${DEFAULT_RIG.zones[id]?.fixture}`);
  const rgbw = (z.rgbw ?? "true") === "true";
  ok(zoneRgbw(DEFAULT_RIG, id) === rgbw,
     `${id}: scenes.yaml rgbw ${rgbw}, DEFAULT_RIG says ${zoneRgbw(DEFAULT_RIG, id)}`);
  const n = z.pixels ? Number(z.pixels) : layoutOf(fixture(z.fixture)).n;
  ok(zoneLayout(DEFAULT_RIG, id).n === n, `${id}: ${n} px in scenes.yaml, desk ${zoneLayout(DEFAULT_RIG, id).n}`);
}

/* what the panel emits for the default rig */
{
  const out = emitConfig(DEFAULT_RIG);
  const strips = out.split("- platform: esp32_rmt_led_strip").slice(1);
  const live = ZONE_DECL.filter((z) => zoneLayout(DEFAULT_RIG, z).n > 0);
  ok(strips.length === live.length, `${strips.length} strips for ${live.length} wired zones`);
  strips.forEach((s, i) => {
    const z = live[i];
    ok(s.includes("rmt_symbols: 64"), `${z}: rmt_symbols: 64 missing — the S2 fix`);
    ok(s.includes("use_psram: false"), `${z}: use_psram: false missing`);
    ok(s.includes(`pin: GPIO${ZONE_PIN[z]}`), `${z}: wrong pin in emitted strip`);
    ok(s.includes(`num_leds: ${zoneLayout(DEFAULT_RIG, z).n}`), `${z}: wrong num_leds`);
    ok(s.includes(`is_rgbw: ${zoneRgbw(DEFAULT_RIG, z)}`), `${z}: wrong is_rgbw`);
    ok(s.includes(`id: zone_${z}`), `${z}: strip id`);
  });
  for (const z of declared) {
    ok(new RegExp(`\\{id: ${z.id}, channel: \\d, pin: ${z.pin}, fixture: ${z.fixture}, `
                  + `pixels: ${zoneLayout(DEFAULT_RIG, z.id).n}, rgbw: ${(z.rgbw ?? "true")}\\}`).test(out),
       `${z.id}: emitted zones: line does not match scenes.yaml`);
  }
  const { amps, pixels } = rigPower(DEFAULT_RIG);
  ok(pixels === 26, `default rig is 26 pixels, got ${pixels}`);
  ok(Math.abs(amps - (14 * 0.08 + 12 * 0.06)) < 1e-9, `default rig peak ${amps} A`);
}

/* every fixture, in every spot, RGBW both ways */
for (const fx of FIXTURES) {
  for (const rgbw of [true, false]) {
    const rig = {
      zones: { towerL: { fixture: fx.id, count: 2 }, door: { fixture: "jewel7" }, towerR: { fixture: "none" } },
      rgbw: { ...DEFAULT_RIG.rgbw, [fx.id]: rgbw },
    };
    const out = emitConfig(rig);
    const L = zoneLayout(rig, "towerL");
    if (L.n === 0) {
      ok(out.includes("# towerL: nothing wired — no strip emitted."), `${fx.id}: empty zone comment`);
      continue;
    }
    const strip = out.split("- platform: esp32_rmt_led_strip").find((s) => s.includes("id: zone_towerL"));
    ok(strip !== undefined, `${fx.id}: no towerL strip`);
    ok(strip?.includes(`num_leds: ${L.n}`), `${fx.id}: num_leds ${L.n}`);
    const want = fx.rgbOnly ? false : rgbw;
    ok(strip?.includes(`is_rgbw: ${want}`), `${fx.id} rgbw=${rgbw}: is_rgbw should be ${want}`);
    ok(strip?.includes("rmt_symbols: 64") && strip?.includes("use_psram: false"),
       `${fx.id}: S2 strip settings`);
  }
}

console.log(`rig config: ${pass} checks`);
if (fails.length) {
  for (const m of fails) console.error("  FAIL " + m);
  process.exit(1);
}
console.log("PASS");
