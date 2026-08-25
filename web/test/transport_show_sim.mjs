/**
 * A whole show, run through transport + show engine at fake-clock speed.
 *
 *     node test/transport_show_sim.mjs      (after `npm run test:desk` builds dist/)
 *
 * main.ts's frame loop is three lines: step() the engine, hand audio cues to
 * the synth after `state.latency`, stop the transport when a scene ends.
 * This replays that loop at 16 ms a frame against the manual clock and asserts
 * the things a listener would otherwise have to hear: cues fire on time and
 * once, lights lead audio by exactly the latency, a scene change mid-show
 * restarts the cue list for the new scene, and a finished scene hands the
 * transport back stopped. (show_sim.mjs, next door, drives the REAL scenes
 * through the engine alone; this one is about the transport around it.)
 */

import {
  clock, installDom, media, running, el, fakeSynth, scene, makeAsserter,
} from "./desk_harness.mjs";

clock.install();
installDom();

const { RenderedAudio } = await import("../dist/audio.mjs");
const { Transport } = await import("../dist/transport.mjs");
const { createState, step } = await import("../dist/show.mjs");
const { defaultParams } = await import("../dist/effects.mjs");

const { ok, done } = makeAsserter("transport show simulation");
const P = defaultParams();
const FRAME = 16;

const A = scene({ id: "a", dur: 4000, cues: [
  { t: 500,  bus: "AUD", op: "play", snd: "bell" },
  { t: 1000, bus: "LED", op: "set", zone: "towerL", eff: "eyes" },
  { t: 1500, bus: "LED", op: "strike", ms: 80, targets: ["door"] },
  { t: 2500, bus: "AUD", op: "play", snd: "thunder" },
]});
const B = scene({ id: "b", dur: 2000, loop: true, cues: [
  { t: 300, bus: "AUD", op: "play", snd: "tick" },
  { t: 900, bus: "LED", op: "set", zone: "door", eff: "blood" },
]});
const AUDIO = { a: "data:audio/mpeg;base64,AAAA", b: "data:audio/mpeg;base64,BBBB" };

const rendered = new RenderedAudio(AUDIO);
const synth = fakeSynth();
const state = createState(A, clock.now());
const ended = [];
const tr = new Transport({
  state, rendered, synth,
  getMode: () => "synth",          // per-cue synthesis is only meaningful here
  onSceneChange: () => {},
});

/** The cue log: {snd, at} per audio cue, `at` relative to the scene clock. */
const fired = [];
const synthPlays = [];
synth.play = (name) => synthPlays.push([clock.now(), name]);

/** One frame, exactly as main.ts wires it. */
function frame() {
  step(state, clock.now(), P,
    (snd) => {
      fired.push({ snd, at: clock.now() - state.t0 });
      // main.ts: window.setTimeout(() => synth.play(snd), state.latency)
      setTimeout(() => synth.play(snd), state.latency);
    },
    () => { ended.push(clock.now()); tr.setPlaying(false); });
}
function run(ms) {
  for (let t = 0; t < ms; t += FRAME) { clock.advance(FRAME); frame(); }
}

/* ── Stopped: frames fire nothing ──────────────────────────────────── */
tr.loadScene(A);
run(1000);
ok(fired.length === 0 && synthPlays.length === 0, "a stopped show fires no cues");
ok(state.eff.towerL === "candle", "…and the base look stands");

/* ── Play: cues on time, once, lights before audio by LATENCY ──────── */
state.latency = 70;
const t0 = clock.now();
tr.play();
run(4100);
ok(fired.map(f => f.snd).join() === "bell,thunder", `audio cues fire once each, in order (${fired.map(f => f.snd)})`);
for (const f of fired) {
  const due = A.cues.find(c => c.snd === f.snd).t;
  ok(f.at >= due && f.at < due + FRAME, `${f.snd} fires within a frame of ${due} (at ${f.at})`);
}
for (const [at, name] of synthPlays) {
  const cue = fired.find(f => f.snd === name);
  ok(at === t0 + cue.at + 70, `${name} sounds exactly LATENCY after its cue (${at - t0 - cue.at})`);
}
ok(synthPlays.length === 2, "every audio cue reached the synth");
ok(state.eff.towerL === "eyes", "the set cue changed the zone's effect");
ok(ended.length === 1 && !state.running && el("playLabel").textContent === "Play",
   "a finished scene stops the transport and the button says Play");
