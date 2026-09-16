/* Behaviour tests for the castle-facing browser code.
 *
 *   node --test demo/castle-radio/
 *
 * These files are plain browser scripts with no module boundary, so each test
 * runs the real source in a node:vm context with a hand-stubbed page: the
 * assertions are about what the castle is actually sent, not about text. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const read = name => fs.readFileSync(path.join(HERE, name), 'utf8');

/* ---------------------------------------------------------------- page stub */

function element(id) {
  const node = {
    id, textContent: '', className: '', title: '', value: '', checked: false,
    disabled: false, hidden: false, innerHTML: '', attributes: {},
    options: [{textContent: ''}, {textContent: ''}],
    onclick: null, oninput: null, onchange: null, listeners: [],
    setAttribute(key, value) { this.attributes[key] = value; },
    getAttribute(key) { return this.attributes[key]; },
    addEventListener(type, fn) { this.listeners.push([type, fn]); },
    dispatch(type) { for (const [t, fn] of this.listeners) { if (t === type) {fn();} } },
    classList: {add() {}, remove() {}, toggle() {}},
    append() {}, querySelector: () => element(`${id}-child`), querySelectorAll: () => [],
  };
  return node;
}

function page() {
  const nodes = new Map();
  const $ = id => { if (!nodes.has(id)) {nodes.set(id, element(id));} return nodes.get(id); };
  return {$, nodes};
}

const fmt = s => `${Math.floor((s || 0) / 60)}:${String(Math.floor((s || 0) % 60)).padStart(2, '0')}`;
const settle = () => new Promise(resolve => setImmediate(resolve));

/* device-link.js in a context whose castle answers with `ctx.payload`. */
function linkContext(options = {}) {
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
      return {ok: true, json: async () => (post ? {ok: true} : ctx.payload)};
    },
  };
  ctx.$ = $;
  ctx.window = {
    remoteLibrary: {
      syncing: () => !!options.syncing,
      item: () => options.item ?? null,
      trackByFilename: () => null,
      ensure: async () => options.inventory ?? null,
      offer: t => calls.offers.push(t),
      retry: () => {calls.retries++;},
    },
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(read('device-link.js'), ctx, {filename: 'device-link.js'});
  return {ctx, calls, $, scene};
}

/* A castle that is playing the looping song `citizens` at `position_s`. */
const playingAt = position_s => ({
  state: {scene: 'citizens', track: '', version: '5.55', playing: true, settling: false,
    volume: 45, show_on: false, sd_mounted: true, scenes: 'stop,citizens,vigil', pir: {armed: true, cooldown_s: 60}},
  playback: {position_s},
  capabilities: {position: true, track_end: true},
  light_show: null,
});

const idle = () => ({
  state: {scene: 'stop', track: '', version: '5.55', playing: false, settling: false,
    volume: 45, show_on: false, sd_mounted: true, scenes: 'stop,citizens,vigil', pir: {armed: true, cooldown_s: 60}},
  playback: {position_s: 0}, capabilities: {position: true, track_end: true}, light_show: null,
});

async function pollAt(ctx, position_s) {
  ctx.payload = playingAt(position_s);
  await ctx.window.castleLink.refresh();
  await settle();
}

/* ------------------------------------------------------------------- B01/B20 */

test('B01: a looping song advances the queue once per loop cycle, not once per poll', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  calls.next = 0;
  // Three polls inside the same finished cycle: 193.36 s song, threshold 192.86.
  for (const at of [193.0, 193.5, 200.0]) {await pollAt(ctx, at);}
  assert.equal(calls.next, 1, 'one advance for one finished loop cycle');
});

test('B20: a 4 s hidden-tab poll that lands past the window still advances, once', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  ctx.document.hidden = true;
  calls.next = 0;
  await pollAt(ctx, 196.9);   // 3.5 s past the end of cycle one
  assert.equal(calls.next, 1);
  await pollAt(ctx, 386.0);   // still inside cycle two
  assert.equal(calls.next, 1);
  await pollAt(ctx, 389.0);   // cycle two is over
  assert.equal(calls.next, 2);
});

/* ---------------------------------------------------------------------- B02 */

