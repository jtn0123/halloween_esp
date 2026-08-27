/**
 * Tests for the cinematic track-light generator.
 *
 *     (runs bundled from web/dist — see package.json "test")
 *
 * The numbers here are the SAME numbers tests/test_generator_parity.py pins
 * the two Python generators to (TestPulseDynamicsParity). Three copies of the
 * arithmetic exist by design; these two files meeting at identical values is
 * what keeps the browser's audition honest about the device.
 */

import {
  BAND_STYLE, BAND_STYLE_CLASSIC, TIERS, blendColor, pixelsForVel, bandStrikes,
  sections, sectionCues, trackCues,
  setStyleVariant, styleVariant, setStyleTweak, resetStyleTweaks, styleFor,
  styleAsTs, tempoFactor, tempoDecay, isAccent, PAN_DECISIVE,
  setFlavor, resetFlavors, driftBase, TAKEOVER_COLORS, TAKEOVER_HOT,
  sustainedSwells,
} from "../src/track_lights.js";
import type { Rgbw } from "../src/types.js";

type Hit = [number, number] | [number, number, number];

let pass = 0;
const fails: string[] = [];
const ok = (c: boolean, m: string): void => { if (c) pass++; else fails.push(m); };
const eq = (a: unknown, b: unknown, m: string): void =>
  ok(JSON.stringify(a) === JSON.stringify(b), `${m} — got ${JSON.stringify(a)}`);

/* ── Colour blend: the numbers from TestPulseDynamicsParity ── */
const base: Rgbw = [1.0, 0.0, 0.0, 0.0], hot: Rgbw = [1.0, 0.5, 0.0, 0.4];
eq(blendColor(base, hot, 1.0), [1, 0.5, 0, 0.4], "vel 1.0 lands on the hot colour");
eq(blendColor(base, hot, 0.55), [1, 0.275, 0, 0.22000000000000003],
   "vel 0.55 blends 55% of the way");
eq(blendColor(base, hot, 0.4).map(v => Math.round(v * 1000) / 1000),
   [1, 0.2, 0, 0.16], "vel 0.40 blends 40%");
eq(blendColor(base, undefined, 0.9), base, "no hot colour: the base passes through");

/* ── Velocity masks: same thresholds as pixels_for in the generators ── */
ok(pixelsForVel(0.2) === "center", "soft hits touch only the centre");
ok(pixelsForVel(0.40) === "scatter", "0.40 sits on the edge and scatters (<, not <=)");
ok(pixelsForVel(0.55) === "scatter", "medium hits scatter");
ok(pixelsForVel(0.72) === "all", "0.72 takes the whole jewel");
ok(pixelsForVel(1.0) === "all", "hard hits take the whole jewel");

/* ── Strike expansion ── */
const hits: Hit[] = [[0.0, 1.0], [0.153, 0.55], [0.82, 0.91], [1.6, 0.4]];
{
  const s = bandStrikes("onset_low", hits, 0, 10);
  eq(s.map(c => c.t), [0, 153, 820, 1600], "times are clip-relative ms");
  eq(s[0]!.targets, ["door", "towerL", "towerR"],
     "a vel-1.0 low hit boosts onto both towers");
  eq(s[1]!.targets, ["door"], "a vel-0.55 low hit stays at the door");
  eq(s[2]!.targets, ["door", "towerL", "towerR"], "0.91 clears boost_at 0.85");
  ok(s[0]!.pixels === "all" && s[1]!.pixels === "scatter" && s[3]!.pixels === "scatter",
     "low band masks follow velocity");
  ok(s.every(c => c.op === "strike" && c.bus === "LED"), "strikes are LED strikes");
  ok(Math.abs(s[1]!.intensity! - BAND_STYLE.onset_low.intensity * 0.55) < 0.001,
     "intensity scales by velocity");
}
{
  const s = bandStrikes("onset_mid", hits, 0, 10);
  eq(s.map(c => c.targets![0]), ["towerL", "towerR", "towerL", "towerR"],
     "mid alternates between the towers");
}
{
  const s = bandStrikes("onset_high", hits, 0, 10);
  ok(s.every(c => c.pixels === "scatter"), "highs always scatter");
  eq(s.map(c => c.targets![0]), ["towerR", "door", "towerL", "towerR"],
     "highs walk towerR→door→towerL");
}
{
  const s = bandStrikes("onset_mid", hits, 0, 10, "door");
  ok(s.every(c => c.targets!.length === 1 && c.targets![0] === "door"),
     "pinning a band in the editor overrides the movement");
}
{
  const s = bandStrikes("onset_low", hits, 0.5, 1.0);
  eq(s.map(c => c.t), [320], "hits outside the clip are dropped, times re-based");
}

