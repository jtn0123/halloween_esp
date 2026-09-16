/* The hand-stubbed page the castle-radio tests run the real sources in.
 *
 * These files are plain browser scripts with no module boundary, so every
 * test runs the shipped source in a node:vm context: the assertions are about
 * what the castle is actually sent, not about text. Kept apart from the tests
 * themselves so both files stay inside the repo's line cap. */
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const read = name => fs.readFileSync(path.join(HERE, name), 'utf8');
export const fmt = s => `${Math.floor((s || 0) / 60)}:${String(Math.floor((s || 0) % 60)).padStart(2, '0')}`;
export const settle = () => new Promise(resolve => setImmediate(resolve));

export function element(id) {
  return {
    id, textContent: '', className: '', title: '', value: '', checked: false,
    disabled: false, hidden: false, innerHTML: '', attributes: {},
    options: [{textContent: ''}, {textContent: ''}],
    onclick: null, oninput: null, onchange: null, listeners: [],
    setAttribute(key, value) { this.attributes[key] = value; },
    getAttribute(key) { return this.attributes[key]; },
    addEventListener(type, fn) { this.listeners.push([type, fn]); },
    dispatch(type) { for (const [t, fn] of this.listeners) { if (t === type) {fn();} } },
    classList: {add() {}, remove() {}, toggle() {}},
    parentNode: {insertBefore() {}},
    append() {}, querySelector: () => element(`${id}-child`), querySelectorAll: () => [],
  };
}

export function page() {
  const nodes = new Map();
  const $ = id => { if (!nodes.has(id)) {nodes.set(id, element(id));} return nodes.get(id); };
  return {$, nodes};
}

