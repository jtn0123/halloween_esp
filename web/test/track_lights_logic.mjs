/**
 * Tests for the cinematic track-light generator.
 *
 *     node test/track_lights_logic.mjs
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
  styleAsTs,
} from "../dist/track_lights.mjs";

let pass = 0;
const fails = [];
const ok = (c, m) => { if (c) pass++; else fails.push(m); };
const eq = (a, b, m) => ok(JSON.stringify(a) === JSON.stringify(b),
                           `${m} — got ${JSON.stringify(a)}`);

/* ── Colour blend: the numbers from TestPulseDynamicsParity ── */
const base = [1.0, 0.0, 0.0, 0.0], hot = [1.0, 0.5, 0.0, 0.4];
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
const hits = [[0.0, 1.0], [0.153, 0.55], [0.82, 0.91], [1.6, 0.4]];
{
  const s = bandStrikes("onset_low", hits, 0, 10);
  eq(s.map(c => c.t), [0, 153, 820, 1600], "times are clip-relative ms");
  eq(s[0].targets, ["door", "towerL", "towerR"],
     "a vel-1.0 low hit boosts onto both towers");
  eq(s[1].targets, ["door"], "a vel-0.55 low hit stays at the door");
  eq(s[2].targets, ["door", "towerL", "towerR"], "0.91 clears boost_at 0.85");
  ok(s[0].pixels === "all" && s[1].pixels === "scatter" && s[3].pixels === "scatter",
     "low band masks follow velocity");
  ok(s.every(c => c.op === "strike" && c.bus === "LED"), "strikes are LED strikes");
  ok(Math.abs(s[1].intensity - BAND_STYLE.onset_low.intensity * 0.55) < 0.001,
     "intensity scales by velocity");
}
{
  const s = bandStrikes("onset_mid", hits, 0, 10);
  eq(s.map(c => c.targets[0]), ["towerL", "towerR", "towerL", "towerR"],
     "mid alternates between the towers");
}
{
  const s = bandStrikes("onset_high", hits, 0, 10);
  ok(s.every(c => c.pixels === "scatter"), "highs always scatter");
  eq(s.map(c => c.targets[0]), ["towerR", "door", "towerL", "towerR"],
     "highs walk towerR→door→towerL");
}
{
  const s = bandStrikes("onset_mid", hits, 0, 10, "door");
  ok(s.every(c => c.targets.length === 1 && c.targets[0] === "door"),
     "pinning a band in the editor overrides the movement");
}
{
  const s = bandStrikes("onset_low", hits, 0.5, 1.0);
  eq(s.map(c => c.t), [320], "hits outside the clip are dropped, times re-based");
}

/* ── Sections ── */
{
  // 0-10s quiet, 10-20s loud, 20-30s quiet — a chorus with verses around it.
  const env = [];
  for (let t = 0; t <= 30; t += 0.25) {
    env.push([t, t >= 10 && t < 20 ? 0.9 : 0.1]);
  }
  const secs = sections(env, 0, 30);
  eq(secs.map(([, tier]) => tier), [0, 2, 0],
     "quiet→loud→quiet becomes three sections");
  ok(Math.abs(secs[1][0] - 10) < 2, "the chorus boundary lands near 10s");
  const cues = sectionCues(env, 0, 30);
  ok(cues.length === 9, "three sections × three zones");
  ok(cues.every(c => c.op === "set"), "sections are set cues");
  const loud = cues.filter(c => c.detail === "chorus");
  ok(loud.some(c => c.zone === "towerL" && c.eff === TIERS[2].towers),
     "the chorus look reaches the towers");
}
eq(sections(undefined, 0, 30), [[0, 1]], "no envelope: hold the middle tier");
eq(sections([], 0, 30), [[0, 1]], "empty envelope: hold the middle tier");
{
  // A level flickering across a boundary must not flap the whole castle.
  const env = [];
  for (let t = 0; t <= 30; t += 0.25) env.push([t, 0.40 + (t % 0.5 < 0.25 ? 0.04 : -0.04)]);
  const secs = sections(env, 0, 30);
  ok(secs.length <= 2, `hysteresis holds a hovering level steady (got ${secs.length})`);
}

/* ── The full expansion ── */
{
  const cues = trackCues({ onset_low: hits, onset_mid: hits }, undefined, 0, 10);
  ok(cues.every((c, i) => i === 0 || cues[i - 1].t <= c.t), "cues come out sorted");
  ok(cues.some(c => c.op === "set"), "sections are present even without an envelope");
  ok(cues.filter(c => c.op === "strike").length === 8, "both bands' strikes made it");
}

/* ── Per-hit colour cycling: consecutive hits differ in hue ── */
{
  const soft = [[0.0, 0.3], [0.5, 0.3], [1.0, 0.3], [1.5, 0.3]];
  const s = bandStrikes("onset_mid", soft, 0, 10);
  const keys = s.map(c => c.color.join(","));
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

/* ── The style lab: variant, tweaks, and the export's honesty ── */
{
  ok(styleVariant() === "current", "the lab starts on the current engine");
  setStyleVariant("classic");
  const s = bandStrikes("onset_mid", hits, 0, 10);
  ok(s.every(c => c.targets.length === 1 && c.targets[0] === "towerL"),
     "classic mid stays parked on towerL — no movement");
  ok(s.every(c => c.pixels === undefined), "classic strikes hit the whole jewel");
  const keys = new Set(s.map(c => c.color.join(",")));
  ok(keys.size === 1, "classic is ONE flat colour — velocity moves intensity only");
  const env = [];
  for (let t = 0; t <= 30; t += 0.25) env.push([t, t >= 10 && t < 20 ? 0.9 : 0.1]);
  ok(sectionCues(env, 0, 30).length === 3,
     "classic holds ONE look — sections do not fire in B");
  // The cardinal rule: the export never ships the comparison baseline.
  ok(styleFor("onset_mid", true).colors.length
       === BAND_STYLE.onset_mid.colors.length,
     "styleFor(forExport) ignores the B variant");
  setStyleVariant("current");
  ok(sectionCues(env, 0, 30).length === 9, "back on A, sections fire again");
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
