/**
 * Tests for what cue format v2 added to the desk's show engine — the copy of
 * firmware/castle_layers.h in src/show_layers.ts and its use in show.ts:
 * the ornament layer, the half-fixture and arc strike masks, look records and the
 * tempo-locked overlay clock, and the per-zone train rule of the soften.
 * The firmware's own side is tests/cxx/layers_check.cpp.
 *
 *     (runs bundled from web/dist — see package.json "test")
 */

import {
  createState, rebuildLightsAt, fireCues, decayFlashes, renderZones, applyLook,
  dominantFlash, ZONE_IDS,
} from "../src/show.js";
import { defaultParams, flashGate, FLASH_MODES } from "../src/effects.js";
import {
  noteStrike, overlayHead, legacyHead, setMotion, zoneExtra, SOFTEN_WINDOW_MS,
} from "../src/show_layers.js";
import type { LookCue } from "../src/show_layers.js";
import type { Scene } from "../src/types.js";

let pass = 0;
const fails: string[] = [];
const ok = (cond: boolean, msg: string): void => { if (cond) pass++; else fails.push(msg); };
const near = (a: number, b: number, eps: number, msg: string): void =>
  ok(Math.abs(a - b) <= eps, `${msg} (${a} vs ${b})`);

const scene = (cues: unknown[] = []): Scene => ({
  id: "t", name: "T", kind: "test", dur: 10000, loop: false, volume: 0.8,
  blurb: "", base: { towerL: "off", towerR: "off", door: "off" },
  levels: {}, cues, file: "t.mp3", yaml: "",
} as unknown as Scene);
const P = defaultParams();

/* ── The ornament ADDS to layer 0, clamped, and decays on its own ──── */
{
  const st = createState(scene(), 0);
  st.flash.door = 0.3;
  st.x.door.ornFlash = 0.2;
  const px = renderZones(st, 1, P).door.pix[0]!;
  near(px[0], 0.3 * 0.92 + 0.2 * 0.92, 1e-9, "both layers add on a dark base");
  st.flash.door = 1; st.x.door.ornFlash = 1;
  ok(renderZones(st, 1, P).door.pix.every((p) => p[0] === 1), "two full layers clamp at 1");
  st.x.door.ornDecay = 0.5;
  st.flashDecay.door = 0.9;
  decayFlashes(st);
  near(st.flash.door, 0.9, 1e-9, "layer 0 decays at its own rate");
  near(st.x.door.ornFlash, 0.5, 1e-9, "layer 1 decays at its own rate");
  st.x.door.ornFlash = 0.8; st.flash.door = 0.1;
  ok(dominantFlash(st).flash === 0.8, "the wash sees the ornament");
}

/* ── A layer-1 strike leaves layer 0 standing ─────────────────────── */
{
  const sc = scene([
    { t: 0, bus: "LED", op: "strike", targets: ["door"], intensity: 0.9 },
    { t: 10, bus: "LED", op: "strike", targets: ["door"], intensity: 0.2, layer: 1,
      pixels: "left", color: [1, 0, 0, 0] },
  ]);
  const st = createState(sc, 0);
  rebuildLightsAt(st, sc, 0);
  fireCues(st, 10, () => {});
  near(st.flash.door, 0.9, 1e-9, "the strong layer-0 hit is not cut short");
  near(st.x.door.ornFlash, 0.2, 1e-9, "the ornament landed");
  ok(st.x.door.ornMode === 4 && st.x.door.ornCol[0] === 1, "with its own mask and colour");
}