/* ── Sections ── */
{
  // 0-10s quiet, 10-20s loud, 20-30s quiet — a chorus with verses around it.
  const env: [number, number][] = [];
  for (let t = 0; t <= 30; t += 0.25) {
    env.push([t, t >= 10 && t < 20 ? 0.9 : 0.1]);
  }
  const secs = sections(env, 0, 30);
  eq(secs.map(([, tier]) => tier), [0, 2, 0],
     "quiet→loud→quiet becomes three sections");
  ok(Math.abs(secs[1]![0] - 10) < 2, "the chorus boundary lands near 10s");
  const cues = sectionCues(env, 0, 30);
  ok(cues.filter(c => c.detail !== "predim").length === 9,
     "three sections × three zones");
  ok(cues.every(c => c.op === "set"), "sections are set cues");
  // #6: the chorus entry is preceded by a dip in the PREVIOUS look.
  const dips = cues.filter(c => c.detail === "predim");
  ok(dips.length === 3, "one pre-dim trio before the chorus");
  const chorusT = cues.find(c => c.detail === "chorus")!.t;
  ok(dips.every(d => d.t === chorusT - 450), "the dip sits 450 ms early");
  ok(dips.some(d => d.eff === TIERS[0]!.towers
                 && d.level === Math.round(TIERS[0]!.towersLevel * 0.45 * 1000) / 1000),
     "the dip is the previous tier's look at 45%");
  const loud = cues.filter(c => c.detail === "chorus");
  ok(loud.some(c => c.zone === "towerL" && c.eff === TIERS[2]!.towers),
     "the chorus look reaches the towers");
}
eq(sections(undefined, 0, 30), [[0, 1]], "no envelope: hold the middle tier");
eq(sections([], 0, 30), [[0, 1]], "empty envelope: hold the middle tier");
{
  // A level flickering across a boundary must not flap the whole castle.
  const env: [number, number][] = [];
  for (let t = 0; t <= 30; t += 0.25) env.push([t, 0.40 + (t % 0.5 < 0.25 ? 0.04 : -0.04)]);
  const secs = sections(env, 0, 30);
  ok(secs.length <= 2, `hysteresis holds a hovering level steady (got ${secs.length})`);
}

/* ── The full expansion ── */
{
  const cues = trackCues({ onset_low: hits, onset_mid: hits }, undefined, 0, 10);
  ok(cues.every((c, i) => i === 0 || cues[i - 1]!.t <= c.t), "cues come out sorted");
  ok(cues.some(c => c.op === "set"), "sections are present even without an envelope");
  ok(cues.filter(c => c.op === "strike").length === 8, "both bands' strikes made it");
}

/* ── Per-hit colour cycling: consecutive hits differ in hue ── */
{
  const soft: Hit[] = [[0.0, 0.3], [0.5, 0.3], [1.0, 0.3], [1.5, 0.3]];
  const s = bandStrikes("onset_mid", soft, 0, 10);
  const keys = s.map(c => c.color!.join(","));
  ok(new Set(keys.slice(0, 3)).size === 3,
     "three consecutive hits use three different base colours");
  ok(keys[0] === keys[3], "the cycle wraps at its length");
}

/* ── The style table stays within the vocabulary, and stays COLOURED ── */
for (const [name, s] of Object.entries(BAND_STYLE)) {
  ok(s.colors.length >= 2, `${name} cycles at least two colours`);
  for (const c of s.colors) {
    ok(Math.min(c[0], c[1], c[2]) <= 0.30,
       `${name} cycle colours are saturated (one channel low): ${c}`);
    ok(c[3] <= 0.12, `${name} cycle colours keep W low: ${c}`);
  }
  // The hot end is what the LOUD hits render as — the visible ones. It must
  // stay a colour, or the show goes white exactly when it matters most.
  ok(Math.min(s.colorHot[0], s.colorHot[1], s.colorHot[2]) <= 0.30,
     `${name} hot colour stays saturated`);
  ok(s.colorHot[3] <= 0.15, `${name} hot colour keeps W low`);
  ok(s.decay > 0.5 && s.decay < 1, `${name} decay is a per-frame factor`);
  ok(!(s.pixelsByVel && s.pixels), `${name} does not set both mask modes`);
}

