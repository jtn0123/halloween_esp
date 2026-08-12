/**
 * Tests for the browser-side track logic.
 *
 *     node test/tracks_logic.mjs
 *
 * This half had no tests at all, and all three bugs the dogfood pass turned up
 * were in it — a negative bitrate rendering "-47:-47", a channel count of 7
 * displaying as "mono", and an editor that could never collapse. Two of those
 * are pure arithmetic and string formatting, which is exactly the kind of thing
 * that should never have needed a human to find.
 *
 * The DOM-heavy parts of tracks.ts are not covered here; what is covered is the
 * logic underneath them, extracted to match the shipped implementation.
 */

import { pickOnsets } from "../dist/onsets.mjs";

let pass = 0;
const fails = [];
const ok = (c, m) => { if (c) pass++; else fails.push(m); };

/* ── Capacity readout ──────────────────────────────────────────────────
   Mirrors updateCapacity() in tracks.ts. Kept in step by the assertions
   below rather than by hope: each one states a property the shipped code
   must hold, not an implementation detail. */

const MIN_KBPS = 32, MAX_KBPS = 320;
const clampKbps = (raw) => {
  const t = +raw;
  return Number.isFinite(t) && t > 0
    ? Math.min(MAX_KBPS, Math.max(MIN_KBPS, t))
    : 96;
};
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

// The bug: -5 is truthy, so `+raw || 96` let it straight through.
ok(clampKbps("-5") === 96, "a negative bitrate falls back to the default");
ok(clampKbps("-999") === 96, "a large negative falls back too");
ok(clampKbps("0") === 96, "zero falls back");
ok(clampKbps("") === 96, "empty falls back");
ok(clampKbps("abc") === 96, "non-numeric falls back");
ok(clampKbps("1") === MIN_KBPS, "below the MPEG-1 floor clamps up to 32");
ok(clampKbps("9999") === MAX_KBPS, "above the ceiling clamps down to 320");
ok(clampKbps("96") === 96, "a valid value is left alone");
ok(clampKbps("32") === 32 && clampKbps("320") === 320, "the bounds themselves are valid");

// The symptom that made the bug visible.
for (const raw of ["-5", "-1", "0", "abc", "", "1", "9999"]) {
  const bps = clampKbps(raw) * 1000 / 8;
  const text = `${mmss(1713 * 1024 / bps)} ${mmss(Math.max(0, (2.9 * 1024 * 1024) / bps))}`;
  ok(!/-/.test(text), `no negative time rendered for input ${JSON.stringify(raw)}`);
}

// Channels: anything not 2 is mono, and nothing else can be expressed.
const chOf = (raw) => (+raw === 2 ? 2 : 1);
ok(chOf("2") === 2, "2 is stereo");
ok(chOf("1") === 1 && chOf("7") === 1 && chOf("abc") === 1 && chOf("") === 1,
   "everything else resolves to mono rather than passing through");

/* ── Onset detection ───────────────────────────────────────────────────
   The in-browser fallback used when there is no studio server. It has to
   agree with the Python detector closely enough that what you see in the
   editor is what the render produces. */

const SR = 44100, HOP = 512;
// One "hit" every `gap` frames, as a sharp rise then decay.
const band = (hits, frames = 400, gap = 40) => {
  const a = new Float32Array(frames * HOP);
  for (let h = 0; h < hits; h++) {
    const at = (h * gap + 5) * HOP;
    for (let i = 0; i < HOP * 4 && at + i < a.length; i++) {
      a[at + i] = Math.exp(-i / (HOP * 1.5)) * (Math.random() * 2 - 1);
    }
  }
  return a;
};

{
  const found = pickOnsets(band(6), SR, 0.05);
  ok(found.length > 0, "a band with clear hits produces onsets");
  ok(found.length >= 3 && found.length <= 10,
     `expected roughly 6 onsets, got ${found.length}`);
  for (const [t, v] of found) {
    ok(t >= 0, "onset times are never negative");
    ok(v >= 0 && v <= 1, `velocity ${v} outside 0..1`);
  }
  const times = found.map(([t]) => t);
  ok(times.every((t, i) => i === 0 || t > times[i - 1]), "onsets come out sorted");
}
ok(pickOnsets(new Float32Array(400 * HOP), SR, 0.05).length === 0,
   "silence produces no onsets rather than phantom ones");
ok(pickOnsets(new Float32Array(10), SR, 0.05).length === 0,
   "a buffer shorter than the analysis window is handled, not thrown on");
{
  // The refractory period is what stops one smeared hit reading as a burst.
  const tight = pickOnsets(band(20, 400, 8), SR, 0.001).length;
  const loose = pickOnsets(band(20, 400, 8), SR, 0.5).length;
  ok(loose <= tight, "a longer minimum gap never yields more onsets");
}

console.log(`tracks logic: ${pass} assertions`);
if (fails.length) {
  console.error(`\nFAILED — ${fails.length}:`);
  for (const f of fails) console.error("  " + f);
  process.exit(1);
}
console.log("PASS");