/* ── Half masks: complementary, by drawn position ─────────────────── */
{
  ok(FLASH_MODES.slice(4, 8).join() === "left,right,top,bottom", "modes 4-7 are the halves");
  const st = createState(scene(), 0);
  for (const id of ZONE_IDS) {
    const L = st.layout[id];
    for (const [a, b] of [[4, 5], [6, 7]] as const) {
      let litA = 0, litB = 0;
      for (let p = 0; p < L.n; p++) {
        const ga = flashGate(a, p, 0, 0, L), gb = flashGate(b, p, 0, 0, L);
        ok((ga === 1 && gb === 0.1) || (ga === 0.1 && gb === 1) || (ga === 0.5 && gb === 0.5),
           `${id} p${p} modes ${a}/${b}: ${ga} ${gb}`);
        litA += ga === 1 ? 1 : 0;
        litB += gb === 1 ? 1 : 0;
      }
      ok(litA > 0 && litA === litB, `${id} modes ${a}/${b} split evenly (${litA}/${litB})`);
    }
  }
}

/* ── Arcs (modes 8-15): one patch of the loop, the same on both layers ─ */
{
  ok(FLASH_MODES.slice(8).join() === "arc0,arc1,arc2,arc3,arc4,arc5,arc6,arc7",
     "modes 8-15 are the arcs");
  // arc0 and arc1, pixel for pixel — castle_effects.h's own table
  // (tests/cxx/layers_check.cpp kJewelArc / kRingArc).
  const want: Record<string, number[][]> = {
    jewel: [[0.3, 1, 0.55, 0.1, 0.1, 0.1, 0.55], [0.3, 0.775, 1, 0.325, 0.1, 0.1, 0.1]],
    ring: [[1, 1, 0.55, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.55, 1],
      [0.775, 1, 1, 0.775, 0.325, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.325]],
  };
  const st = createState(scene(), 0);
  const seen = { jewel: 0, ring: 0 };
  ZONE_IDS.forEach((id, zi) => {
    const L = st.layout[id];
    const kind = L.n === 7 && L.center === 0 ? "jewel" : L.n === 12 && L.center === null
      ? "ring" : null;
    if (kind) seen[kind]++;
    const g = (k: number, p: number): number => flashGate(8 + k, p, zi, 0, L);
    for (let k = 0; k < 2 && kind; k++) {
      for (let p = 0; p < L.n; p++) {
        ok(g(k, p) === want[kind]![k]![p], `${id} arc${k} p${p}: ${g(k, p)}`);
      }
    }
    // The other six are those two turned (a quarter turn is three pixels on a
    // Ring12, half a turn three on a Jewel's six).
    for (let k = 0; k + 2 < 8 && kind === "ring"; k++) {
      for (let p = 0; p < 12; p++) ok(g(k + 2, (p + 3) % 12) === g(k, p), `${id} arc${k} turned`);
    }
    for (let k = 0; k + 4 < 8 && kind === "jewel"; k++) {
      for (let j = 0; j < 6; j++) {
        ok(g(k + 4, 1 + ((j + 3) % 6)) === g(k, 1 + j), `${id} arc${k} turned`);
      }
    }
    // The same arc on layer 0 and on the ornament lights the same pixels.
    for (let k = 0; k < 8; k++) {
      const a = createState(scene(), 0), b = createState(scene(), 0);
      a.flash[id] = 0.8; a.flashMode[id] = 8 + k;
      b.x[id].ornFlash = 0.8; b.x[id].ornMode = 8 + k;
      const pa = renderZones(a, 1, P)[id].pix, pb = renderZones(b, 1, P)[id].pix;
      ok(pa.every((px, p) => px.every((v, c) => v === pb[p]![c])),
         `${id} arc${k}: the two layers differ`);
      // …and each pixel is what an unmasked strike of 0.8 x its gate draws.
      ok(pa.every((px, p) => {
        const c = createState(scene(), 0);
        c.flash[id] = 0.8 * g(k, p);
        const ref = renderZones(c, 1, P)[id].pix[p]!;
        return px.every((v, ch) => Math.abs(v - ref[ch]!) < 1e-9);
      }), `${id} arc${k}: not the gate times the strike`);
    }
  });
  ok(seen.jewel > 0 && seen.ring > 0, `the desk rig has ${seen.jewel} Jewel7s, ${seen.ring} Ring12s`);
  // A layer-1 strike named by arc lands its mode on the ornament.
  const sc = scene([{ t: 0, bus: "LED", op: "strike", targets: ["door"], intensity: 0.5,
    layer: 1, pixels: "arc3" }]);
  const s2 = createState(sc, 0);
  rebuildLightsAt(s2, sc, 0);
  fireCues(s2, 1, () => {});
  ok(s2.x.door.ornMode === 11 && s2.flashMode.door === 0, "arc3 on layer 1 is ornament mode 11");
}