ok(state.held === A.dur, "the clock holds at the end");
ok(Math.abs(ended[0] - (t0 + A.dur)) <= FRAME, `the end lands within a frame of dur (${ended[0] - t0})`);
ok(running() === 0, "synth mode ran no rendered file");

/* ── The strike: door lit at 1500, decayed well before 2500 ─────────── */
{
  fired.length = 0; synthPlays.length = 0;
  tr.play();                         // from the head again
  run(1500 + FRAME * 2);
  ok(state.flash.door > 0.5, `the strike lands on the door (${state.flash.door})`);
  ok(state.flash.towerL === 0, "…and only on its targets");
  run(900);
  ok(state.flash.door < 0.1, `the strike has decayed by 2.4 s (${state.flash.door})`);
  ok(fired.map(f => f.snd).join() === "bell", "replaying from the head fires the head cues again");
  tr.blackout();
}

/* ── Scene change mid-show: the new scene's cues, from ITS zero ─────── */
{
  fired.length = 0; synthPlays.length = 0;
  tr.play();
  run(1200);                         // bell fired, eyes set
  ok(fired.length === 1 && state.eff.towerL === "eyes", "(control) A is a second in");
  const switchAt = clock.now();
  tr.loadScene(B, { play: state.running });
  ok(state.running, "the show keeps running across the switch");
  ok(state.fired.size === 0, "the fired set is fresh for the new scene");
  ok(state.eff.towerL === "candle", "the new scene's base look replaces the old cues' state");
  run(1000);
  const tick = fired.find(f => f.snd === "tick");
  ok(tick && tick.at >= 300 && tick.at < 300 + FRAME,
     `B's cue fires at B's own 300 ms, not A's clock (${tick?.at})`);
  ok(!fired.some(f => f.snd === "thunder"), "A's later cue never fires after the switch");
  ok(state.eff.door === "blood", "B's set cue landed");
  ok(synthPlays.at(-1)[0] === switchAt + tick.at + 70, "latency ordering holds for the new scene too");

  // Looping: the cue list re-arms every lap.
  run(2000);
  ok(fired.filter(f => f.snd === "tick").length === 2, "a looping scene fires its cues again each lap");
  ok(ended.length === 1, "a looping scene never ends the transport");
  tr.blackout();
}

/* ── Seek backwards while playing re-arms what it crossed ───────────── */
{
  fired.length = 0;
  tr.loadScene(A, { play: true });
  run(1100);
  ok(fired.length === 1, "(control) bell fired once");
  tr.seekTo(0);
  run(700);
  ok(fired.length === 2 && fired[1].snd === "bell", "seeking back over a cue fires it again");
  ok(state.eff.towerL === "candle", "seeking back restores the base look");
  tr.blackout();
}

/* ── The latency slider moves light→sound and light→file together ──── */
{
  const r2 = new Transport({
    state, rendered, synth, getMode: () => "rendered", onSceneChange: () => {} });
  r2.loadScene(A);
  for (const lat of [0, 40, 200]) {
    state.latency = lat; rendered.latency = lat;   // main.ts: the `lat` slider writes both
    fired.length = 0; synthPlays.length = 0;
    const start = clock.now();
    r2.play();
    // The element is born on play (G5) and reused across iterations, so
    // read the play that belongs to THIS start, not the log's first.
    const file = media.find(a => a.src === AUDIO.a);
    run(600);
    ok(file?.log.find(([t, op]) => op === "play" && t >= start)?.[0] === start + lat,
       `latency ${lat}: the rendered file starts ${lat} ms after play()`);
    r2.blackout();
  }
}

done();