/* ── Stream dynamics: pan, tempo, accents — TestStreamDynamicsParity's
      numbers, digit for digit ── */
{
  const panned: Hit[] = [[0, 0.5, -0.6], [0.3, 0.5, 0.6], [0.6, 0.5, 0.05], [0.9, 0.5]];
  const s = bandStrikes("onset_mid", panned, 0, 10);
  eq(s.map(c => c.targets![0]), ["towerL", "towerR", "towerL", "towerR"],
     "decisive pans pick their tower; the rest keep the round-robin");
  const edge = bandStrikes("onset_mid", [[0, 0.5, 0.1], [0.3, 0.5, -0.09]], 0, 10);
  eq(edge.map(c => c.targets![0]), ["towerR", "towerR"],
     `pan ${PAN_DECISIVE} is decisive (>=), 0.09 is not`);
  const door = bandStrikes("onset_low", [[0, 0.5, -0.9]], 0, 10);
  eq(door[0]!.targets, ["door"], "a door-only stream ignores pan");
}
{
  ok(tempoFactor([0, 1, 2, 3, 4, 5, 6]) === 1.0, "7 hits are not a tempo");
  ok(Math.abs(tempoFactor(Array.from({length: 8}, (_, i) => i * 0.25)) - 0.7) < 1e-9,
     "0.25 s gaps clamp the factor at 0.7");
  ok(Math.abs(tempoFactor(Array.from({length: 8}, (_, i) => i * 0.9)) - 1.6) < 1e-9,
     "0.9 s gaps clamp at 1.6");
  ok(tempoDecay(0.90, 0.7) === 0.8571, "fast decay digit matches Python");
  ok(tempoDecay(0.90, 1.6) === 0.9375, "slow decay digit matches Python");
  const fast = bandStrikes("onset_low",
    Array.from({length: 8}, (_, i): Hit => [i * 0.25, 0.5]), 0, 10);
  ok(fast.every(c => c.ms === Math.floor(BAND_STYLE.onset_low.ms * 0.7 + 0.5)
                  && c.decay === tempoDecay(BAND_STYLE.onset_low.decay, 0.7)),
     "fast streams get shorter strikes and tails");
}
{
  const accent = bandStrikes("onset_low",
    [[0, 0.3], [0.2, 0.3], [0.4, 0.3], [0.6, 0.7]], 0, 10);
  eq(accent[3]!.targets, ["door", "towerL", "towerR"],
     "vel 0.7 among 0.3s is an accent — boost fires below the global bar");
  eq(accent[2]!.targets, ["door"], "its neighbours do not");
  ok(!isAccent([0.3, 0.7], 1), "one prior hit is not a neighbourhood");
  ok(!isAccent([0.3, 0.3, 0.3, 0.5], 3), "0.5 misses the 0.55 floor");
  // The tempo maths reads only the CLIP's hits — the Python side never sees
  // outside the clip, and the two must drift never.
  const windowed = bandStrikes("onset_low",
    [[0, 1.0], ...Array.from({length: 8}, (_, i): Hit => [5 + i * 0.25, 0.5])], 5, 10);
  ok(windowed.every(c => c.ms === Math.floor(BAND_STYLE.onset_low.ms * 0.7 + 0.5)),
     "hits outside the clip do not vote on the tempo");
}

