/**
 * A fake browser just big enough for the desk's audio and transport modules.
 *
 * transport.ts, audio.ts and preview.ts are the seams where "what the clock
 * says" meets "what the speakers do". They touch three browser things —
 * `document` (a handful of elements by id), `Audio` (detached media
 * elements) and time (`performance.now`, `setTimeout`, `setInterval`) — and
 * nothing else. Faking those three here makes the whole audio↔light contract
 * testable under node at any speed, which is how a "does the rendered file
 * start LATENCY ms after the lights" question becomes an assertion instead
 * of a listen.
 *
 * Time is manual: nothing happens until `clock.advance(ms)`, and every
 * timer due inside that window fires in order, with `performance.now()`
 * reading the timer's own due time while it runs — the same ordering a
 * real event loop gives a single-threaded page.
 */

/* ── Manual clock ───────────────────────────────────────────────────── */
export const clock = (() => {
  let now = 0;
  let seq = 0;
  const timers = new Map();
  const add = (fn, ms, every) => {
    const id = ++seq;
    timers.set(id, { fn, due: now + Math.max(0, Number(ms) || 0), every, id });
    return id;
  };
  return {
    now: () => now,
    install() {
      globalThis.performance = { now: () => now };
      globalThis.setTimeout = (fn, ms) => add(fn, ms, null);
      globalThis.setInterval = (fn, ms) => add(fn, ms, Math.max(1, Number(ms) || 1));
      globalThis.clearTimeout = (id) => { timers.delete(id); };
      globalThis.clearInterval = (id) => { timers.delete(id); };
    },
    /** Run everything due in the next `ms`, in due order, then land at the end. */
    advance(ms) {
      const end = now + ms;
      for (;;) {
        let next = null;
        for (const t of timers.values()) {
          if (t.due <= end && (next === null || t.due < next.due
              || (t.due === next.due && t.id < next.id))) next = t;
        }
        if (!next) break;
        now = next.due;
        if (next.every) next.due = now + next.every; else timers.delete(next.id);
        next.fn();
      }
      now = end;
    },
    pending: () => timers.size,
  };
})();

/* ── Media elements ─────────────────────────────────────────────────── */
/** Every fake Audio ever constructed, so a test can ask "what is sounding". */
export const media = [];

export class FakeAudio {
  constructor(src = "") {
    this.src = src;
    this.muted = false;
    this.paused = true;
    this.volume = 1;
    this.loop = false;
    this.currentTime = 0;
    this.preload = "";
    /** What a play() promise does: "ok" resolves, anything else rejects. */
    this.playMode = "ok";
    /** Timeline of interesting calls: [ms, "play"|"pause"|…]. */
    this.log = [];
    this.listeners = new Map();
    media.push(this);
  }
  play() {
    this.log.push([clock.now(), "play"]);
    if (this.playMode === "ok") { this.paused = false; return Promise.resolve(); }
    return Promise.reject(new Error(this.playMode));
  }
  pause() { this.log.push([clock.now(), "pause"]); this.paused = true; }
  load() { /* the real element re-fetches src; this fake reads it directly */ }
  removeAttribute(n) { if (n === "src") this.src = ""; }
  addEventListener(ev, fn) {
    if (!this.listeners.has(ev)) this.listeners.set(ev, []);
    this.listeners.get(ev).push(fn);
  }
  emit(ev) { for (const fn of this.listeners.get(ev) ?? []) fn(); }
}

/** How many fake players are running and audible. */
export const sounding = () => media.filter(a => !a.paused && !a.muted).length;
export const running = () => media.filter(a => !a.paused).length;

/* ── Document ───────────────────────────────────────────────────────── */
class FakeEl {
  constructor(id) {
    this.id = id;
    this.innerHTML = "";
    this.textContent = "";
    this.attrs = new Map();
    this.handlers = new Map();
    this.dispatched = [];
  }
  setAttribute(k, v) { this.attrs.set(k, String(v)); }
  getAttribute(k) { return this.attrs.get(k) ?? null; }
  addEventListener(ev, fn) {
    if (!this.handlers.has(ev)) this.handlers.set(ev, []);
    this.handlers.get(ev).push(fn);
  }
  dispatchEvent(e) {
    this.dispatched.push(e.type);
    for (const fn of this.handlers.get(e.type) ?? []) fn(e);
    return true;
  }
}

export const els = new Map();
export const el = (id) => {
  if (!els.has(id)) els.set(id, new FakeEl(id));
  return els.get(id);
};

export function installDom() {
  globalThis.Event = class { constructor(type) { this.type = type; } };
  globalThis.document = {
    getElementById: (id) => el(id),
    addEventListener: () => {},
    querySelector: () => null,
    createElement: () => new FakeEl(""),
  };
  globalThis.window = globalThis;
  globalThis.Audio = FakeAudio;
  globalThis.localStorage = {
    store: new Map(),
    getItem(k) { return this.store.has(k) ? this.store.get(k) : null; },
    setItem(k, v) { this.store.set(k, String(v)); },
  };
}

/* ── The synth, as the transport sees it ───────────────────────────── */
/** A stand-in Synth: records the calls the transport is allowed to make. */
export function fakeSynth() {
  const s = {
    armed: false, muted: true, log: [],
    arm() { this.armed = true; this.log.push("arm"); },
    newShowBus() { this.log.push("newShowBus"); },
    startWind() { this.log.push("startWind"); },
    stopWind() { this.log.push("stopWind"); },
    setMuted(m) { this.muted = m; },
    play(name) { this.log.push(`play:${name}`); },
  };
  return s;
}

/* ── Scenes ────────────────────────────────────────────────────────── */
/** A scene shaped like gen_previewer.py's output. */
export const scene = (over = {}) => ({
  id: "t", name: "T", kind: "test", dur: 10000, loop: false, volume: 0.8,
  blurb: "", base: { towerL: "candle", towerR: "off", door: "off" },
  levels: {}, cues: [], file: "t.mp3", yaml: "", ...over,
});

/* ── Plain assertions, matching the other node tests ───────────────── */
export function makeAsserter(label) {
  let pass = 0;
  const fails = [];
  const ok = (c, m) => { if (c) pass++; else fails.push(m); };
  const done = () => {
    console.log(`${label}: ${pass} assertions`);
    if (fails.length) {
      console.error(`\nFAILED — ${fails.length}:`);
      for (const f of fails) console.error("  " + f);
      process.exit(1);
    }
    console.log("PASS");
  };
  return { ok, done };
}
