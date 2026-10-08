/* First run — the Listen page before anything is imported (first-run.js).
 *
 *   node --test demo/castle-radio/test_first_run.test.mjs
 *
 * The real script in a node:vm page that holds the parts of app.js it leans
 * on: the track list (every built-in starting hidden, as app.js's does), the
 * queue, renderTracks, load and probeDuration. Held to: until the server
 * answers nothing is shown, loaded or probed and the card waits; demo rows
 * whose file is not on this computer stay hidden and out of the queue; with
 * nothing left the first-run card leads, the count says 0 and Import is one
 * press away; the first song to arrive takes the card down; the demo rows
 * the computer does have are shown, probed and the first of them loaded; a
 * built-in the owner removed stays removed; and the castle-served page asks
 * nothing and points at the computer instead, hiding the rows whose audio
 * its own card does not hold (a sold castle's card has the scenes and no
 * song). test_first_paint.test.mjs holds app.js's and preview.js's half. */
import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';

import {read, settle} from './test_support.mjs';

function node(tag = 'div') {
  return {tag, id: '', className: '', textContent: '', hidden: false, children: [], onclick: null,
    append(...kids) { this.children.push(...kids); }};
}

const APP = `
  const tracks = [
    {id: 0, title: 'Vigil', file: '01_vigil.mp3', kind: 'scene', deleted: true, absent: true},
    {id: 1, title: 'Storm', file: '02_storm.mp3', kind: 'scene', deleted: true, absent: true},
    {id: 2, title: 'This Is Halloween', file: '09_the_citizens.mp3', kind: 'song', deleted: true, absent: true},
  ];
  let current = 0, queue = [], history = [];
  let renders = 0, loaded = [], queues = 0, probed = [];
  function renderTracks() { renders += 1; }
  function renderQueue() { queues += 1; }
  function updatePlayer() { renderTracks(); }
  function load(id) { current = id; loaded.push(id); updatePlayer(); }
  function probeDuration(t) { probed.push(t.id); }
`;

function page({demo = [], direct = false, failing = false, onCard = undefined, hidden = null, pending = false} = {}) {
  const nodes = new Map();
  const byId = id => { if (!nodes.has(id)) {nodes.set(id, node());} return nodes.get(id); };
  const placed = [];
  byId('tracks').parentNode = {insertBefore: (card, before) => placed.push([card, before])};
  const calls = [];
  const ctx = {
    console, JSON, Promise, Set, String, Error,
    location: {hash: ''},
    document: {getElementById: byId, createElement: node},
    async fetch(path) {
      calls.push(path);
      if (failing) {throw new Error('offline');}
      if (pending) {return new Promise(() => {});}
      return {ok: true, json: async () => ({demo})};
    },
    // preview.js's record of the built-ins the owner removed.
    localStorage: {getItem: key => (key === 'castle-radio-hidden' && hidden ? JSON.stringify(hidden) : null)},
  };
  ctx.window = ctx;
  ctx.$ = byId;
  if (direct) {ctx.castleDirect = {scenes: []};}
  // remote-library.js's inventory of the castle's card, keyed by file:
  // `onCard` lists the files on it; null is a card that never answered.
  if (onCard !== undefined) {
    ctx.remoteLibrary = {ensure: async () => {
      if (onCard === 'fails') {throw new Error('listing timed out');}
      return onCard && {tracks: Object.fromEntries(onCard.map(f => [f, {audio: true}]))};
    }};
  }
  vm.createContext(ctx);
  vm.runInContext(APP, ctx);
  vm.runInContext(read('first-run.js'), ctx, {filename: 'first-run.js'});
  const card = placed[0]?.[0];
  // JSON across the realm boundary: the context's arrays are not this one's.
  const val = code => JSON.parse(vm.runInContext(`JSON.stringify(${code})`, ctx));
  return {ctx, byId, calls, card, placed, val, run: code => vm.runInContext(code, ctx)};
}

test('no demo on this computer and nothing imported: the card leads and Import is one press away', async () => {
  const {byId, calls, card, placed, val, ctx} = page();
  await settle();
  assert.deepEqual(calls, ['/radio/first-run']);
  assert.equal(placed[0][1], byId('tracks'), 'the card sits above the list');
  assert.equal(val('tracks.filter(t => !t.deleted).length'), 0);
  assert.deepEqual(val('[queue, history]'), [[], []], 'no hidden row is left to play next');
  assert.deepEqual(val('[loaded, probed]'), [[], []], 'no audio is asked for, so none can be unavailable');
  assert.equal(card.hidden, false);
  assert.equal(card.children[0].textContent, 'Add your first song');
  assert.equal(byId('collection-count').textContent, 0);
  assert.equal(byId('collection-caption').textContent, 'No songs yet · add your first');
  const go = card.children.find(c => c.id === 'first-run-import');
  go.onclick();
  assert.equal(ctx.location.hash, 'import');
});

