/**
 * The audio ↔ transport contract, under node at fake-clock speed.
 *
 *     node test/transport_audio.mjs      (after `npm run test:desk` builds dist/)
 *
 * Everything the desk promises about sound, asserted against the shipped
 * modules rather than a retelling of them:
 *   - muted by default, every player, and mute is the element's own flag;
 *   - play() is the ONLY path that starts audio — loadScene, seek, syncTo,
 *     restart-while-stopped never do;
 *   - the rendered file starts LATENCY ms after the lights (the slider
 *     moves both, via the same number);
 *   - one sound source at a time — a row preview, the audition and the
 *     scene player hand over, never overlap;
 *   - a scene change while playing keeps playing; blackout stops everything.
 */

import {
  clock, installDom, media, sounding, running, el, fakeSynth, scene,
  makeAsserter,
} from "./desk_harness.mjs";

clock.install();
installDom();

const { RenderedAudio } = await import("../dist/audio.mjs");
const { Transport } = await import("../dist/transport.mjs");
const { createPreview } = await import("../dist/preview.mjs");
const { createState } = await import("../dist/show.mjs");

const { ok, done } = makeAsserter("transport/audio");

const A = scene({ id: "a", dur: 5000 });
const B = scene({ id: "b", dur: 8000, loop: true });
const SCENES = [A, B];
const AUDIO = { a: "data:audio/mpeg;base64,AAAA", b: "data:audio/mpeg;base64,BBBB" };

/* ── RenderedAudio alone ────────────────────────────────────────────── */
{
  const r = new RenderedAudio(AUDIO);
  ok(r.muted === true, "RenderedAudio starts muted");
  ok(media.every(a => a.muted), "every rendered element is constructed muted");
  ok(r.defaultMode === "rendered", "with files present the default mode is rendered");
  ok(new RenderedAudio({}).defaultMode === "synth", "with no files the default is the synth");
  ok(r.count === 2 && r.has("a") && !r.has("zzz"), "count/has report the files given");

  const elA = media.find(a => a.src === AUDIO.a);
  r.latency = 120;
  ok(r.play(A, 0) === true, "play() accepts a scene it has a file for");
  ok(r.play(scene({ id: "nope" })) === false, "play() refuses a scene it has no file for");
  ok(elA.paused && elA.log.length === 0, "nothing starts before the latency elapses");
  clock.advance(119);
  ok(elA.paused, "still silent one ms before the modelled latency");
  clock.advance(1);
  ok(!elA.paused && elA.log[0][0] === 120,
     `the file starts exactly LATENCY ms after play() (${elA.log[0]?.[0]})`);
  ok(elA.muted === true && sounding() === 0,
     "playing while muted runs the file without a sound");
  ok(elA.volume < 0.1, "the ramp starts from silence, not a step");
  clock.advance(400);
  const target = Math.min(1, r.volume * A.volume);
  ok(Math.abs(elA.volume - target) < 1e-6,
     `the fade lands on volume × scene volume (${elA.volume} vs ${target})`);
  ok(clock.pending() === 0, "the fade timer is cleared once it lands");

  // The latency slider moves the same number, so a second play waits longer.
  r.stopAll();
  ok(elA.paused && elA.currentTime === 0, "stopAll pauses and rewinds");
  r.latency = 30;
  const t1 = clock.now();
  r.play(A, 1500);
  clock.advance(29);
  ok(elA.paused, "(control) not yet at 29 ms");
  clock.advance(1);
  ok(!elA.paused && elA.log.at(-1)[0] === t1 + 30,
     "a lower latency starts the file sooner");
  ok(Math.abs(elA.currentTime - 1.5) < 1e-9, "play(from) seeks the element to fromMs");

  // Unmuting is the element's flag, applied to every file, and survives a
  // running fade (apply kills the ramp so it cannot undo the write).
  r.muted = false;
  r.apply(SCENES);
  ok(media.filter(a => a.src.startsWith("data:")).every(a => a.muted === false),
     "apply() writes muted=false onto every rendered element");
  ok(sounding() === 1, "unmuted while running: exactly one file is sounding");
  ok(clock.pending() === 0, "apply() cancels the in-flight fade");
  r.muted = true;
  r.apply(SCENES);
  ok(sounding() === 0, "muting again silences it without pausing it");
  ok(running() === 1, "…the file keeps running, muted");
  r.pauseAll();
  ok(running() === 0, "pauseAll pauses every file");
  r.seek("a", 2000);
  ok(Math.abs(elA.currentTime - 2) < 1e-9, "seek drags the element's position");

  // The bug this guards: a Stop (or a scene switch) inside the latency
  // window used to leave the start pending, and the file began AFTER the
  // Stop — two files at once on a quick scene change.
  r.play(A, 0);
  clock.advance(10);
  r.stopAll();
  clock.advance(100);
  ok(elA.paused, "Stop inside the latency window cancels the pending start");
  r.play(A, 0);
  clock.advance(10);
  r.pauseAll();
  clock.advance(100);
  ok(elA.paused, "Pause inside the latency window cancels the pending start too");
  const elB = media.find(a => a.src === AUDIO.b);
  r.play(A, 0);
  clock.advance(10);
  r.play(B, 0);
  clock.advance(100);
  ok(elA.paused && !elB.paused, "a second play() inside the window supersedes the first");
  r.stopAll();
}

