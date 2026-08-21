/**
 * Fuzz for the card-reconciliation arithmetic: cardName / sendable /
 * cardState (track_send.ts) and syncPlan / cardOnly (track_card.ts).
 *
 *     node test/tracks_fuzz.mjs      (after `npm run test:desk` builds dist/)
 *
 * These five functions decide what the library says about the castle's card
 * and what Sync will PUT over it. The e2e suite covers the handful of shapes
 * a person thinks of; this throws a few thousand random ones at them —
 * missing fields, odd extensions, zero and negative sizes, names that only
 * differ by case — and checks the invariants that must hold for every one:
 *
 *   - nothing throws;
 *   - stale ⇔ the card holds the name with a DIFFERENT byte count;
 *   - absent ⇔ the card lacks the name;
 *   - a track whose import failed is never sendable, never badged, never in
 *     the plan (it would PUT nothing over a good copy — pass 1, J1-2);
 *   - Sync never includes a current copy, never a track outside the show,
 *     and is empty without a castle;
 *   - card-only rows are exactly the audio files no local track maps onto.
 */

import { cardName, cardState, sendable } from "../dist/track_send.mjs";
import { cardOnly, syncPlan } from "../dist/track_card.mjs";

let pass = 0;
const fails = [];
const ok = (c, m) => { if (c) pass++; else if (fails.length < 40) fails.push(m); };

/* Deterministic PRNG so a failure reproduces. */
let seed = 0x5eed;
const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
const pick = (xs) => xs[Math.floor(rnd() * xs.length)];
const maybe = (p, v) => (rnd() < p ? v : undefined);

const IDS = ["vigil", "Storm", "seance_2", "e2e_beats", "x", "phantom-waltz",
             "ghost.busters", "ÄLIEN", "", "01_vigil"];
const EXTS = ["mp3", "MP3", "wav", "flac", "opus", "Mp3", "", undefined, "ogg"];

function track() {
  const id = pick(IDS);
  const ext = pick(EXTS);
  const t = { id, kb: Math.floor(rnd() * 4000) };
  if (ext !== undefined) t.ext = ext;
  const fmt = maybe(0.4, pick(EXTS));
  if (fmt !== undefined) t.opts = { format: fmt };
  const bytes = pick([undefined, 0, -1, 1, 287744, 985088, Math.floor(rnd() * 2e6)]);
  if (bytes !== undefined) t.bytes = bytes;
  if (rnd() < 0.15) t.error = "ffmpeg could not convert it";
  return t;
}

/** A card: some names derived from real tracks (same or different bytes),
 *  some strangers, some directories' worth of non-audio. */
function card(tracks) {
  if (rnd() < 0.15) return null;
  const m = new Map();
  for (const t of tracks) {
    if (rnd() < 0.6) {
      const name = cardName(t);
      const same = rnd() < 0.5 && typeof t.bytes === "number";
      m.set(name, same ? t.bytes : Math.floor(rnd() * 1e6));
    }
  }
  for (let i = Math.floor(rnd() * 4); i > 0; i--) {
    m.set(pick(["phantom_waltz.mp3", "logs", "notes.txt", "LOUD.WAV", "a.flac",
                "b.opus", "c.m4a", ".hidden.mp3", "ghost.busters.mp3"]),
          Math.floor(rnd() * 1e6));
  }
  return m;
}

const ROUNDS = 3000;
for (let round = 0; round < ROUNDS; round++) {
  const tracks = Array.from({ length: Math.floor(rnd() * 6) }, track);
  const c = card(tracks);
  const inShow = new Set(tracks.filter(() => rnd() < 0.5).map(t => t.id));
  const ctx = { tracks, sceneIds: inShow, card: c, canPull: rnd() < 0.5 };

  let plan, only;
  try {
    plan = syncPlan(ctx);
    only = cardOnly(ctx);
    for (const t of tracks) { cardName(t); sendable(t); cardState(t, c); }
  } catch (e) {
    ok(false, `round ${round}: threw ${e}`);
    continue;
  }

  for (const t of tracks) {
    const name = cardName(t);
    const ext = (t.ext || t.opts?.format || "mp3").toLowerCase();
    ok(name === `${t.id}.${ext}`, `cardName is id.ext lowercased (${name})`);
    const s = sendable(t);
    ok(s === (!t.error && (t.bytes === undefined || t.bytes > 0)),
       `sendable ⇔ no error and bytes undefined-or-positive (${JSON.stringify(t)})`);
    const st = cardState(t, c);
    if (c === null) ok(st === null, "no castle → no verdict");
    else if (!s) ok(st === null, "an unsendable track gets no badge at all");
    else if (!c.has(name)) ok(st === "absent", "absent ⇔ not on the card");
    else if (t.bytes === undefined) ok(st === "current", "no local byte count → presence is enough");
    else ok(st === (c.get(name) === t.bytes ? "current" : "stale"),
            `stale ⇔ sizes differ (${c.get(name)} vs ${t.bytes} → ${st})`);
  }

  // The plan.
  if (c === null) ok(plan.length === 0, "no castle → nothing to sync");
  for (const t of plan) {
    ok(inShow.has(t.id), `sync only sends the show (${t.id})`);
    ok(sendable(t), `sync never sends a broken import (${t.id})`);
    ok(cardState(t, c) !== "current", `sync never re-sends a current copy (${t.id})`);
  }
  for (const t of tracks) {
    const should = c !== null && inShow.has(t.id) && sendable(t)
      && cardState(t, c) !== "current";
    ok(plan.includes(t) === should,
       `plan membership for ${t.id}: ${plan.includes(t)} vs expected ${should}`);
  }

  // Card-only rows.
  if (c === null) ok(only.length === 0, "no castle → no card-only rows");
  else {
    const local = new Set(tracks.map(cardName));
    for (const f of only) {
      ok(/\.(mp3|wav|flac|opus)$/i.test(f.name), `card-only rows are audio (${f.name})`);
      ok(!local.has(f.name), `card-only rows have no local twin (${f.name})`);
      ok(c.get(f.name) === f.size, "card-only rows carry the card's byte count");
    }
    const expected = [...c.keys()].filter(n => /\.(mp3|wav|flac|opus)$/i.test(n) && !local.has(n));
    ok(only.length === expected.length, `every stranger audio file gets a row (${only.length} vs ${expected.length})`);
    ok(only.every((f, i) => i === 0 || only[i - 1].name.localeCompare(f.name) <= 0),
       "card-only rows come out sorted");
  }
}

// A couple of fixed shapes worth naming outright.
ok(cardName({ id: "a", kb: 1 }) === "a.mp3", "no ext, no opts → .mp3");
ok(cardName({ id: "a", kb: 1, ext: "WAV" }) === "a.wav", "ext is lowercased");
ok(cardName({ id: "a", kb: 1, opts: { format: "flac" } }) === "a.flac", "opts.format stands in for ext");
ok(cardState({ id: "a", kb: 1, bytes: 0 }, new Map([["a.mp3", 0]])) === null,
   "a 0-byte file is not 'current' even if the card has 0 bytes under its name");
ok(cardState({ id: "a", kb: 1, bytes: 5 }, new Map([["A.mp3", 5]])) === "absent",
   "names are case-sensitive — A.mp3 is not a.mp3 on the card");

console.log(`tracks fuzz: ${pass} assertions over ${ROUNDS} rounds`);
if (fails.length) {
  console.error(`\nFAILED — ${fails.length}+:`);
  for (const f of fails) console.error("  " + f);
  process.exit(1);
}
console.log("PASS");
