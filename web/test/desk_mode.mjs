/**
 * The desk's mode machine (desk_mode.ts), driven through every rule it has.
 *
 *     node test/desk_mode.mjs
 *
 * These used to be six flags and four callbacks in main.ts, and the only
 * check on them was clicking around. The rules worth pinning: an audition
 * displaces the loaded scene and puts it back on stop; a clip change while
 * auditioning re-loads without stopping; starting with no clip scene does
 * nothing; adopting is a phase the pick handler can see and nothing else.
 */

import { auditioning, initialMode, transition } from "../dist/desk_mode.mjs";

let pass = 0;
const fails = [];
const ok = (c, m) => { if (c) pass++; else fails.push(m); };

const sc = (id) => ({ id, name: id, kind: "t", dur: 1000, loop: false, volume: 1,
                      blurb: "", base: {}, levels: {}, cues: [], file: "", bytes: 0, yaml: "" });
const SHOW = sc("show"), CLIP = sc("clip"), CLIP2 = sc("clip2");
const run = (m, ...events) => {
  const loads = [];
  for (const e of events) {
    const t = transition(m, e);
    m = t.mode;
    if (t.load) loads.push(t.load.id);
  }
  return { m, loads };
};

// ── initial ──
const m0 = initialMode("rendered");
ok(m0.source === "rendered" && m0.phase.kind === "show" && m0.preview === null
   && m0.track === null && m0.players === null, "initial mode is show/no clip/no players");
ok(!auditioning(m0), "not auditioning at start");

// ── source / ready / select: plain records, no loads ──
{
  const { m, loads } = run(m0, { type: "source", source: "synth" },
                           { type: "ready", players: { p: 1 } },
                           { type: "select", track: "song" });
  ok(m.source === "synth" && m.players.p === 1 && m.track === "song" && loads.length === 0,
     "source/ready/select update their field and load nothing");
  ok(run(m, { type: "select", track: null }).m.track === null, "deselect clears the track");
}

// ── audition: displace and restore ──
{
  const { m, loads } = run(m0, { type: "clip", preview: CLIP },
                           { type: "audition-start", current: SHOW });
  ok(m.phase.kind === "audition" && m.phase.before === SHOW, "audition remembers the displaced scene");
  ok(auditioning(m), "auditioning() reads the phase");
  ok(loads.join() === "clip", "starting the audition loads the clip's scene");
  // Start again while running: no second displacement, nothing loaded.
  const again = run(m, { type: "audition-start", current: CLIP });
  ok(again.m.phase.before === SHOW && again.loads.length === 0,
     "a second start keeps the original before and loads nothing");
  // Clip changes mid-audition re-load without stopping.
  const re = run(m, { type: "clip", preview: CLIP2 });
  ok(re.m.phase.kind === "audition" && re.loads.join() === "clip2",
     "a clip change while auditioning loads the new preview");
  // Clip gone mid-audition: stays auditioning, loads nothing.
  const gone = run(m, { type: "clip", preview: null });
  ok(gone.m.phase.kind === "audition" && gone.m.preview === null && gone.loads.length === 0,
     "a vanished clip mid-audition loads nothing");
  // Stop restores.
  const stop = run(m, { type: "audition-stop" });
  ok(stop.m.phase.kind === "show" && stop.loads.join() === "show",
     "stopping loads the displaced scene back");
  ok(stop.m.preview === CLIP, "the preview survives the stop (next start re-uses it)");
}

// ── audition with nothing to show ──
{
  const { m, loads } = run(m0, { type: "audition-start", current: SHOW });
  ok(m.phase.kind === "show" && loads.length === 0, "no clip scene: the show's lights stay");
  const s = run(m, { type: "audition-stop" });
  ok(s.m.phase.kind === "show" && s.loads.length === 0, "stop with nothing displaced loads nothing");
}
// A clip change when NOT auditioning never loads.
ok(run(m0, { type: "clip", preview: CLIP }).loads.length === 0,
   "a clip change outside an audition loads nothing");

// ── adopting ──
{
  const a = run(m0, { type: "adopt-start" });
  ok(a.m.phase.kind === "adopting" && !auditioning(a.m), "adopt-start enters the adopting phase");
  const b = run(a.m, { type: "adopt-end" });
  ok(b.m.phase.kind === "show" && a.loads.length + b.loads.length === 0,
     "adopt-end returns to show; neither loads");
}

// ── immutability: transition never edits its input ──
{
  const frozen = Object.freeze({ ...m0, phase: Object.freeze({ kind: "show" }) });
  let threw = false;
  try { run(frozen, { type: "clip", preview: CLIP }, { type: "audition-start", current: SHOW },
            { type: "audition-stop" }); } catch { threw = true; }
  ok(!threw, "transition copies rather than mutates");
}

console.log(`desk_mode: ${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log("  FAIL:", f);
process.exit(fails.length ? 1 : 0);