/* ── Transport over RenderedAudio ───────────────────────────────────── */
media.length = 0;
{
  const rendered = new RenderedAudio(AUDIO);
  const synth = fakeSynth();
  const state = createState(A, clock.now());
  let mode = "rendered";
  let external = false;
  /** @type {{stopExternal: number, sceneChange: (string|null)[], blackout: number}} */
  const calls = { stopExternal: 0, sceneChange: [], blackout: 0 };
  const tr = new Transport({
    state, rendered, synth,
    getMode: () => mode,
    onSceneChange: (sc) => calls.sceneChange.push(sc.id),
    stopExternal: () => { calls.stopExternal++; external = false; },
    isExternalPlaying: () => external,
    onBlackout: () => calls.blackout++,
  });
  const label = () => el("playLabel").textContent;
  const elOf = (id) => media.find(a => a.src === AUDIO[id]);

  tr.loadScene(A);
  ok(!state.running && running() === 0, "loadScene does not play");
  ok(label() === "Play", "…and the button says Play");
  ok(calls.sceneChange.join() === "a", "loadScene tells the host which scene");
  ok(state.eff.towerL === "candle", "loadScene applies the base look");

  tr.seekTo(2000);
  ok(!state.running && state.held === 2000 && running() === 0,
     "seekTo while stopped moves the clock only — no audio");
  ok(Math.abs(elOf("a").currentTime - 2) < 1e-9, "seekTo drags the file's position while stopped");

  tr.restart();
  ok(!state.running && running() === 0 && state.held === 0,
     "restart while stopped stays stopped and silent");

  // play(): lights now, audio after LATENCY, button Pause, still muted.
  state.latency = 90; rendered.latency = 90;
  const t0 = clock.now();
  tr.play();
  ok(state.running && label() === "Pause", "play() runs the clock and says Pause");
  ok(synth.log.includes("arm"), "play() arms the synth (the gesture is the consent)");
  ok(running() === 0, "no file runs in the same tick as play()");
  clock.advance(90);
  ok(running() === 1 && elOf("a").log.at(-1)[0] === t0 + 90,
     "the file starts LATENCY ms after the lights did");
  ok(sounding() === 0 && rendered.muted,
     "play() does not unmute — muted-by-default survives Play");
  ok(el("hall").dispatched.includes("input") || !synth.armed,
     "once the synth is armed the sliders are replayed onto it");
  ok(Math.abs(tr.elapsed() - 90) < 1e-9, "elapsed() follows the clock while running");

  // Scene change while playing keeps playing — on the NEW file.
  tr.loadScene(B, { play: state.running });
  ok(state.running, "loading a scene while playing keeps playing");
  ok(elOf("a").paused, "…the old file stops");
  clock.advance(90);
  ok(!elOf("b").paused && elOf("b").loop === true, "…and the new file runs, looping as the scene says");
  ok(calls.sceneChange.at(-1) === "b", "the host is told about the new scene");
  ok(state.held === 0 && tr.elapsed() < 100, "the clock restarts at the head of the new scene");

  // Pause: clock holds, files pause, external players are silenced too.
  external = true;
  tr.toggle();
  ok(!state.running && label() === "Play", "toggle while running pauses");
  ok(running() === 0, "pause pauses the file");
  ok(calls.stopExternal === 1, "pause silences the external players too");
  ok(state.held > 0, "the held position is kept for resume");

  // Resume from held, not from zero.
  const held = state.held;
  tr.toggle();
  clock.advance(90);
  ok(state.running && Math.abs(elOf("b").currentTime - held / 1000) < 1e-6,
     "resume restarts the file where it was held");

  // A row preview sounding + Pause showing: toggle pauses THAT, not the scene.
  tr.blackout();
  external = true;
  tr.refreshUI();
  ok(label() === "Pause", "an external player shows as Pause on the transport");
  const stops = calls.stopExternal;
  tr.toggle();
  ok(calls.stopExternal === stops + 1 && !state.running && running() === 0,
     "toggle with a row preview sounding stops the preview and does not start the scene");
  external = false;
  tr.refreshUI();
  ok(label() === "Play", "…and the button goes back to Play");

  // Blackout: everything off, clock zero, host told (it mirrors to the castle).
  tr.play(); clock.advance(200);
  ok(running() === 1, "(control) playing again");
  tr.blackout();
  ok(!state.running && state.held === 0, "blackout stops the clock at zero");
  ok(running() === 0, "blackout stops every file");
  ok(["towerL", "towerR", "door"].every(z => state.eff[z] === "off"), "blackout turns every zone off");
  ok(synth.log.at(-1) === "stopWind" && synth.log.includes("newShowBus"),
     "blackout drops the synth bus and the wind");
  ok(calls.blackout === 2, "blackout tells the host every time (mirroring hook)");
  ok(calls.stopExternal > stops + 1, "blackout silences the external players");

  // Play after the scene ended starts from the top.
  state.held = B.dur;
  tr.play();
  ok(state.held === 0 || tr.elapsed() < 50, "play at the end rewinds to the head");
  tr.blackout();

  // syncTo: the clock runs, no audio starts (the audition owns the sound).
  tr.syncTo(1234);
  ok(state.running && label() === "Pause", "syncTo runs the clock");
  clock.advance(500);
  ok(running() === 0, "syncTo never starts a rendered file");
  ok(Math.abs(tr.elapsed() - 1734) < 1e-6, "syncTo sets the clock to the audition's position");
  state.fired.add(0);
  tr.syncTo(100);
  ok(state.fired.size === 0, "a rewind of the audition clears fired cues so they fire again");
  tr.blackout();

  // Synth mode: play() starts the wind, never a file.
  mode = "synth";
  tr.play();
  clock.advance(500);
  ok(synth.log.at(-1) === "startWind", "synth mode starts the wind");
  ok(running() === 0, "synth mode starts no rendered file");
  tr.blackout();
}