/* ── Silence (#5) and section gating (#9) ── */
{
  // Loud, then a 3 s hole (env points below 0.04 are dropped at source, so
  // a hole IS silence), then loud again.
  const env: [number, number][] = [];
  for (let t = 0; t <= 20; t += 0.25) {
    if (t < 8 || t > 11) env.push([t, 0.9]);
  }
  const secs = sections(env, 0, 20);
  ok(secs.some(([, tier]) => tier === 3), "a held pause becomes a silence tier");
  const sil = secs.find(([, tier]) => tier === 3)!;
  ok(Math.abs(sil[0] - 8) < 1.2, `the pause starts near 8 s (got ${sil[0]})`);
  const cues = sectionCues(env, 0, 20);
  ok(cues.some(c => c.detail === "silence" && c.level! <= 0.08),
     "silence cues drop the castle near-black");
  ok(!cues.some(c => c.detail === "predim"
                  && Math.abs(c.t - (secs.find(([, tr], i) => tr === 2 && secs[i - 1]?.[1] === 3)?.[0] ?? -9) * 1000 + 450) < 1),
     "no dip when coming OUT of silence — it is already dark");
  // A 1.5 s gap is a breath, not a pause.
  const short: [number, number][] = [];
  for (let t = 0; t <= 20; t += 0.25) { if (t < 8 || t > 9.5) short.push([t, 0.9]); }
  ok(!sections(short, 0, 20).some(([, tier]) => tier === 3),
     "short gaps do not trigger the silence tier");
}
{
  const gates: [number, string][] = [[0, "hush"], [5000, "chorus"], [10000, "silence"]];
  const hits4: Hit[] = [[1, 0.8], [6, 0.8], [11, 0.8]];
  const high = bandStrikes("onset_high", hits4, 0, 20, undefined, gates);
  eq(high.map(c => c.t), [6000], "highs are gated out of hush and silence");
  const mid = bandStrikes("onset_mid", hits4, 0, 20, undefined, gates);
  ok(Math.abs(mid[0]!.intensity! - BAND_STYLE.onset_mid.intensity * 0.8 * 0.5) < 1e-9,
     "mids whisper at half intensity in a hush");
  ok(Math.abs(mid[1]!.intensity! - BAND_STYLE.onset_mid.intensity * 0.8) < 1e-9,
     "and speak at full in the chorus");
  ok(mid.length === 2, "nothing strikes in a held pause");
  const low = bandStrikes("onset_low", hits4, 0, 20, undefined, gates);
  eq(low.map(c => c.t), [1000, 6000], "the low band keeps its hush hits, drops silence");
}

/* ── Attack (#10): voices swell, drums slam ── */
{
  const mid = bandStrikes("onset_mid", [[0, 0.8]], 0, 10);
  ok(mid[0]!.attack === BAND_STYLE.onset_mid.attackMs,
     "mid strikes carry the style's attack");
  const low = bandStrikes("onset_low", [[0, 0.8]], 0, 10);
  ok(low[0]!.attack === undefined, "low strikes keep the instant slam");
}

/* ── Phase-3 flavours: drift, takeover, swells (default OFF) ── */
{
  // Drift arithmetic — the digit pinned by pulse_dynamics.drift_base.
  eq(driftBase([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]], 0, 30000),
     [0, 0.5, 0.5, 0], "half a period walks the triad half a lap");
  eq(driftBase([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]], 0, 0),
     [1, 0, 0, 0], "t=0 is the plain cycle colour");

  const one: Hit[] = [[0, 0.5]];
  const plain = bandStrikes("onset_mid", one, 0, 10)[0]!.color!.join(",");
  setFlavor("drift", true);
  ok(bandStrikes("onset_mid", one, 0, 10)[0]!.color!.join(",") === plain,
     "drift at t=0 changes nothing — it drifts, it does not repaint");
  const later = bandStrikes("onset_mid", [[20, 0.5]], 0, 60)[0]!;
  const noDrift = (resetFlavors(),
                   bandStrikes("onset_mid", [[20, 0.5]], 0, 60)[0]!);
  ok(later.color!.join(",") !== noDrift.color!.join(","),
     "20 s in, the drifted hue differs from the static cycle");

  // Takeover: chorus hits use the shared warm family.
  setFlavor("takeover", true);
  const gates: [number, string][] = [[0, "verse"], [5000, "chorus"]];
  const verse = bandStrikes("onset_mid", [[1, 1.0]], 0, 10, undefined, gates)[0]!;
  const chorus = bandStrikes("onset_mid", [[6, 1.0]], 0, 10, undefined, gates)[0]!;
  eq(chorus.color, TAKEOVER_HOT, "a vel-1.0 chorus hit lands on the takeover hot");
  ok(verse.color!.join(",") !== chorus.color!.join(","),
     "the verse keeps the band's own colour — the splinter is the point");
  for (const c of TAKEOVER_COLORS) {
    ok(Math.min(c[0], c[1], c[2]) <= 0.30 && c[3] <= 0.12,
       "takeover colours pass the same saturation guard as BAND_STYLE");
  }
  resetFlavors();

  // Swells: a loud plateau blooms; toggles default off.
  const env: [number, number][] = [];
  for (let t = 0; t <= 20; t += 0.25) env.push([t, t >= 5 && t < 12 ? 0.8 : 0.2]);
  const sw = sustainedSwells(env, 0, 20);
  ok(sw.length === 1 && sw[0]!.attack === 900 && sw[0]!.detail === "swell",
     "a 7 s plateau becomes one swell with a 900 ms attack");
  ok(Math.abs(sw[0]!.t - 5000) < 1200, "the swell starts at the plateau");
  const short2: [number, number][] = [];
  for (let t = 0; t <= 20; t += 0.25) short2.push([t, t >= 5 && t < 7.25 ? 0.8 : 0.2]);
  ok(sustainedSwells(short2, 0, 20).length === 0,
     "a 2.25 s plateau is not sustained enough");
  const cues = trackCues({ onset_mid: [[1, 0.5]] }, env, 0, 20);
  ok(!cues.some(c => c.detail === "swell"),
     "swells are OFF unless the toggle says otherwise");
  setFlavor("swells", true);
  ok(trackCues({ onset_mid: [[1, 0.5]] }, env, 0, 20)
       .some(c => c.detail === "swell"), "and ON when it does");
  setStyleVariant("classic");
  ok(!trackCues({ onset_mid: [[1, 0.5]] }, env, 0, 20)
       .some(c => c.detail === "swell"), "the classic baseline ignores flavours");
  setStyleVariant("current");
  resetFlavors();
}