test('B02: the Your-castle stop ends the evening playlist, not just the scene', async () => {
  const {ctx, calls} = linkContext({payload: idle()});
  await settle();
  calls.commands.length = 0;
  ctx.$('live-show-stop').onclick();
  await settle();
  assert.equal(JSON.stringify(calls.commands[0]), '{"action":"show/stop"}');
});

/* ---------------------------------------------------------------------- B53 */

test('B53: starting the installed playlist parks the radio queue', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  await ctx.window.castlePlayer.play();        // the radio queue is live here
  await pollAt(ctx, 10);
  calls.commands.length = 0; calls.next = 0;
  ctx.$('live-show-start').onclick();
  await settle();
  assert.equal(JSON.stringify(calls.commands[0]), '{"action":"show/start"}');
  ctx.payload = idle();                        // the dark gap between scenes
  for (let i = 0; i < 4; i++) { await ctx.window.castleLink.refresh(); await settle(); }
  assert.equal(calls.next, 0, 'playlist gaps must not advance the radio queue');
});

/* ---------------------------------------------------------------------- B08 */

test('B08: a running sync throttles the status poll instead of stopping it', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10), syncing: true});
  await settle();
  assert.equal(calls.fetch.length, 1, 'the first poll went out during the sync');
  await ctx.window.castleLink.refresh(); await settle();
  assert.equal(calls.fetch.length, 1, 'a sync throttles the poll');
  ctx.now += 2100;
  await ctx.window.castleLink.refresh(); await settle();
  assert.equal(calls.fetch.length, 2, 'and the poll comes back on its own');
  ctx.now += 2100;
  await ctx.window.castleLink.refresh(); await settle();
  assert.equal(calls.fetch.length, 3, 'for as long as the transfer lasts');
});

/* ---------------------------------------------------------------------- B37 */

test('B37: a listing that has not answered yet does not open the sync dialog', async () => {
  const imported = {id: 2, key: 'radio_abc', file: '', kind: 'song', duration: 100, title: 'Imported'};
  const {ctx, calls} = linkContext({payload: idle(), current: 2, inventory: null,
    tracks: [{id: 0, file: '09_citizens.mp3', kind: 'song', duration: 193},
      {id: 1, file: '01_vigil.mp3', kind: 'scene', duration: 60}, imported]});
  await settle();
  ctx.$('output-target').value = 'castle';
  calls.commands.length = 0;
  await ctx.window.castlePlayer.play();
  assert.deepEqual(calls.offers, [], 'no "not synced" dialog for an unanswered listing');
  assert.equal(calls.retries, 1, 'the listing is retried');
  assert.match(calls.toasts.at(-1), /listing slow/i);
  assert.deepEqual(calls.commands, []);
});

test('B37: an inventory that does answer still offers a sync for a missing file', async () => {
  const imported = {id: 2, key: 'radio_abc', file: '', kind: 'song', duration: 100, title: 'Imported'};
  const {ctx, calls} = linkContext({payload: idle(), current: 2, inventory: {tracks: {}},
    tracks: [{id: 0, file: '09_citizens.mp3', kind: 'song'}, {id: 1, file: '01_vigil.mp3', kind: 'scene'}, imported]});
  await settle();
  ctx.$('output-target').value = 'castle';
  await ctx.window.castlePlayer.play();
  assert.deepEqual(calls.offers, [imported]);
});

/* ---------------------------------------------------------------------- B44 */

test('B44: previous restarts the castle track when its own clock is past 3 s', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  await pollAt(ctx, 12);
  calls.commands.length = 0;
  assert.equal(ctx.window.castlePlayer.previous(), true);
  await settle();
  assert.equal(JSON.stringify(calls.commands[0]), '{"action":"scene","scene":"citizens"}', 'the same song again');
});

test('B44: previous inside the first 3 s leaves the history walk alone', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  await pollAt(ctx, 1.2);
  calls.commands.length = 0;
  assert.equal(ctx.window.castlePlayer.previous(), false);
  assert.deepEqual(calls.commands, []);
});

/* ---------------------------------------------------------------------- B54 */