/* ── The row preview ────────────────────────────────────────────────── */
media.length = 0;
{
  /** @type {{change: (string|null)[], err: string[], claim: number}} */
  const ev = { change: [], err: [], claim: 0 };
  const p = createPreview({
    onChange: (id) => ev.change.push(id),
    onError: (m) => ev.err.push(m),
    onClaim: () => ev.claim++,
  });
  const a = media[0];
  ok(a?.muted === true && a.paused, "the preview element is built muted and stopped");
  ok(p.playing() === null, "nothing plays at first");

  p.toggle("one");
  ok(ev.claim === 1, "starting claims the speakers from everything else");
  ok(a.muted === false && !a.paused && a.src.endsWith("/api/track/one"),
     "toggle unmutes (the click is the consent) and plays the track's URL");
  ok(p.playing() === "one" && ev.change.at(-1) === "one", "the sounding id is reported");

  p.toggle("one");
  ok(a.paused && a.muted === true && p.playing() === null,
     "toggling the sounding track stops it and puts the mute back");
  ok(ev.change.at(-1) === null, "…and reports nothing playing");

  // The superseded-play race: the first play() rejects AFTER the second
  // click — the rejection must not stop or blame the second.
  a.playMode = "AbortError";
  p.toggle("slow");
  a.playMode = "ok";
  p.toggle("fast");
  await Promise.resolve(); await Promise.resolve();
  ok(p.playing() === "fast" && !a.paused, "a superseded play() cannot stop its successor");
  ok(ev.err.length === 0, "…and reports no error for the click that lost");

  // A real failure on the CURRENT track does report.
  p.stop();
  a.playMode = "NotAllowedError";
  p.toggle("blocked");
  await Promise.resolve(); await Promise.resolve();
  ok(p.playing() === null && /blocked/.test(ev.err.at(-1) ?? ""),
     "a play() the browser refuses stops and says which track");
  a.playMode = "ok";

  p.toggle("two");
  a.emit("ended");
  ok(p.playing() === null && a.muted === true, "ended stops and re-mutes");
  p.toggle("three");
  a.emit("error");
  ok(p.playing() === null && /three/.test(ev.err.at(-1)), "a load error names the track");
  a.emit("error");
  ok(ev.err.filter(m => /three/.test(m)).length === 1,
     "the teardown error (src empty) is not reported as a failure");
  p.destroy();
  ok(a.src === "", "destroy drops the source");
}

done();