/* ── The style lab: variant, tweaks, and the export's honesty ── */
{
  ok(styleVariant() === "current", "the lab starts on the current engine");
  setStyleVariant("classic");
  const s = bandStrikes("onset_mid", hits, 0, 10);
  ok(s.every(c => c.targets!.length === 1 && c.targets![0] === "towerL"),
     "classic mid stays parked on towerL — no movement");
  ok(s.every(c => c.pixels === undefined), "classic strikes hit the whole jewel");
  const keys = new Set(s.map(c => c.color!.join(",")));
  ok(keys.size === 1, "classic is ONE flat colour — velocity moves intensity only");
  const env: [number, number][] = [];
  for (let t = 0; t <= 30; t += 0.25) env.push([t, t >= 10 && t < 20 ? 0.9 : 0.1]);
  ok(sectionCues(env, 0, 30).length === 3,
     "classic holds ONE look — sections do not fire in B");
  // The cardinal rule: the export never ships the comparison baseline.
  ok(styleFor("onset_mid", true).colors.length
       === BAND_STYLE.onset_mid.colors.length,
     "styleFor(forExport) ignores the B variant");
  setStyleVariant("current");
  ok(sectionCues(env, 0, 30).filter(c => c.detail !== "predim").length === 9,
     "back on A, sections fire again");
}
{
  setStyleTweak("onset_low", { intensity: 0.5 });
  const s = styleFor("onset_low");
  ok(Math.abs(s.intensity - BAND_STYLE.onset_low.intensity * 0.5) < 1e-9,
     "intensity knob multiplies");
  setStyleTweak("onset_low", { intensity: 1.3 });
  ok(styleFor("onset_low").intensity <= 1, "intensity clamps at 1");
  setStyleTweak("onset_low", { intensity: 1, decay: 2 });
  const d0 = BAND_STYLE.onset_low.decay, d2 = styleFor("onset_low").decay;
  ok(Math.abs((1 - d2) - (1 - d0) / 2) < 1e-3,
     "decay ×2 halves the per-frame loss — twice the tail, never ≥ 1");
  ok(styleFor("onset_low", true).decay === d2,
     "tweaks DO export — what you auditioned is what ships");
  resetStyleTweaks();
  ok(styleFor("onset_low").decay === d0, "reset returns to the committed style");
  ok(styleAsTs().includes("onset_mid") && styleAsTs().includes("colorHot"),
     "the TS snippet carries the whole table");
}
{
  const cues = trackCues({ onset_low: hits, onset_mid: hits }, undefined, 0, 10,
                         {}, { onset_mid: false });
  ok(cues.filter(c => c.op === "strike").length === 4,
     "a muted band's strikes are absent from the audition");
  ok(cues.filter(c => c.op === "strike").every(c => c.detail === "onset_low"),
     "only the live band remains");
}
for (const [name, s] of Object.entries(BAND_STYLE_CLASSIC)) {
  ok(s.colors.length === 1 && !s.alternate && !s.pixelsByVel && !s.boostAt,
     `${name} classic baseline stays flat — it exists to lose fairly`);
}

if (fails.length) {
  console.error(`FAIL — ${fails.length} of ${pass + fails.length}:`);
  for (const f of fails) console.error("  ✗ " + f);
  process.exit(1);
}
console.log(`track lights: ${pass} assertions\nPASS`);
