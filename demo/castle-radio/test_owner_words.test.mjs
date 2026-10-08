/* The sentences an owner reads after removing a song, and on the castle's
 * inventory card, in the owner's words rather than the demo's.
 *
 *   node --test demo/castle-radio/test_owner_words.test.mjs
 *
 * Castle Radio runs inside the desktop app for a castle's owner, who has no
 * demo: "Removed … from this demo." and "outside this demo's synced library"
 * were the last of it in these two files. Castle Tools runs on Windows too,
 * so the castle page's tools card, a song the computer cannot hand over and
 * an older Castle Radio name the app and "your computer", never a Mac. These
 * run the real lines that write each sentence; test_owner_words.py holds
 * index.html's half, on the computer's page and the castle's. */
import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';

import {element, importsContext, notFound, page, read, settle} from './test_support.mjs';

const PREVIEW = read('preview.js');
const between = (source, from, to) => source.slice(source.indexOf(from), source.indexOf(to));

test('removing a song says it left your collection, and Undo is beside it', async () => {
  const {$} = page();
  const said = element('undo-text');
  $('undo-bar').hidden = true;
  $('undo-bar').querySelector = () => said;
  const asked = [];
  const ctx = {
    $, tracks: [{id: 0, key: 'radio_a', title: 'Ghost Walk'}, {id: 1, file: '01_vigil.mp3', title: 'Vigil'}],
    imported: new Set(['radio_a']), queue: [0, 1], history: [], current: -1,
    request: async (url, init) => { asked.push([init.method, url]); return {}; },
    stop() {}, load() {}, rememberHidden() {}, refresh: async () => {},
    renderTracks() {}, renderQueue() {}, renderImports() {},
    toast(message) { assert.fail(`no failure toast expected, got ${message}`); },
  };
  vm.createContext(ctx);
  await vm.runInContext(
    `${between(PREVIEW, 'async function deleteSong(id){', "$('tracks').addEventListener")}\ndeleteSong(0);`, ctx);
  assert.deepEqual(asked, [['DELETE', '/radio/library/radio_a']]);
  assert.equal($('undo-bar').hidden, false);
  assert.equal(said.textContent, 'Removed “Ghost Walk” from your collection.');
  assert.doesNotMatch(said.textContent, /demo/i);
});

test('the castle’s other audio is described against your synced songs, not a demo', () => {
  const {$} = page();
  const made = [];
  const appended = [];
  $('device').append = child => appended.push(child);
  const ctx = {
    $, tracks: [], window: {}, REQUEST_MS: {inventory: 1, act: 1, poll: 1},
    // The first listing never answers: this test reads the card's words.
    request: () => new Promise(() => {}),
    safe: s => String(s), toast() {}, renderTracks() {}, renderImports() {},
    setInterval: () => 0, setTimeout: () => 0,
    document: {
      hidden: false, body: {append() {}}, addEventListener() {},
      createElement(tag) {
        const el = Object.assign(element(tag), {open: false, showModal() {}, close() {}});
        made.push(el);
        return el;
      },
    },
  };
  vm.createContext(ctx);
  vm.runInContext(read('remote-library.js'), ctx, {filename: 'remote-library.js'});
  assert.equal(appended.length, 1, 'one card joins Your castle');
  const card = appended[0].innerHTML;
  assert.match(card, /<h2>Other audio on castle<\/h2>/);
  assert.match(card, /Files already on the castle’s SD card that are not among your synced songs\./);
  for (const el of made) {assert.doesNotMatch(el.innerHTML, /demo/i, el.className);}
});

// The castle page's tools card, run with no tools connected, then pressed.
function toolsCard({connected = false, fail = false} = {}) {
  const {$} = page();
  $('tools-state').dataset = {};
  $('tools-checks').replaceChildren = () => {};
  const ctx = {
    window: {castleDirect: {}, castleDesktop: {connected, connect() {}}, addEventListener() {}},
    document: {getElementById: $, createElement: () => element('li')},
    AbortSignal,
    fetch: async () => { if (fail) {throw Error('offline');} return {ok: true, json: async () => ({})}; },
  };
  vm.runInNewContext(read('desktop-tools.js'), ctx, {filename: 'desktop-tools.js'});
  return $;
}

test('the castle page asks to connect your computer, by the app’s own name', async () => {
  const $ = toolsCard();
  await settle();
  assert.equal($('tools-state').textContent, 'Connect your computer');
  assert.equal($('tools-summary').textContent, 'Click Start Castle Tools, allow it to open, then click '
    + 'Connect Castle Tools. You can import and split songs here while the castle handles playback.');
  assert.equal($('tools-connect').textContent, 'Connect Castle Tools');
  $('tools-start').dispatch('click');
  assert.equal($('tools-state').textContent, 'Starting Castle Tools…');
  assert.match($('tools-summary').textContent, / Connect Castle Tools\. .* once on your computer\.$/);
  $('tools-connect').dispatch('click');
  assert.equal($('tools-state').textContent, 'Connecting to your computer…');
});

test('tools that stop answering are started and connected again by name', async () => {
  const $ = toolsCard({connected: true, fail: true});
  await settle();
  assert.equal($('tools-connect').textContent, 'Reconnect Castle Tools');
  assert.equal($('tools-summary').textContent, 'Click Start Castle Tools, then Connect Castle Tools. '
    + 'If the browser cannot open Castle Tools, follow Setup & startup below.');
});

test('a song your computer cannot hand over says so, and what to press', async () => {
  const said = [];
  const ctx = {
    URL, toast: message => said.push(message), $: () => ({value: 'computer'}),
    audio: {src: '', removeAttribute() {}, load() {}},
    window: {castleDesktop: {connected: true, media: () => Promise.reject(new Error('gone'))}},
  };
  vm.createContext(ctx);
  const APP = read('app.js');
  vm.runInContext(`${between(APP, 'let audioSourceEpoch=0', '// The castle as the output')}
    setAudioSource('/radio/audio/radio_a').catch(() => {});`, ctx);
  await settle();
  assert.deepEqual(said, ['This song could not be loaded from your computer. '
    + 'Reconnect Castle Tools and select the song again.']);
});

test('an older Castle Radio is restarted with Castle Tools, on your computer', async () => {
  const {ctx} = importsContext(notFound);
  const said = [];
  ctx.toast = message => said.push(message);
  ctx.window.prompt = () => 'A new name';
  await vm.runInContext('renameSong(tracks[0])', ctx);
  assert.deepEqual(said, ['Restart Castle Tools on your computer to rename songs — '
    + 'it is still running the older version.']);
});
