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

import type { Scene } from "../src/types.js";

/* The whole point of this module is to impersonate the browser, so it
 * writes fakes onto globalThis through one loosely-typed handle. */
const g = globalThis as unknown as Record<string, unknown>;

/* ── Manual clock ───────────────────────────────────────────────────── */
type TimerFn = () => void;
interface Timer { fn: TimerFn; due: number; every: number | null; id: number }

export const clock = (() => {
  let now = 0;
  let seq = 0;
  const timers = new Map<number, Timer>();
  const add = (fn: TimerFn, ms: number | undefined, every: number | null): number => {
    const id = ++seq;
    timers.set(id, { fn, due: now + Math.max(0, Number(ms) || 0), every, id });
    return id;
  };
  return {
    now: (): number => now,
    install(): void {
      g.performance = { now: () => now };
      g.setTimeout = (fn: TimerFn, ms?: number) => add(fn, ms, null);
      g.setInterval = (fn: TimerFn, ms?: number) => add(fn, ms, Math.max(1, Number(ms) || 1));
      g.clearTimeout = (id: number) => { timers.delete(id); };
      g.clearInterval = (id: number) => { timers.delete(id); };
    },
    /** Run everything due in the next `ms`, in due order, then land at the end. */
    advance(ms: number): void {
      const end = now + ms;
      for (;;) {
        let next: Timer | null = null;
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
    pending: (): number => timers.size,
  };
})();

/* ── Media elements ─────────────────────────────────────────────────── */
/** Every fake Audio ever constructed, so a test can ask "what is sounding". */
export const media: FakeAudio[] = [];

export class FakeAudio {
  src: string;
  muted = false;
  paused = true;
  volume = 1;
  loop = false;
  currentTime = 0;
  preload = "";
  /** What a play() promise does: "ok" resolves, anything else rejects. */
  playMode = "ok";
  /** Timeline of interesting calls: [ms, "play"|"pause"|…]. */
  log: [number, string][] = [];
  listeners = new Map<string, TimerFn[]>();

  constructor(src = "") {
    this.src = src;
    media.push(this);
  }
  play(): Promise<void> {
    this.log.push([clock.now(), "play"]);
    if (this.playMode === "ok") { this.paused = false; return Promise.resolve(); }
    return Promise.reject(new Error(this.playMode));
  }
  pause(): void { this.log.push([clock.now(), "pause"]); this.paused = true; }
  load(): void { /* the real element re-fetches src; this fake reads it directly */ }
  removeAttribute(n: string): void { if (n === "src") this.src = ""; }
  addEventListener(ev: string, fn: TimerFn): void {
    if (!this.listeners.has(ev)) this.listeners.set(ev, []);
    this.listeners.get(ev)!.push(fn);
  }
  emit(ev: string): void { for (const fn of this.listeners.get(ev) ?? []) fn(); }
}

/** How many fake players are running and audible. */
export const sounding = (): number => media.filter(a => !a.paused && !a.muted).length;
export const running = (): number => media.filter(a => !a.paused).length;

/* ── Document ───────────────────────────────────────────────────────── */
interface FakeEvent { type: string }

export class FakeEl {
  id: string;
  innerHTML = "";
  textContent = "";
  attrs = new Map<string, string>();
  handlers = new Map<string, ((e: FakeEvent) => void)[]>();
  dispatched: string[] = [];

  constructor(id: string) { this.id = id; }
  setAttribute(k: string, v: unknown): void { this.attrs.set(k, String(v)); }
  getAttribute(k: string): string | null { return this.attrs.get(k) ?? null; }
  addEventListener(ev: string, fn: (e: FakeEvent) => void): void {
    if (!this.handlers.has(ev)) this.handlers.set(ev, []);
    this.handlers.get(ev)!.push(fn);
  }
  dispatchEvent(e: FakeEvent): boolean {
    this.dispatched.push(e.type);
    for (const fn of this.handlers.get(e.type) ?? []) fn(e);
    return true;
  }
}

export const els = new Map<string, FakeEl>();
export const el = (id: string): FakeEl => {
  if (!els.has(id)) els.set(id, new FakeEl(id));
  return els.get(id)!;
};

export function installDom(): void {
  g.Event = class { type: string; constructor(type: string) { this.type = type; } };
  g.document = {
    getElementById: (id: string) => el(id),
    addEventListener: () => {},
    querySelector: () => null,
    createElement: () => new FakeEl(""),
  };
  g.window = globalThis;
  g.Audio = FakeAudio;
  const store = new Map<string, string>();
  g.localStorage = {
    store,
    getItem: (k: string): string | null => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: unknown): void => { store.set(k, String(v)); },
  };
}

/* ── The synth, as the transport sees it ───────────────────────────── */
export interface FakeSynth {
  armed: boolean;
  muted: boolean;
  log: string[];
  arm(): void;
  newShowBus(): void;
  startWind(): void;
  stopWind(): void;
  setMuted(m: boolean): void;
  play(name: string): void;
}

/** A stand-in Synth: records the calls the transport is allowed to make. */
export function fakeSynth(): FakeSynth {
  const s: FakeSynth = {
    armed: false, muted: true, log: [],
    arm() { this.armed = true; this.log.push("arm"); },
    newShowBus() { this.log.push("newShowBus"); },
    startWind() { this.log.push("startWind"); },
    stopWind() { this.log.push("stopWind"); },
    setMuted(m: boolean) { this.muted = m; },
    play(name: string) { this.log.push(`play:${name}`); },
  };
  return s;
}

/* ── Scenes ────────────────────────────────────────────────────────── */
/** A scene shaped like gen_previewer.py's output. The tests hand in loose
 *  cue literals on purpose, so the boundary is one cast. */
export const scene = (over: Record<string, unknown> = {}): Scene => ({
  id: "t", name: "T", kind: "test", dur: 10000, loop: false, volume: 0.8,
  blurb: "", base: { towerL: "candle", towerR: "off", door: "off" },
  levels: {}, cues: [], file: "t.mp3", yaml: "", ...over,
} as unknown as Scene);

/* ── Plain assertions, matching the other node tests ───────────────── */
export function makeAsserter(label: string): {
  ok: (c: boolean, m: string) => void; done: () => void;
} {
  let pass = 0;
  const fails: string[] = [];
  const ok = (c: boolean, m: string): void => { if (c) pass++; else fails.push(m); };
  const done = (): void => {
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