test('B54: owns() is true only for the song this page started on the castle', async () => {
  const {ctx} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  await pollAt(ctx, 10);
  // Nobody pressed Play here yet: the castle is playing something of its own.
  assert.equal(ctx.window.castlePlayer.owns(ctx.tracks[0]), false);
  await ctx.window.castlePlayer.play();
  await pollAt(ctx, 10);
  assert.equal(ctx.window.castlePlayer.owns(ctx.tracks[0]), true);
  assert.equal(ctx.window.castlePlayer.owns(ctx.tracks[1]), false);
});

test('B54: a library refresh only stops the castle for a song this page started', () => {
  const source = read('imports.js');
  const start = source.indexOf('function integrate(rows){');
  const end = source.indexOf('renderImports();}', start) + 'renderImports();}'.length;
  assert.ok(start >= 0 && end > start, 'integrate() is still one function');
  const run = owns => {
    const {$} = page();
    const calls = {stops: 0, loads: []};
    const ctx = {
      $, console, imported: new Map(), current: 0,
      tracks: [{id: 0, key: 'radio_gone', title: 'Gone'}, {id: 1, key: 'radio_keep', title: 'Keep'}],
      queue: [1], history: [],
      stop() { calls.stops++; }, load(id) { calls.loads.push(id); },
      renderTracks() {}, renderQueue() {}, renderImports() {},
      window: {castlePlayer: {active: () => true, owns: () => owns}},
    };
    vm.createContext(ctx);
    vm.runInContext(`${source.slice(start, end)}\nintegrate([{key:'radio_keep',title:'Keep'}]);`, ctx);
    return calls;
  };
  assert.equal(run(true).stops, 1, 'our own song is stopped before it is replaced');
  assert.equal(run(false).stops, 0, 'someone else’s song on the porch is left alone');
  assert.deepEqual(run(false).loads, [1]);
});

/* ------------------------------------------------------------------ B36/B12 */

function benchContext() {
  const {$} = page();
  const calls = {commands: [], subscribed: []};
  const bench = element('bench');
  bench.querySelectorAll = () => [];
  const grid = element('grid');
  grid.parentNode = {insertBefore() {}};
  const device = element('device');
  device.querySelector = () => grid;
  const ctx = {
    console, toast() {},
    $: id => (id === 'device' ? device : $(id)),
    document: {createElement: () => bench, addEventListener() {}},
    fetch() { throw new Error('the bench must not open its own request'); },
    window: {castleLink: {
      command: async body => { calls.commands.push(body); return true; },
      lastError: () => '',
      subscribe: fn => calls.subscribed.push(fn),
    }},
  };
  vm.createContext(ctx);
  vm.runInContext(read('device-tools.js'), ctx, {filename: 'device-tools.js'});
  return {ctx, calls, bench};
}

test('B36: bench commands go through castleLink so busy and the epoch apply', async () => {
  const {ctx, calls} = benchContext();
  await ctx.$('bench-audio-stop').onclick();
  assert.equal(JSON.stringify(calls.commands), '[{"action":"stop"}]', 'no private fetch of its own');
});

test('B12: the bench stop does not claim the installed playlist is over', async () => {
  const {ctx} = benchContext();
  await ctx.$('bench-audio-stop').onclick();
  const detail = ctx.$('bench-detail').textContent;
  assert.match(detail, /does not end the installed playlist/i);
});

/* ---------------------------------------------------------------------- B47 */

test('B47: load() gives the audio element no source while the castle is the output', () => {
  const source = read('app.js');
  const line = source.split('\n').find(l => l.startsWith('function load(id){'));
  assert.ok(line, 'load() is still one line of app.js');
  const run = output => {
    const {$} = page();
    $('output-target').value = output;
    const calls = {src: [], removed: 0, loads: 0};
    const ctx = {
      $, console, current: -1, fmt, updatePlayer() {},
      tracks: [{id: 0, file: '09_citizens.mp3', duration: 193}],
      audio: {
        set src(value) { calls.src.push(value); },
        get src() { return calls.src.at(-1) || ''; },
        removeAttribute(name) { if (name === 'src') {calls.removed++;} },
        load() { calls.loads++; },
      },
      window: {radioLayer: '', dispatchEvent() {}},
      CustomEvent: class { constructor(type) { this.type = type; } },
    };
    vm.createContext(ctx);
    vm.runInContext(`${line}\nload(0);`, ctx);
    return calls;
  };
  const castle = run('castle');
  assert.deepEqual(castle.src, [], 'the control-room laptop fetches nothing either');
  assert.equal(castle.removed, 1);
  assert.deepEqual(run('computer').src, ['media/09_citizens.mp3']);
});

