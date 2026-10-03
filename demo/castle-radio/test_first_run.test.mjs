/* First run — the Listen page before anything is imported (first-run.js).
 *
 *   node --test demo/castle-radio/test_first_run.test.mjs
 *
 * The real script in a node:vm page that holds the parts of app.js it leans
 * on: the track list, the queue, renderTracks and load. Held to: demo rows
 * whose file is not on this computer are hidden and leave the queue; with
 * nothing left the first-run card leads, the count says 0 and Import is one
 * press away; the first song to arrive takes the card down; the demo rows
 * the computer does have stay; and the castle-served page asks nothing and
 * points at the computer instead, hiding the rows whose audio its own card
 * does not hold (a sold castle's card has the scenes and no song). */
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
    {id: 0, title: 'Vigil', file: '01_vigil.mp3', kind: 'scene'},
    {id: 1, title: 'Storm', file: '02_storm.mp3', kind: 'scene'},
    {id: 2, title: 'This Is Halloween', file: '09_the_citizens.mp3', kind: 'song'},
  ];
  let current = 0, queue = [1, 2], history = [0];
  let renders = 0, loaded = [], queues = 0;
  function renderTracks() { renders += 1; }
  function renderQueue() { queues += 1; }
  function updatePlayer() { renderTracks(); }
  function load(id) { current = id; loaded.push(id); updatePlayer(); }
`;

function page({demo = [], direct = false, failing = false, onCard = undefined} = {}) {
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
      return {ok: true, json: async () => ({demo})};
    },
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

test('the demo rows this computer has stay, and the player moves off a hidden one', async () => {
  const {card, val, byId} = page({demo: ['02_storm.mp3', '09_the_citizens.mp3']});
  await settle();
  assert.deepEqual(val('tracks.map(t => !!t.deleted)'), [true, false, false]);
  assert.deepEqual(val('loaded'), [1], 'Vigil is gone, so Storm is the current song');
  assert.deepEqual(val('queue'), [1, 2]);
  assert.equal(card.hidden, true);
  assert.equal(byId('collection-count').textContent, 2);
});

test('a server that does not answer hides nothing', async () => {
  const {card, val} = page({failing: true});
  await settle();
  assert.equal(val('tracks.filter(t => !t.deleted).length'), 3);
  assert.equal(card.hidden, true);
});

test('the castle-served page asks nothing and points at the computer', async () => {
  const {calls, card, run} = page({direct: true});
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
