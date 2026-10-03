/* Find my castle — the Your castle card (castle-find.js).
 *
 *   node --test demo/castle-radio/test_castle_find.test.mjs
 *
 * The real script in a node:vm page whose server answers the three routes
 * castle_finder.py serves. Held to: one sentence for each state (none yet,
 * not answering, connected, pinned); a castle is adopted by the address the
 * browse found and the name it gave; a hand-typed address goes the same
 * way; the offline chip opens the card; and the castle-served page, which
 * IS the castle, hides it and asks nothing. */
import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';

import {read, settle} from './test_support.mjs';

function node(tag = 'div') {
  return {
    tag, textContent: '', title: '', value: '', hidden: false, disabled: false, children: [],
    listeners: {}, onclick: null,
    append(...kids) { this.children.push(...kids); },
    replaceChildren(...kids) { this.children = kids; },
    addEventListener(type, fn) { this.listeners[type] = fn; },
    scrollIntoView() { this.scrolled = true; },
  };
}

function cardPage({where = {host: '', pinned: false, store: true}, found = [], direct = false} = {}) {
  const nodes = new Map();
  const byId = id => { if (!nodes.has(id)) {nodes.set(id, node());} return nodes.get(id); };
  const calls = [];
  const subscribers = [];
  const deviceNav = node('button');
  deviceNav.click = () => { deviceNav.clicked = true; };
  const ctx = {
    console, JSON, Promise,
    document: {getElementById: byId, createElement: node, querySelector: q => (q.includes('device') ? deviceNav : null)},
    castleLink: {subscribe: fn => subscribers.push(fn), refresh() { calls.push('refresh'); }},
    castleKey: {refresh() { calls.push('key'); }},
    async fetch(path, init = {}) {
      const body = init.body ? JSON.parse(init.body) : undefined;
      calls.push({path, method: init.method || 'GET', body});
      let answer = where;
      if (path === '/radio/device/find') {answer = {...where, found};}
      if (path === '/radio/device/address' && body) {
        if (body.host === 'nowhere') {return {ok: false, status: 502, json: async () => ({error: 'Nothing at nowhere answered as a castle'})};}
        where = {...where, host: body.host};
        answer = {...where, name: body.name, version: '5.75'};
      }
      return {ok: true, status: 200, json: async () => answer};
    },
  };
  ctx.window = ctx;
  if (direct) {ctx.castleDirect = {scenes: []};}
  vm.createContext(ctx);
  vm.runInContext(read('castle-find.js'), ctx, {filename: 'castle-find.js'});
  const say = snapshot => subscribers.forEach(fn => fn(snapshot));
  return {ctx, byId, calls, say, deviceNav};
}

const FOUND = [{name: 'castle-a1b2c3.local', address: '192.168.1.50', version: '5.75', fw_variant: 'buyer', current: false}];

test('no castle yet: find, pick, and it is remembered and followed', async () => {
  const {ctx, byId, calls} = cardPage({found: FOUND});
  await settle();
  assert.match(byId('find-state').textContent, /^No castle found yet/);
  await ctx.castleFind.find();
  const [row] = byId('find-results').children;
  assert.match(row.children[0].textContent, /castle-a1b2c3\.local · 192\.168\.1\.50 · v5\.75 · buyer/);
  assert.equal(byId('find-message').textContent, 'One castle answered.');
  await row.children[1].onclick();
  await settle();
  const adopt = calls.find(c => c.path === '/radio/device/address' && c.method === 'POST');
  assert.deepEqual(adopt.body, {host: '192.168.1.50', name: 'castle-a1b2c3.local'});
  assert.equal(byId('find-message').textContent, 'This is your castle now · castle-a1b2c3.local');
  assert.ok(calls.includes('refresh') && calls.includes('key'), 'the link and the key card follow');
  assert.match(byId('find-state').textContent, /192\.168\.1\.50/);
});

test('a typed address goes the same way, and a refusal is said', async () => {
  const {byId, calls} = cardPage();
  await settle();
  byId('find-use').onclick();
  assert.equal(byId('find-message').textContent, 'Type the castle’s address first');
  byId('find-address').value = ' nowhere ';
  byId('find-use').onclick();
  await settle();
  assert.equal(byId('find-message').textContent, 'Nothing at nowhere answered as a castle');
  byId('find-address').value = '10.0.0.5';
  byId('find-use').onclick();
  await settle();
  assert.equal(calls.at(-3).body.host, '10.0.0.5');
  assert.equal(byId('find-address').value, '', 'the field empties once it is the castle');
});

test('one sentence for each state, and the offline chip opens the card', async () => {
  const {byId, say, deviceNav} = cardPage({where: {host: '192.168.1.50', pinned: false, store: true}});
  await settle();
  say({connected: false, error: 'timed out'});
  assert.match(byId('find-state').textContent, /not answering.*find it again/);
  assert.equal(byId('castle-chip').title, 'timed out · click to find it');
  byId('castle-chip').listeners.click();
  assert.ok(deviceNav.clicked && byId('castle-find').scrolled);
  say({connected: true});
  assert.equal(byId('find-state').textContent, 'Connected to your castle at 192.168.1.50.');
  const pinned = cardPage({where: {host: 'castle.local', pinned: true, store: true}, found: FOUND});
  await settle();
  assert.match(pinned.byId('find-state').textContent, /castle_host in its settings file/);
  assert.ok(pinned.byId('find-run').disabled && pinned.byId('find-use').disabled);
  await pinned.ctx.castleFind.find();
  assert.ok(pinned.byId('find-results').children[0].children[1].disabled);
});

test('the castle-served page hides the card and asks nothing', async () => {
  const {byId, calls} = cardPage({direct: true});
  await settle();
  assert.equal(byId('castle-find').hidden, true);
  assert.deepEqual(calls, []);
});