/* ---------------------------------------------------------------------- B51 */

/* A castle that has been told to start `scene` but has not left the old one. */
const settlingOn = scene => ({
  state: {scene, track: '', version: '5.55', playing: false, settling: true,
    volume: 45, show_on: false, sd_mounted: true, scenes: 'stop,citizens,vigil', pir: {armed: true, cooldown_s: 60}},
  playback: {position_s: 0}, capabilities: {position: true, track_end: true}, light_show: null,
});

test('B51: a command still settling reads as starting, and offers no stop', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  await pollAt(ctx, 10);
  ctx.payload = settlingOn('vigil');
  await ctx.window.castleLink.refresh(); await settle();
  assert.equal(ctx.window.castlePlayer.playing(), false, 'settling is not playing');
  assert.match(ctx.$('hero-play').textContent, /starting/i);
  assert.match(ctx.$('preview-toggle').textContent, /starting/i);
  assert.equal(ctx.$('toggle').disabled, true, 'nothing to press while it starts');
  calls.commands.length = 0; calls.next = 0;
  ctx.window.castlePlayer.toggle();
  await settle();
  assert.deepEqual(calls.commands, [], 'the toggle must not stop the scene being left');
  assert.equal(calls.next, 0, 'and a settling poll is not the end of a song');
});

/* ---------------------------------------------------------------------- B60 */

/* castle-direct.js with a hand-stubbed castle: `answers` maps an /api path
   prefix to the JSON the firmware would return, and every sleep is recorded
   and then resolved at once, so the frame clock is read and not waited on. */
function directContext(answers, library) {
  const delays = [];
  const asked = [];
  const nodes = {
    'radio-scenes': {textContent: '[]'},
    'radio-library': {textContent: JSON.stringify(library)},
  };
  const ctx = {
    console, URL, Response, AbortController, Promise,
    JSON, Math, Number, String, Set, Map, Object,
    performance: {now: () => 1000},
    setTimeout(fn, ms) { delays.push(ms); setImmediate(fn); return delays.length; },
    clearTimeout() {},
    location: {href: 'http://castle.local/', origin: 'http://castle.local', host: 'castle.local'},
    document: {
      getElementById: id => nodes[id] || null,
      querySelectorAll: () => [],
      addEventListener() {},
    },
  };
  ctx.window = ctx;
  ctx.fetch = async path => {
    asked.push(path);
    const key = Object.keys(answers).find(p => path.startsWith(p));
    return {ok: true, status: 200, text: async () => JSON.stringify(answers[key] ?? {})};
  };
  vm.createContext(ctx);
  vm.runInContext(read('castle-direct.js'), ctx, {filename: 'castle-direct.js'});
  return {ctx, delays, asked};
}

test('B60: the direct frame clock waits for the speaker, like the Python bridge', async () => {
  const row = {key: 'radio_a', filename: 'radio_a.mp3', bytes: 9, duration: 30,
    frames: [[0, 'towerL:a832ff@50']]};
  // No position_ms: this castle never reports a clock, so the seeded estimate
  // is what every frame is sent on — 0.24 s of command latency plus the half
  // second the speaker takes to run (SPEAKER_START_S).
  const {ctx, delays} = directContext({
    '/api/status': {version: '5.55', scene: 'stop', track: 'radio_a.mp3', playing: true, scenes: 'stop'},
    '/api/files': [{name: 'radio_a.mp3', size: 9, dir: false}],
    '/api/light': {ok: true},
    '/api/play': {ok: true},
  }, [row]);
  await ctx.window.fetch('/radio/device/command', {method: 'POST',
    body: JSON.stringify({action: 'file', file: 'radio_a.mp3', key: 'radio_a'})});
  for (let i = 0; i < 40; i++) {await settle();}
  assert.ok(delays.some(ms => Math.abs(ms - 740) < 1),
    `the first frame waits 0.74 s, not 0.24 s (waits seen: ${delays})`);
  assert.ok(!delays.some(ms => Math.abs(ms - 240) < 1), 'no speaker-blind 0.24 s seed');
});
