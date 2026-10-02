/* The castle key (firmware v5.74) in the page the castle serves, and the
 * Run settings card both builds share.
 *
 *   node --test demo/castle-radio/test_castle_key.test.mjs
 *
 * castle-direct.js runs against a castle that holds a key the way
 * firmware/sd_web_prefs.h does: changes without the right X-Castle-Key are
 * 401 "castle key required", POST /api/key with nothing to change is 400 to
 * the right key, and ?new= / ?clear=1 change it. The key lives in this
 * browser's localStorage under `castleKey`, the firmware fallback page's
 * name — and never in a reply. */
import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';

import {element, read, settle} from './test_support.mjs';

const KEY_MSG = 'This castle has a key — enter it in Settings';

function keyedCastle(key = 'pa"ss#1') {
  const castle = {key, heard: []};
  const store = new Map();
  const reply = (status, body) => ({ok: status < 400, status, text: async () => body});
  const ctx = {
    console, URL, Response, AbortController, Promise, JSON, Math, Number, String, Set, Map, Object,
    performance: {now: () => 1000},
    setTimeout: (fn, ms) => (ms >= 8000 ? 0 : setImmediate(fn)), clearTimeout() {},
    location: {href: 'http://castle.local/', origin: 'http://castle.local', host: 'castle.local'},
    localStorage: {getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k)},
    document: {hidden: false, getElementById: () => null, querySelectorAll: () => [], addEventListener() {}},
  };
  ctx.window = ctx;
  ctx.fetch = async (path, init = {}) => {
    const sent = init.headers?.['X-Castle-Key'] ?? null;
    castle.heard.push({path, method: init.method || 'GET', sent});
    const right = !castle.key || sent === castle.key;
    if (path === '/api/status') {return reply(200, JSON.stringify({version: '5.74', scene: 'stop', track: '', scenes: 'stop', locked: !!castle.key}));}
    if (path.startsWith('/api/key')) {
      if (!right) {return reply(401, 'castle key required\n');}
      const url = new URL(path, 'http://castle.local');
      if (url.searchParams.get('clear') === '1') { castle.key = ''; return reply(200, '{"locked":false}'); }
      if (url.searchParams.get('new')) { castle.key = url.searchParams.get('new'); return reply(200, '{"locked":true}'); }
      return reply(400, 'need new=<key> or clear=1\n');
    }
    if (path.startsWith('/api/pir')) {return right ? reply(200, '{"queued":true}') : reply(401, 'castle key required\n');}
    return reply(404, 'not found\n');
  };
  vm.createContext(ctx);
  vm.runInContext(read('castle-direct.js'), ctx, {filename: 'castle-direct.js'});
  const call = async (path, body) => {
    const r = await ctx.fetch(path, body ? {method: 'POST', body: JSON.stringify(body)} : undefined);
    return {status: r.status, body: await r.json()};
  };
  return {ctx, castle, store, call};
}

const PIR = {action: 'pir', armed: true, cooldown: 60};

test('a keyed castle\'s refusal reads as the Settings sentence, and the held key is sent', async () => {
  const {castle, store, call} = keyedCastle();
  const refused = await call('/radio/device/command', PIR);
  assert.deepEqual([refused.status, refused.body.error], [401, KEY_MSG]);
  store.set('castleKey', castle.key);
  assert.deepEqual((await call('/radio/device/command', PIR)).body, {queued: true});
  const pir = castle.heard.filter(h => h.path.startsWith('/api/pir'));
  assert.deepEqual(pir.map(h => h.sent), [null, castle.key]);
  // Reads do not carry it: a status poll is open on every castle.
  assert.ok(castle.heard.filter(h => h.method === 'GET').every(h => h.sent === null));
});

