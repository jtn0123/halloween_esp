/**
 * Tests for the show engine — the code that decides what the lights do.
 *
 *     node test/show_engine.mjs
 *
 * This is the highest-consequence logic in the browser half: a bug here is a
 * wrong show, and it mirrors the firmware's own render loop, so a divergence
 * means the previewer stops predicting the device. It is also pure — no DOM,
 * no audio graph — so there is no excuse for it being untested.
 *
 * Plain assertions rather than a test framework, matching effects_equivalence.
 */

import {
  createState, rebuildLightsAt, fireCues, decayFlashes, renderZones,
  dominantFlash, step, ZONE_IDS, PIXELS_PER_ZONE,
} from "../dist/show.mjs";
import { defaultParams } from "../dist/effects.mjs";

let pass = 0;
const fails = [];
const ok = (cond, msg) => { if (cond) pass++; else fails.push(msg); };
const near = (a, b, eps, msg) => ok(Math.abs(a - b) <= eps, `${msg} (${a} vs ${b})`);

/** A scene shaped exactly like what gen_previewer.py emits. */
const scene = (over = {}) => ({
  id: "t", name: "T", kind: "test", dur: 10000, loop: false, volume: 0.8,
  blurb: "", base: { towerL: "off", towerR: "off", door: "off" },
  levels: {}, cues: [], file: "t.mp3", yaml: "", ...over,
});

const P = defaultParams();

/* ── Base state ──────────────────────────────────────────────────── */
{
  const sc = scene({ base: { towerL: "candle", towerR: "ember", door: "blood" } });
  const st = createState(sc, 0);
  rebuildLightsAt(st, sc, 0);
  ok(st.eff.towerL === "candle" && st.eff.door === "blood", "base effects applied");
  ok(ZONE_IDS.every(z => st.flash[z] === 0), "flashes start at zero");
  ok(ZONE_IDS.every(z => st.level[z] === 1), "levels default to 1");
}

/* ── Levels ──────────────────────────────────────────────────────── */
{
  const sc = scene({ levels: { towerL: 0.35 } });
  const st = createState(sc, 0);
  rebuildLightsAt(st, sc, 0);
  ok(st.level.towerL === 0.35, "scene levels applied");
  ok(st.level.door === 1, "unlisted zones stay at full");
}

/* ── Seeking rebuilds state — the classic bug this guards ────────── */
{
  const sc = scene({ cues: [
    { t: 1000, bus: "LED", op: "set", zone: "towerL", eff: "eyes" },
    { t: 2000, bus: "LED", op: "set", zone: "towerL", eff: "chill", level: 0.2 },
    { t: 8000, bus: "LED", op: "set", zone: "door", eff: "furnace" },
  ]});
  const st = createState(sc, 0);

  rebuildLightsAt(st, sc, 2500);
  ok(st.eff.towerL === "chill", "seek applies the latest set cue, not the first");
  ok(st.level.towerL === 0.2, "seek applies that cue's level too");
  ok(st.eff.door === "off", "seek does not apply cues from the future");
  ok(st.fired.size === 2, "past cues are marked fired so they do not re-run");

  // Seeking backwards must forget the future again.
  rebuildLightsAt(st, sc, 500);
  ok(st.eff.towerL === "off", "seeking back returns to base");
  ok(st.fired.size === 0, "seeking back clears fired history");
}

/* ── Strikes ─────────────────────────────────────────────────────── */
{
  const sc = scene({ cues: [
    { t: 0, bus: "LED", op: "strike", ms: 80 },                       // all zones
    { t: 100, bus: "LED", op: "strike", ms: 80, targets: ["door"], intensity: 0.5 },
    { t: 200, bus: "LED", op: "strike", ms: 80, zone: "towerR", intensity: 0.25 },
  ]});
  const st = createState(sc, 0);
  rebuildLightsAt(st, sc, 0);

  fireCues(st, 0, () => {});
  ok(ZONE_IDS.every(z => st.flash[z] === 1), "a strike with no target hits every zone");

  st.flash.door = 0;
  fireCues(st, 150, () => {});
  near(st.flash.door, 0.5, 1e-9, "targets[] strike lands on its zone at its intensity");

  st.flash.towerR = 0;
  fireCues(st, 250, () => {});
  near(st.flash.towerR, 0.25, 1e-9, "legacy `zone` field still works");
}

