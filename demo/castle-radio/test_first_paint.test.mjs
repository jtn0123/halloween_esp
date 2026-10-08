/* The Listen page before first-run.js has heard what is here.
 *
 *   node --test demo/castle-radio/test_first_paint.test.mjs
 *
 * An installed app has none of media/, and its first run used to say so in
 * three error lines under the welcome card: "This audio file is
 * unavailable" (load(0) gave the player a built-in's file), "Waveform
 * unavailable" (preview.js asked for its waveform) and "No matching tracks"
 * (an empty collection read as a search that missed). These run the real
 * lines of app.js and preview.js that did each: every built-in starts
 * hidden, a hidden row gets no audio source and no waveform request, an
 * empty collection prints no search miss, and the player names no song it
 * cannot play. test_first_run.test.mjs holds first-run.js's half. */
import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';

import {fmt, page, read} from './test_support.mjs';

const APP = read('app.js');
const PREVIEW = read('preview.js');
const line = (source, start) => {
  const found = source.split('\n').find(l => l.startsWith(start));
  assert.ok(found, `${start} is still one line`);
  return found;
};
const between = (source, from, to) => source.slice(source.indexOf(from), source.indexOf(to));

test('every built-in starts hidden, until first-run.js hears its audio is here', () => {
  const ctx = vm.createContext({});
  const tracks = JSON.parse(vm.runInContext(
    `${between(APP, 'const tracks = [', 'const $ = id')}\nJSON.stringify(tracks)`, ctx));
  assert.equal(tracks.length, 10);
  for (const t of tracks) {
    assert.equal(t.deleted, true, t.title);
    assert.equal(t.absent, true, t.title);
  }
  assert.match(APP, /let current=0, queue=\[\], history=\[\]/, 'nothing queued before anything is shown');
});

function loadInto(output, rows) {
  const {$} = page();
  $('output-target').value = output;
  const calls = {src: [], removed: 0};
  const ctx = {
    $, console, current: -1, fmt, updatePlayer() {}, tracks: rows,
    audio: {
      set src(value) { calls.src.push(value); },
      get src() { return calls.src.at(-1) || ''; },
      removeAttribute(name) { if (name === 'src') {calls.removed++;} },
      load() {},
    },
    window: {radioLayer: '', dispatchEvent() {}},
    CustomEvent: class { constructor(type) { this.type = type; } },
  };
  vm.createContext(ctx);
  vm.runInContext(`${between(APP, 'let audioSourceEpoch', 'function load(id)')}\n${line(APP, 'function load(id){')}\nload(0);`, ctx);
  return calls;
}

test('a hidden row gets no audio source, so there is no file to be unavailable', () => {
  const hidden = loadInto('computer', [{id: 0, file: '09_citizens.mp3', duration: 0, deleted: true}]);
  assert.deepEqual(hidden.src, []);
  assert.equal(hidden.removed, 1);
  const shown = loadInto('computer', [{id: 0, file: '09_citizens.mp3', duration: 0}]);
  assert.deepEqual(shown.src, ['media/09_citizens.mp3']);
});

function rows(tracks, query = '') {
  const {$} = page();
  $('search').value = query;
  const ctx = {$, tracks, fmt, filter: 'all', current: -1, audio: {paused: true},
    art: () => '', safe: s => String(s), window: {}};
  vm.createContext(ctx);
  vm.runInContext(`${line(APP, 'function renderTracks(){')}\nrenderTracks();`, ctx);
  return $('tracks').innerHTML;
}

test('an empty collection prints no search miss; a search that misses still does', () => {
  const song = (id, extra = {}) => ({id, title: `Song ${id}`, artist: '', style: '', kind: 'song', ...extra});
  assert.equal(rows([song(0, {deleted: true}), song(1, {deleted: true})]), '');
  assert.match(rows([song(0), song(1)], 'nothing like it'), /No matching tracks/);
});

test('the player names no song it cannot play', () => {
  const {$} = page();
  const ctx = {
    $, tracks: [{id: 0, title: 'This Is Halloween', symbol: '☾', color: '#a27143', deleted: true}],
    current: 0, queue: [], history: [], repeat: false, shuffle: false, blacked: false, stopped: true,
    audio: {paused: true, currentTime: 0}, renderTracks() {}, window: {},
  };
  $('current-art').style = {setProperty() {}};
  vm.createContext(ctx);
  vm.runInContext(`${between(APP, 'function playerDetail(){', 'let audioSourceEpoch')}\nupdatePlayer();`, ctx);
  assert.equal($('current-title').textContent, 'No song yet');
  assert.equal($('current-art').textContent, '');
  assert.equal($('current-detail').textContent, 'Nothing to play yet');
  assert.equal($('toggle').disabled, true);
  assert.equal($('hero-play').disabled, true);
});

function waveFor(t) {
  const {$} = page();
  const asked = [];
  const ctx = {
    $, tracks: [t], current: 0, waveformEpoch: 0, waveformData: undefined,
    waveformCache: new Map(), REQUEST_MS: {analysis: 1},
    request: async path => { asked.push(path); throw new Error('404'); },
    drawWaveforms() {}, encodeURIComponent,
  };
  vm.createContext(ctx);
  vm.runInContext(`${between(PREVIEW, 'async function loadWaveforms(){', 'function drawWaveforms(){')}\nloadWaveforms();`, ctx);
  return {asked, status: () => $('wave-status').textContent};
}

test('a hidden row asks for no waveform, so none is unavailable', async () => {
  const hidden = waveFor({id: 0, file: '09_citizens.mp3', deleted: true});
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(hidden.asked, []);
  assert.equal(hidden.status(), '');
  const shown = waveFor({id: 0, file: '09_citizens.mp3'});
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(shown.asked, ['/radio/waveform/09_citizens.mp3']);
  assert.match(shown.status(), /Waveform unavailable/, 'a song that is here still reports a failure');
});

test('the removed-songs record holds what the owner removed, not what is absent', () => {
  let saved = null;
  const ctx = {
    tracks: [
      {file: '01_vigil.mp3', deleted: true, absent: false},
      {file: '02_storm.mp3', deleted: true, absent: true},
      {file: '03_seance.mp3', deleted: false, absent: false},
      {key: 'radio_x', file: '', deleted: true},
    ],
    localStorage: {setItem: (key, value) => { saved = [key, JSON.parse(value)]; }},
    JSON,
  };
  vm.createContext(ctx);
  vm.runInContext(`${line(PREVIEW, 'function rememberHidden(){')}\nrememberHidden();`, ctx);
  assert.deepEqual(saved, ['castle-radio-hidden', ['01_vigil.mp3']]);
});

test('a built-in shown on this computer is read for its length from media/', () => {
  const made = [];
  const ctx = {
    renders: 0,
    Audio: class { constructor() { made.push(this); } },
    Number,
  };
  ctx.renderTracks = () => { ctx.renders++; };
  vm.createContext(ctx);
  vm.runInContext(`${line(APP, 'function probeDuration(t){')}\nconst t={file:'01_vigil.mp3',duration:0};probeDuration(t);globalThis.t=t;`, ctx);
  assert.equal(made.length, 1);
  assert.equal(made[0].src, 'media/01_vigil.mp3');
  made[0].duration = 61.5;
  made[0].onloadedmetadata();
  assert.equal(vm.runInContext('t.duration', ctx), 61.5);
  assert.equal(ctx.renders, 1);
});