/* ── Look records and the overlay clock ───────────────────────────── */
{
  const st = createState(scene(), 0);
  const look = (c: Omit<LookCue, "op">): void => applyLook(st, { op: "look", ...c });
  ok(overlayHead(st.x.towerL, 3) === -1, "a fresh zone runs the legacy clock");
  look({ t: 1000, targets: ["towerL"], overlay: "chase", rate: 0.5 });
  ok(st.overlay.towerL === 2 && st.overlay.door === 0, "overlay set on the named zone only");
  near(overlayHead(st.x.towerL, 1), legacyHead(2, 1 + st.phase.towerL, 0), 1e-12,
       "leaving the legacy clock does not move the head");
  const before = overlayHead(st.x.towerL, 2.5);
  look({ t: 2500, targets: ["towerL"], rate: 2 });
  near(overlayHead(st.x.towerL, 2.5), before, 1e-12, "a rate change does not move the head");
  near(overlayHead(st.x.towerL, 2.75), (before + 0.5) % 1, 1e-12, "and runs at the new rate");
  look({ t: 3000, targets: ["towerL"], head: 0.25 });
  ok(st.x.towerL.rate === 2 && overlayHead(st.x.towerL, 3) === 0.25, "head set, rate kept");
  look({ t: 3100, targets: ["towerL"], rate: 0 });
  ok(overlayHead(st.x.towerL, 9) === -1, "rate 0 is the legacy clock again");
  look({ t: 3200, palette: "moonlight", center: "none" });
  ok(ZONE_IDS.every((z) => st.palette[z] === 2 && st.centerEff[z] === null),
     "a look with no targets reaches every zone");
  look({ t: 3300, targets: ["door"], center: "eyes" });
  ok(st.centerEff.door === "eyes", "a centre role by name");
  // A seek lands in the same state as playing through.
  const cues = [
    { t: 0, bus: "LED", op: "look", targets: ["door"], overlay: "meteor", rate: 1.5 },
    { t: 700, bus: "LED", op: "look", targets: ["door"], rate: 0.25 },
  ];
  const played = createState(scene(cues), 0);
  rebuildLightsAt(played, scene(cues), 0);
  fireCues(played, 800, () => {});
  const sought = createState(scene(cues), 0);
  rebuildLightsAt(sought, scene(cues), 800);
  near(overlayHead(played.x.door, 5), overlayHead(sought.x.door, 5), 1e-12,
       "seek and play agree on the head");
  ok(played.overlay.door === 3 && sought.overlay.door === 3, "and on the overlay");
}

/* ── The train rule, exactly at its edge ──────────────────────────── */
{
  const x = zoneExtra();
  ok(SOFTEN_WINDOW_MS === 333, "the window is 333 ms");
  ok(!noteStrike(x, 1000), "the first strike after load is never a train");
  ok(noteStrike(x, 1332), "332 ms after is");
  ok(noteStrike(x, 1332), "a second strike in the same millisecond is the same event");
  ok(!noteStrike(x, 1665), "333 ms after is not");
  const y = zoneExtra();
  setMotion(y, 2, 0, 0, 1, -1, 0.5);
  ok(y.rate === 0 && overlayHead(y, 1) === -1, "a head alone does not leave the legacy clock");
}

console.log(`show layers: ${pass} assertions`);
if (fails.length) {
  for (const m of fails) console.error("  FAIL " + m);
  process.exit(1);
}
console.log("PASS");