test('the key card: use checks before it remembers, set follows, clear forgets', async () => {
  const {castle, store, call} = keyedCastle('old-key');
  let r = await call('/radio/device/key');
  assert.deepEqual(r.body, {host: 'castle.local', remembered: false, pinned: false, locked: true});
  r = await call('/radio/device/key', {action: 'use', key: 'not-it'});
  assert.deepEqual([r.status, r.body.error, store.has('castleKey')], [401, 'that is not this castle\'s key', false]);
  r = await call('/radio/device/key', {action: 'use', key: 'two words'});
  assert.equal(r.status, 400);
  assert.ok(!JSON.stringify(r.body).includes('two words'), 'a refusal names the rule, never the key');
  r = await call('/radio/device/key', {action: 'use', key: 'old-key'});
  assert.deepEqual([r.status, r.body.remembered, store.get('castleKey')], [200, true, 'old-key']);
  r = await call('/radio/device/key', {action: 'set', key: 'n3w&key'});
  assert.equal(r.status, 200);
  assert.deepEqual([castle.key, store.get('castleKey')], ['n3w&key', 'n3w&key']);
  const set = castle.heard.find(h => h.path.startsWith('/api/key?new='));
  assert.deepEqual([set.path, set.sent], ['/api/key?new=n3w%26key', 'old-key']);
  r = await call('/radio/device/key', {action: 'clear'});
  assert.deepEqual([r.status, r.body.locked, castle.key, store.has('castleKey')], [200, false, '', false]);
  for (const answer of [r, await call('/radio/device/key')]) {
    assert.ok(!/old-key|n3w&key/.test(JSON.stringify(answer)), 'no reply carries a key');
  }
});

test('a key changed elsewhere is said, not remembered; an unknown action is refused', async () => {
  const {castle, store, call} = keyedCastle('someone-elses');
  store.set('castleKey', 'stale');
  let r = await call('/radio/device/key', {action: 'clear'});
  assert.deepEqual([r.status, r.body.error, castle.key, store.get('castleKey')], [401, KEY_MSG, 'someone-elses', 'stale']);
  r = await call('/radio/device/key', {action: 'constructor', key: 'k'});
  assert.deepEqual([r.status, r.body.error], [400, 'action is use, set or clear']);
});

/* The card itself, against whichever server answers /radio/device/key. */
function card(answer) {
  const nodes = new Map();
  const $ = id => { if (!nodes.has(id)) {nodes.set(id, element(id));} return nodes.get(id); };
  const posted = [];
  const ctx = {
    console, JSON, Promise,
    document: {getElementById: $},
    async fetch(path, init = {}) {
      if (init.method === 'POST') {posted.push(JSON.parse(init.body));}
      const [status, body] = answer(init.method === 'POST' ? JSON.parse(init.body) : null);
      return {ok: status < 400, status, json: async () => body};
    },
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(read('castle-key.js'), ctx, {filename: 'castle-key.js'});
  return {$, posted, ctx};
}

test('the card says the state, sends the typed key once, and empties the field', async () => {
  const state = {host: 'h', remembered: false, pinned: false, locked: true};
  const {$, posted} = card(body => (body?.key === 'wrong' ? [401, {error: 'that is not this castle\'s key'}]
    : [200, body ? {...state, remembered: body.action !== 'clear'} : state]));
  await settle();
  assert.equal($('key-state').textContent, 'This castle has a key. No key is remembered for it here.');
  $('key-input').value = 'wrong';
  await $('key-use').onclick();
  assert.equal($('key-message').textContent, 'that is not this castle\'s key');
  assert.equal($('key-input').value, '', 'a refused key does not linger in the field');
  $('key-input').value = ' s3cret ';
  await $('key-use').onclick();
  assert.deepEqual(posted.at(-1), {action: 'use', key: 's3cret'});
  assert.match($('key-state').textContent, /A key is remembered/);
  assert.ok(![...['key-state', 'key-message']].some(id => $(id).textContent.includes('s3cret')));
  await $('key-set').onclick();
  assert.equal($('key-message').textContent, 'Type the key first');
  await $('key-clear').onclick();
  assert.deepEqual(posted.at(-1), {action: 'clear', key: ''});
});

test('a pinned key sends the owner to the settings file', async () => {
  const {$} = card(() => [200, {host: 'h', remembered: false, pinned: true, locked: true}]);
  await settle();
  assert.match($('key-state').textContent, /settings file \(castle_key\)/);
});