/* ── Strike colour and decay ─────────────────────────────────────── */
{
  const sc = scene({ cues: [
    { t: 0, bus: "LED", op: "strike", ms: 80, targets: ["door"],
      color: [1, 0.03, 0.01, 0], decay: 0.82 },
  ]});
  const st = createState(sc, 0);
  rebuildLightsAt(st, sc, 0);
  fireCues(st, 0, () => {});
  ok(st.flashCol.door[0] === 1 && st.flashCol.door[3] === 0, "strike colour applied");
  ok(st.flashDecay.door === 0.82, "strike decay applied");
  ok(st.flashDecay.towerL === 0.90, "other zones keep the default decay");
}

/* ── Decay maths matches the firmware ────────────────────────────── */
{
  const st = createState(scene(), 0);
  st.flash.door = 1; st.flashDecay.door = 0.9; st.soft = false;
  decayFlashes(st);
  near(st.flash.door, 0.9, 1e-9, "one frame of decay");

  st.flash.door = 0.003;
  decayFlashes(st);
  ok(st.flash.door === 0, "a flash below the floor snaps to zero, not a long tail");

  // Soft mode slows the fall: 0.90 -> 1-(1-0.90)*0.35 = 0.965
  const s2 = createState(scene(), 0);
  s2.flash.door = 1; s2.flashDecay.door = 0.9; s2.soft = true;
  decayFlashes(s2);
  near(s2.flash.door, 0.965, 1e-9, "soft mode slows decay to the firmware's curve");
}

/* ── Soft mode caps strike intensity (photosensitivity) ──────────── */
{
  const sc = scene({ cues: [{ t: 0, bus: "LED", op: "strike", ms: 80 }] });
  const st = createState(sc, 0);
  rebuildLightsAt(st, sc, 0);
  st.soft = true;
  fireCues(st, 0, () => {});
  near(st.flash.door, 0.42, 1e-9, "soft mode damps a full-intensity strike");
}

/* ── Rendering ───────────────────────────────────────────────────── */
{
  const sc = scene({ base: { towerL: "candle", towerR: "candle", door: "candle" } });
  const st = createState(sc, 0);
  rebuildLightsAt(st, sc, 0);
  const out = renderZones(st, 1.234, P);
  ok(Object.keys(out).length === 3, "every zone renders");
  ok(out.towerL.pix.length === PIXELS_PER_ZONE, "seven pixels per zone");
  const allInRange = ZONE_IDS.every(z =>
    out[z].pix.every(p => p.every(c => c >= 0 && c <= 1)));
  ok(allInRange, "channels stay inside 0..1");

  // Per-pixel seeding is what makes a flame move ACROSS a jewel.
  const distinct = new Set(out.towerL.pix.map(p => p.join(","))).size;
  ok(distinct > 1, "pixels within a zone differ — per-pixel seeding is live");
}

/* ── level scales the base but NOT the strike ────────────────────── */
{
  const dim = createState(scene({ base: { towerL: "furnace", towerR: "off", door: "off" },
                                  levels: { towerL: 0.1 } }), 0);
  rebuildLightsAt(dim, dim.scene, 0);
  const dimOut = renderZones(dim, 2.0, P).towerL.pix[0];

  const full = createState(scene({ base: { towerL: "furnace", towerR: "off", door: "off" } }), 0);
  rebuildLightsAt(full, full.scene, 0);
  const fullOut = renderZones(full, 2.0, P).towerL.pix[0];
  ok(dimOut[0] < fullOut[0], "level dims the base effect");

  // With a strike up, the dim zone must still reach full brightness — this is
  // the whole mechanism that gives a scene contrast.
  dim.flash.towerL = 1; dim.flashCol.towerL = [1, 1, 1, 1];
  const struck = renderZones(dim, 2.0, P).towerL.pix[0];
  ok(struck[0] > 0.9, "a strike is unscaled by level");
}

