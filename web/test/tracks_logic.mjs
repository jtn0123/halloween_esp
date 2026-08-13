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
 * logic underneath them — which now lives in import_opts.ts and is imported
 * directly, so these assertions test the shipped code rather than a copy of it
 * that has to be kept in step by hand.
 */

import { pickOnsets } from "../dist/onsets.mjs";
import {
  MIN_KBPS, MAX_KBPS, clampKbps, channelsOf, capacityHtml, optsHint,
} from "../dist/import_opts.mjs";

let pass = 0;
const fails = [];
const ok = (c, m) => { if (c) pass++; else fails.push(m); };

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

// The symptom that made the bug visible, asserted against the real readout.
for (const raw of ["-5", "-1", "0", "abc", "", "1", "9999"]) {
  const html = capacityHtml(raw, "1", 0);
  ok(!/>-|-\d+:/.test(html),
     `no negative time in the capacity line for bitrate ${JSON.stringify(raw)}`);
}
// The readout names the two builds that exist, and must not claim a third.
// It used to end with "streamed from SD: no limit" in green — a promise the
// This guard has flipped sides once: it used to forbid the word "stream"
// because micro-decoder 0.2.0 had no pull API and the readout was making a
// promise the stack could not keep. Then the loopback stream shipped
// (firmware/sd_web_site.h, verified on the device), and the same guard now
// requires the claim it once banned. The invariant underneath is constant:
// the readout must match what the hardware actually does TODAY.
ok(/flash build/.test(capacityHtml("96", "1", 0)), "the flash build's ceiling is named");
ok(/SD build/.test(capacityHtml("96", "1", 0)), "the SD build is named");
ok(/stream/i.test(capacityHtml("96", "1", 0)),
   "the SD build streams now — the readout must say so");
ok(!/under 4 min|one track:/.test(capacityHtml("96", "1", 0)),
   "and the dead PSRAM per-track ceiling must not resurface");
ok(/stereo/.test(capacityHtml("96", "2", 0)), "channels reach the readout");
// More of the show on the flash means less room left for the next track.
{
  const room = (used) => capacityHtml("96", "1", used).match(/show: <b>(\d+):(\d+)</);
  const empty = room(0), full = room(2.5 * 1024 * 1024);
  ok(+empty[1] * 60 + +empty[2] > +full[1] * 60 + +full[2],
     "a fuller show leaves less flash for the next import");
}
ok(/show: <b>0:00</.test(capacityHtml("96", "1", 99 * 1024 * 1024)),
   "an over-full show reads as 0:00, never as negative time");

// Channels: anything not 2 is mono, and nothing else can be expressed.
ok(channelsOf("2") === 2, "2 is stereo");
ok(channelsOf("1") === 1 && channelsOf("7") === 1
   && channelsOf("abc") === 1 && channelsOf("") === 1,
   "everything else resolves to mono rather than passing through");

/* ── The collapsed Options summary ─────────────────────────────────────
   It is the only thing telling you what an import will do before you run
   it, so a wrong summary is worse than none. */

const hint = (o) => optsHint({
  id: "", start: "", take: "", sensitivity: "", bitrate: "96",
  sample_rate: "44100", channels: "1", format: "mp3", normalize: false, ...o,
});
ok(hint({}) === "— MP3 96k", "the default summary is the format and its bitrate");
ok(hint({ take: "24", start: "0:30" }) === "— 24s from 0:30, MP3 96k",
   "a trim is summarised as length and offset");
ok(hint({ take: "24", start: "0:00" }) === "— 24s, MP3 96k",
   "a start of zero is not worth saying");
ok(hint({ take: "24" }) === "— 24s, MP3 96k", "nor is a blank start");
for (const fmt of ["wav", "flac"]) {
  ok(hint({ format: fmt }) === `— ${fmt.toUpperCase()}`,
     `${fmt} shows no bitrate, because it has none`);
}
ok(hint({ format: "opus" }) === "— OPUS 96k", "opus is lossy, so its bitrate shows");
ok(hint({ bitrate: "-5" }) === "— MP3 96k",
   "an out-of-range bitrate is summarised as what will actually be used");
ok(hint({ channels: "2", normalize: true }) === "— MP3 96k, stereo, loudness matched",
   "stereo and loudness matching are both called out");
ok(hint({ channels: "7" }) === "— MP3 96k",
   "a channel count that cannot be expressed is not claimed to be stereo");

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
