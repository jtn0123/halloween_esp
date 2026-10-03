/* The "Update castle" card (castle-update.js) against the answers
 * castle_update_routes.py gives, and against the castle-served page, where
 * castle-direct.js answers and there is no computer to fetch firmware.
 *
 *   node --test demo/castle-radio/test_castle_update.test.mjs
 *
 * The card never sends firmware on its own: only the button POSTs, and it
 * follows the job until the castle says which firmware it came back on. */
import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';

import {element, read, settle} from './test_support.mjs';

const COMPUTER_ONLY = 'This runs in the control room on your computer, not on the castle itself.';
const READY = {current: '5.75', available: '5.76', tag: 'v1.1.0', update: true,
  message: 'Firmware 5.76 (v1.1.0) is ready — the castle runs 5.75.', job: {running: false, done: false}};

/* The card, with `answer(method, headers)` -> [status, body] as the server. */
function card(answer) {
  const nodes = new Map();
  const $ = id => { if (!nodes.has(id)) {nodes.set(id, element(id));} return nodes.get(id); };
  const sent = [];
  const later = [];
  const ctx = {
    console, JSON, Promise, Error,
    setTimeout: fn => later.push(fn),
    document: {getElementById: $},
    async fetch(path, init = {}) {
      sent.push({path, method: init.method || 'GET', headers: init.headers || {}, body: init.body && JSON.parse(init.body)});
      const [status, body] = answer(init.method || 'GET', init.headers || {});
      return {ok: status < 400, status, json: async () => body};
    },
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(read('castle-update.js'), ctx, {filename: 'castle-update.js'});
  const tick = async () => { const fn = later.shift(); assert.ok(fn, 'a poll was scheduled'); await fn(); await settle(); };
  return {$, sent, later, tick};
}

test('the card shows both versions and offers the update only when there is one', async () => {
  const {$, sent} = card(() => [200, READY]);
  await settle();
  assert.deepEqual([$('fw-current').textContent, $('fw-available').textContent], ['5.75', '5.76 (v1.1.0)']);
  assert.equal($('fw-state').textContent, READY.message);
  assert.deepEqual([$('fw-update').disabled, $('fw-update').textContent], [false, 'Update castle to 5.76']);
  assert.ok(sent.every(s => s.method === 'GET'), 'opening the page sends nothing');

  const current = {...READY, current: '5.76', update: false, message: 'The castle runs firmware 5.76, the newest there is — nothing to update.'};
  const again = card(() => [200, current]);
  await settle();
  assert.deepEqual([again.$('fw-update').disabled, again.$('fw-update').textContent], [true, 'Update castle']);
  assert.match(again.$('fw-state').textContent, /nothing to update/);
});

test('the button starts one update and the card follows it to the verdict', async () => {
  let job = {running: false, done: false};
  const plan = {...READY};
  const {$, sent, tick} = card((method) => {
    if (method === 'POST') { job = {running: true, done: false, phase: 'Starting'}; return [202, {job}]; }
    return [200, job.running ? {job} : {...plan, job}];
  });
  await settle();
  await $('fw-update').onclick();
  const post = sent.find(s => s.method === 'POST');
  assert.deepEqual([post.path, post.headers['Content-Type'], post.body], ['/radio/castle/update', 'application/json', {action: 'update'}]);
  assert.equal($('fw-progress').hidden, false);
  assert.equal($('fw-update').disabled, true, 'no second press while it runs');
  job = {running: true, done: false, phase: 'Waiting for the castle to restart'};
  await tick();
  assert.equal($('fw-message').textContent, 'Waiting for the castle to restart…');
  assert.match($('fw-state').textContent, /leave it switched on/);
  job = {running: false, done: true, error: null, result: 'The castle now runs firmware 5.76 (it ran 5.75).'};
  Object.assign(plan, {current: '5.76', update: false, message: 'The castle runs firmware 5.76, the newest there is — nothing to update.'});
  await tick();
  assert.equal($('fw-message').textContent, job.result);
  assert.deepEqual([$('fw-current').textContent, $('fw-progress').hidden, $('fw-update').disabled], ['5.76', true, true]);
});

test('a rollback or a refusal is the message, and the card says why it cannot offer', async () => {
  const rolled = 'The castle restarted but still runs firmware 5.75: 5.76 did not start';
  const {$} = card(() => [502, {error: 'Could not get the newest castle firmware: cannot reach GitHub', job: {running: false, done: true, error: rolled}}]);
  await settle();
  assert.equal($('fw-message').textContent, rolled);
  assert.match($('fw-state').textContent, /cannot reach GitHub/);
  assert.equal($('fw-update').disabled, true);

  const busy = card(method => (method === 'POST' ? [409, {error: 'The castle is already being updated.'}] : [200, READY]));
  await settle();
  await busy.$('fw-update').onclick();
  assert.equal(busy.$('fw-message').textContent, 'The castle is already being updated.');
});

test('on the castle-served page the card says it needs the computer', async () => {
  const nodes = new Map();
  const $ = id => {
    if (!id.startsWith('fw-') && id !== 'castle-update') {return null;}
    if (!nodes.has(id)) {nodes.set(id, element(id));}
    return nodes.get(id);
  };
  const heard = [];
  const ctx = {
    console, URL, Response, AbortController, Promise, JSON, Math, Number, String, Set, Map, Object, Error,
    performance: {now: () => 1000},
    setTimeout: () => 0, clearTimeout() {},
    location: {href: 'http://castle.local/', origin: 'http://castle.local', host: 'castle.local'},
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
    document: {hidden: false, getElementById: $, querySelectorAll: () => [], addEventListener() {}},
    fetch: async path => { heard.push(path); return {ok: false, status: 404, text: async () => ''}; },
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(read('castle-direct.js'), ctx, {filename: 'castle-direct.js'});
  vm.runInContext(read('castle-update.js'), ctx, {filename: 'castle-update.js'});
  await settle();
  await settle();
  assert.equal($('fw-state').textContent, COMPUTER_ONLY);
  assert.equal($('fw-update').disabled, true);
  await ctx.castleUpdate.update();
  assert.equal($('fw-message').textContent, COMPUTER_ONLY);
  assert.deepEqual(heard, [], 'the castle is never sent an update request');
});