test('the first song to arrive takes the card down, as imports.js renders it', async () => {
  const {card, byId, run} = page();
  await settle();
  run("tracks.push({id: 3, key: 'radio_first', title: 'First', file: ''}); renderTracks();");
  assert.equal(card.hidden, true);
  assert.equal(byId('collection-count').textContent, 1);
  assert.equal(byId('collection-caption').textContent, '1 tracks · automatic light shows');
});

test('before the server answers, nothing is shown, loaded or probed, and the card waits', async () => {
  const {card, val, byId} = page({pending: true});
  await settle();
  assert.equal(val('tracks.filter(t => !t.deleted).length'), 0);
  assert.deepEqual(val('[loaded, probed, queue]'), [[], [], []]);
  assert.equal(card.hidden, true, 'an empty list is not yet "no songs"');
  assert.equal(byId('split-preview').hidden, true, 'no preview of a song that is not here');
  assert.equal(byId('collection-count').textContent, 0, 'not the page\'s 10');
  assert.equal(byId('collection-caption').textContent, 'Looking for your songs…');
});

test('the demo rows this computer has are shown, and the first of them is loaded', async () => {
  const {card, val, byId} = page({demo: ['02_storm.mp3', '09_the_citizens.mp3']});
  await settle();
  assert.deepEqual(val('tracks.map(t => !!t.deleted)'), [true, false, false]);
  assert.deepEqual(val('probed'), [1, 2], 'only audio that is here is read for its length');
  assert.deepEqual(val('loaded'), [1], 'Vigil is not here, so Storm is the current song');
  assert.deepEqual(val('queue'), [2], 'and the rest follow it');
  assert.equal(card.hidden, true);
  assert.equal(byId('split-preview').hidden, false);
  assert.equal(byId('collection-count').textContent, 2);
});

test('a built-in the owner removed stays removed, and is still theirs to have removed', async () => {
  const {val} = page({demo: ['01_vigil.mp3', '02_storm.mp3', '09_the_citizens.mp3'], hidden: ['02_storm.mp3']});
  await settle();
  assert.deepEqual(val('tracks.map(t => !!t.deleted)'), [false, true, false]);
  assert.deepEqual(val('probed'), [0, 2]);
  assert.deepEqual(val('[current, queue]'), [0, [2]]);
  // preview.js's rememberHidden() keeps what is deleted and not absent.
  assert.deepEqual(val('tracks.map(t => !!t.absent)'), [false, false, false]);
});

test('a server that does not answer hides nothing', async () => {
  const {card, val} = page({failing: true});
  await settle();
  assert.equal(val('tracks.filter(t => !t.deleted).length'), 3);
  assert.deepEqual(val('[loaded, queue]'), [[0], [1, 2]]);
  assert.equal(card.hidden, true);
});

test('the castle-served page asks nothing and points at the computer', async () => {
  const {calls, card, run, val} = page({direct: true});
  assert.equal(val('tracks.filter(t => !t.deleted).length'), 3, 'every row shows at once');
  await settle();
  assert.deepEqual(calls, []);
  assert.match(card.children[1].textContent, /^No songs on this castle yet\. Add them from Castle Radio on your computer/);
  assert.equal(card.children.length, 2, 'no Import button where importing cannot happen');
  run('tracks.forEach(t => { t.deleted = true; }); renderTracks();');
  assert.equal(card.hidden, false);
});

test('on the castle, a built-in row whose audio its card lacks is hidden', async () => {
  const {calls, val, byId, card} = page({direct: true, onCard: ['01_vigil.mp3', '02_storm.mp3']});
  await settle();
  assert.deepEqual(calls, [], 'the card is read through remote-library.js, not the computer');
  assert.deepEqual(val('tracks.map(t => !!t.deleted)'), [false, false, true], 'the song is not on it');
  assert.deepEqual(val('tracks.map(t => !!t.absent)'), [false, false, true], 'and is not one the owner removed');
  assert.deepEqual(val('queue'), [1]);
  assert.equal(card.hidden, true);
  assert.equal(byId('collection-count').textContent, 2);
});

test('on the castle, a card that does not answer hides nothing', async () => {
  for (const answer of [null, 'fails']) {
    const {val} = page({direct: true, onCard: answer});
    await settle();
    assert.equal(val('tracks.filter(t => !t.deleted).length'), 3, String(answer));
  }
});