/* device-link.js in a context whose castle answers with `ctx.payload`. */
export function linkContext(options = {}) {
  const {$} = page();
  const calls = {fetch: [], commands: [], next: 0, toasts: [], load: [], offers: [], retries: 0};
  const scene = {id: 'citizens', dur: 193360, loop: true, ...(options.scene || {})};
  const tracks = options.tracks || [
    {id: 0, file: '09_citizens.mp3', kind: 'song', duration: 193, title: 'Citizens'},
    {id: 1, file: '01_vigil.mp3', kind: 'scene', duration: 60, title: 'Vigil'},
  ];
  const ctx = {
    payload: options.payload,
    now: 1000, step: 0.2,
    // A poll that throws is how the castle goes quiet for a second.
    fail: false,
    // What the SD listing knows: `known` false is "no answer yet", which is
    // not the same fact as "that song is not on the castle".
    known: options.known ?? !!options.item,
    item: options.item ?? null,
    console,
    tracks, current: options.current ?? 0, queue: options.queue ?? [1], history: [],
    repeat: false, shuffle: false, blacked: false, stopped: true,
    switchEpoch: 0, switching: false, sceneData: [scene],
    fmt, syncLayer() {}, syncVolume() {}, drawPlayheads() {}, renderQueue() {}, updatePlayer() {},
    toast(message) { calls.toasts.push(message); },
    load(id) { calls.load.push(id); },
    next() { calls.next++; },
    audio: {pause() {}, currentTime: 0},
    // A real page clock moves between the poll landing and the code that
    // reads it, and never by the same amount twice: a frozen or evenly
    // stepped clock hides exactly the key B01 is about.
    performance: {now: () => { ctx.step += 0.11; ctx.now += ctx.step; return ctx.now; }},
    setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0,
    document: {hidden: false, addEventListener() {}},
    async fetch(url, init) {
      calls.fetch.push(url);
      const post = init?.method === 'POST';
      if (post) {calls.commands.push(JSON.parse(init.body));}
      if (!post && ctx.fail) {throw new Error('Failed to fetch');}
      return {ok: true, json: async () => (post ? {ok: true} : ctx.payload)};
    },
  };
  ctx.$ = $;
  ctx.window = {
    remoteLibrary: {
      syncing: () => !!options.syncing,
      item: () => ctx.item,
      known: () => ctx.known,
      trackByFilename: () => null,
      ensure: async () => options.inventory ?? null,
      offer: t => calls.offers.push(t),
      retry: () => {ctx.retried = true; calls.retries++;},
    },
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(read('device-link.js'), ctx, {filename: 'device-link.js'});
  return {ctx, calls, $, scene};
}

/* A castle that is playing the looping song `citizens` at `position_s`. */
export const playingAt = (position_s, uptime_s) => ({
  state: {scene: 'citizens', track: '', version: '5.55', playing: true, settling: false, uptime_s,
    volume: 45, show_on: false, sd_mounted: true, scenes: 'stop,citizens,vigil', pir: {armed: true, cooldown_s: 60}},
  playback: {position_s},
  capabilities: {position: true, track_end: true},
  light_show: null,
});

export const idle = (uptime_s, scenes = 'stop,citizens,vigil') => ({
  state: {scene: 'stop', track: '', version: '5.55', playing: false, settling: false, uptime_s, scenes,
    volume: 45, show_on: false, sd_mounted: true, pir: {armed: true, cooldown_s: 60}},
  playback: {position_s: 0}, capabilities: {position: true, track_end: true}, light_show: null,
});

/* A castle that has been told to start `scene` but has not left the old one. */
export const settlingOn = scene => ({
  state: {scene, track: '', version: '5.55', playing: false, settling: true,
    volume: 45, show_on: false, sd_mounted: true, scenes: 'stop,citizens,vigil', pir: {armed: true, cooldown_s: 60}},
  playback: {position_s: 0}, capabilities: {position: true, track_end: true}, light_show: null,
});

export async function poll(ctx, payload) {
  ctx.payload = payload;
  await ctx.window.castleLink.refresh();
  await settle();
}
export const pollAt = (ctx, position_s, uptime_s) => poll(ctx, playingAt(position_s, uptime_s));

/* castle-direct.js with a hand-stubbed castle: `answers` maps an /api path
   prefix to the JSON the firmware would return. Every sleep is recorded and
   resolved at once, and moves ctx.clock the way a real wait would, so the
   frame clock is read honestly and never actually waited on. */
export function directContext(answers, library) {
  const delays = [];
  const asked = [];
  const live = [];
  let peak = 0;
  const nodes = {
    'radio-scenes': {textContent: '[]'},
    'radio-library': {textContent: JSON.stringify(library)},
  };
  const ctx = {
    console, URL, Response, AbortController, Promise,
    JSON, Math, Number, String, Set, Map, Object,
    clock: 1000,
    performance: {now: () => ctx.clock},
    setTimeout(fn, ms) {
      // The 8 s abort guard of castle() is not a wait: only sleeps move time.
      if (ms < 8000) { delays.push(ms); ctx.clock += Math.max(0, ms); }
      setImmediate(fn);
      return delays.length;
    },
    clearTimeout() {},
    location: {href: 'http://castle.local/', origin: 'http://castle.local', host: 'castle.local'},
    document: {
      hidden: false,
      getElementById: id => nodes[id] || null,
      querySelectorAll: () => [],
      addEventListener() {},
    },
  };
  ctx.window = ctx;
  ctx.fetch = async path => {
    asked.push({path, at: ctx.clock});
    live.push(path);
    peak = Math.max(peak, live.length);
    // A socket is never instant, and overlapping requests are the whole
    // question for a castle that keeps four of them with an LRU purge.
    await new Promise(resolve => setImmediate(resolve));
    live.pop();
    const key = Object.keys(answers).find(p => path.startsWith(p));
    return {ok: true, status: 200, text: async () => JSON.stringify(answers[key] ?? {})};
  };
  vm.createContext(ctx);
  vm.runInContext(read('castle-direct.js'), ctx, {filename: 'castle-direct.js'});
  return {ctx, delays, asked, peak: () => peak};
}

/* A castle running a generated show for one imported song, whose position_ms
   never goes positive: the case that used to fire every past-due frame at once. */
const SOUNDLESS = {version: '5.55', scene: 'stop', track: 'radio_a.mp3', playing: true, position_ms: 0, scenes: 'stop'};
export const lightsSent = asked => asked.filter(a => a.path.startsWith('/api/light?c='))
  .map(a => ({c: a.path.slice('/api/light?c='.length), at: a.at}));

export function showContext(frames, extra = {}) {
  const row = {key: 'radio_a', filename: 'radio_a.mp3', bytes: 9, duration: 30, frames};
  const state = {...SOUNDLESS, ...extra};
  return {...directContext({
    '/api/status': state,
    '/api/files': [{name: 'radio_a.mp3', size: 9, dir: false}],
    '/api/light': {ok: true}, '/api/play': {ok: true}, '/api/stop': {ok: true},
  }, [row]), state};
}

/* The light_show the castle page reports for a generated show whose castle
   counted `before` applied/evicted LIGHT frames when the show started and
   `after` by the time the panel asked. */
export async function landedShow(before, after) {
  const {ctx, state} = showContext([[0, '111111'], [1, '222222']], before);
  await ctx.window.fetch('/radio/device/command', {method: 'POST',
    body: JSON.stringify({action: 'file', file: 'radio_a.mp3', key: 'radio_a'})});
  for (let i = 0; i < 40; i++) {await settle();}
  Object.assign(state, after);
  ctx.window.castleDirect.forget();
  const response = await ctx.window.fetch('/radio/device');
  return JSON.parse(await response.text()).light_show;
}

/* device-tools.js in a context whose castle link is a stub: `answer` is what
   /api/events gives back, and `push` is one update from the shared poll. */
export function toolsContext(answer) {
  const {$} = page();
  const ctx = {console, JSON, Number, String, Array, Math, Object, Promise, $, toast() {}};
  let push = () => {};
  ctx.document = {createElement: () => element('bench')};
  ctx.window = {castleLink: {
    subscribe(fn) { push = fn; }, command: async () => true, lastError: () => '', events: answer,
  }};
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(read('device-tools.js'), ctx, {filename: 'device-tools.js'});
  return {ctx, $, push: payload => push(payload)};
}

/* The castle's own record, and the bench panel after asking for it. */
export const EVENTS = [{t: 1000, e: 'play', a: 'radio_a.mp3'},
  {t: 6500, e: 'light_evicted', a: '3'}, {t: 11000, e: 'stop', a: ''}];
export async function eventLog(answer) {
  const {$, push} = toolsContext(answer);
  push({connected: false, error: 'offline', healthLine: 'link 4 ms', framesText: ''});
  await $('bench-events-load').onclick();
  return {log: $('bench-events-log').textContent, health: $('live-link-health').textContent};
}

export async function runFrames(frames) {
  const {ctx, asked} = showContext(frames);
  await ctx.window.fetch('/radio/device/command', {method: 'POST',
    body: JSON.stringify({action: 'file', file: 'radio_a.mp3', key: 'radio_a'})});
  for (let i = 0; i < 200; i++) {await settle();}
  return lightsSent(asked).filter(l => l.c !== 'show');
}

/* The three wordings for a running show: no counters, counters, evictions. */
export const wordings = text => [text({frames_sent: 40, frames_total: 50}),
  text({frames_sent: 40, frames_total: 50, frames_landed: 38, frames_evicted: 0}),
  text({frames_sent: 40, frames_total: 50, frames_landed: 36, frames_evicted: 2})];

/* One fast poll, one that takes half a second, and one that never lands. */
export async function healthRun(ctx) {
  const readings = [ctx.window.castleLink.health()];
  const quick = ctx.fetch;
  ctx.fetch = async (...args) => { ctx.now += 500; return quick(...args); };
  await pollAt(ctx, 12, 3673);
  readings.push(ctx.window.castleLink.health());
  ctx.fetch = quick; ctx.fail = true;
  await ctx.window.castleLink.refresh(); await settle();
  return [...readings, ctx.window.castleLink.health()];
}