/* ── dominantFlash picks the strongest, with its colour ──────────── */
{
  const st = createState(scene(), 0);
  st.flash.towerL = 0.3; st.flashCol.towerL = [1, 0, 0, 0];
  st.flash.door = 0.7; st.flashCol.door = [0, 1, 0, 0];
  const d = dominantFlash(st);
  near(d.flash, 0.7, 1e-9, "strongest flash wins");
  ok(d.color[1] === 1, "and brings its own colour");
}

/* ── step(): looping, ending, and audio cues ─────────────────────── */
{
  // A looping scene wraps and re-arms its cues.
  const sc = scene({ loop: true, dur: 1000,
                     cues: [{ t: 100, bus: "AUD", op: "play", snd: "wind" }] });
  const st = createState(sc, 0);
  rebuildLightsAt(st, sc, 0);
  st.running = true; st.t0 = 0;

  let plays = 0;
  step(st, 200, P, () => plays++, () => {});
  ok(plays === 1, "audio cue fires");
  step(st, 300, P, () => plays++, () => {});
  ok(plays === 1, "and does not re-fire within the same pass");
  step(st, 1200, P, () => plays++, () => {});   // past the loop point
  ok(plays === 2, "loop re-arms cues for the next time round");

  // A non-looping scene reports the end exactly once and clamps its clock.
  const one = scene({ loop: false, dur: 500 });
  const s2 = createState(one, 0);
  rebuildLightsAt(s2, one, 0);
  s2.running = true; s2.t0 = 0;
  let ended = 0;
  const f = step(s2, 900, P, () => {}, () => ended++);
  ok(ended === 1, "non-looping scene signals its end");
  near(f.elapsed, 500, 1e-9, "and its clock stops at the duration");
}

/* ── A paused show must not fire cues ────────────────────────────── */
{
  const sc = scene({ cues: [{ t: 10, bus: "AUD", op: "play", snd: "wind" }] });
  const st = createState(sc, 0);
  rebuildLightsAt(st, sc, 0);
  st.running = false; st.held = 5000;
  let plays = 0;
  step(st, 9999, P, () => plays++, () => {});
  ok(plays === 0, "a paused show fires nothing, however far the clock has moved");
}

/* ── Attack (#10): a strike can swell to its peak instead of popping ── */
{
  const sc = scene({ cues: [
    { t: 0, bus: "LED", op: "strike", ms: 200, targets: ["door"],
      intensity: 0.8, decay: 0.9, attack: 96 },
  ]});
  const st = createState(sc, 0);
  fireCues(st, 0, () => {});
  ok(st.flash.door === 0, "an attack strike starts from dark, not at peak");
  ok(st.flashTarget.door === 0.8, "the peak is armed");
  decayFlashes(st);
  ok(Math.abs(st.flash.door - 0.8 * 16 / 96) < 1e-9,
     "one frame climbs peak*16/attack — the firmware's exact arithmetic");
  // 6 exact frames in theory; float accumulation means the clamp lands on
  // the 7th. What matters is reaching the peak and disarming.
  for (let i = 0; i < 6; i++) decayFlashes(st);
  ok(st.flash.door === 0.8 && st.flashTarget.door === 0,
     "the swell reaches the peak and disarms");
  decayFlashes(st);
  ok(Math.abs(st.flash.door - 0.72) < 1e-9, "then ordinary decay takes over");
}
{
  const sc = scene({ cues: [
    { t: 0, bus: "LED", op: "strike", ms: 100, targets: ["door"],
      intensity: 0.5, decay: 0.9, attack: 200 },
    { t: 50, bus: "LED", op: "strike", ms: 100, targets: ["door"],
      intensity: 1.0, decay: 0.9 },
  ]});
  const st = createState(sc, 0);
  fireCues(st, 0, () => {});
  decayFlashes(st);
  fireCues(st, 60, () => {});
  ok(st.flash.door === 1 && st.flashTarget.door === 0,
     "a slam mid-swell wins instantly and cancels the pending rise");
}

console.log(`show engine: ${pass} assertions`);
if (fails.length) {
  console.error(`\nFAILED — ${fails.length}:`);
  for (const f of fails) console.error("  " + f);
  process.exit(1);
}
console.log("PASS");
