/* Help with your castle (castle-help.js) — the card's three jobs.
 *
 *   node --test demo/castle-radio/test_castle_help.test.mjs
 *
 * The real script in a node:vm page with a stand-in castleLink, clipboard
 * and download. Held to: the castle's own page is linked at the address this
 * app knows (and not invented when it knows none); diagnostics come from
 * /radio/diagnostics, go to the clipboard and are shown before they are
 * shared, and fall back to a file when the browser will not copy; Save is a
 * file named as the server named it; a castle with no motion sensor loses the
 * motion controls and says why, and older firmware that does not say keeps
 * them; and the castle-served page links /owner and asks this computer for
 * nothing. */
import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';

import {read, settle} from './test_support.mjs';

function node(tag = 'div') {
  return {tag, id: '', className: '', textContent: '', hidden: false, disabled: false, value: '',
    children: [], onclick: null, href: '', clicked: 0, attributes: {}, label: null,
    append(...kids) { this.children.push(...kids); },
    setAttribute(k, v) { this.attributes[k] = v; },
    click() { this.clicked += 1; }, remove() {}, select() {},
    closest(sel) { return sel === 'label' ? this.label : null; }};
}

const REPORT = {text: 'Castle problem report\n…', name: 'castle-report-2026-10-02T10-00-00.txt'};

function page({direct = false, copies = true, answer = REPORT, ok = true} = {}) {
  const nodes = new Map();
  const byId = id => { if (!nodes.has(id)) {nodes.set(id, node());} return nodes.get(id); };
  for (const id of ['motion', 'interrupt', 'cooldown']) {byId(id).label = node('label');}
  const placed = [];
  byId('castle-find').parentNode = {insertBefore: (card, after) => placed.push([card, after])};
  const calls = [], copied = [], links = [];
  let listener = null;
  const ctx = {
    console, JSON, Promise, Error, Object,
    document: {getElementById: byId, body: {append: a => links.push(a)},
      createElement: tag => node(tag)},
    URL: {createObjectURL: blob => `blob:${blob.parts.join('')}`},
    Blob: class { constructor(parts) { this.parts = parts; } },
    navigator: {clipboard: {async writeText(t) { if (!copies) {throw new Error('denied');} copied.push(t); }}},
    async fetch(path) { calls.push(path); return {ok, status: ok ? 200 : 502, json: async () => answer}; },
    castleLink: {subscribe(fn) { listener = fn; }},
  };
  ctx.window = ctx;
  if (direct) {ctx.castleDirect = {};}
  vm.createContext(ctx);
  vm.runInContext(read('castle-help.js'), ctx, {filename: 'castle-help.js'});
  const card = placed[0]?.[0];
  const find = id => {
    const walk = n => n.id === id ? n : n.children?.map(walk).find(Boolean);
    return walk(card);
  };
  return {byId, calls, copied, links, card, find, placed, hear: s => listener(s)};
}

test('the card sits after Find my castle and links the castle’s own page at its address', () => {
  const {card, placed, byId, find, hear} = page();
  assert.equal(placed[0][1], byId('castle-find').nextSibling);
  assert.equal(card.id, 'castle-help');
  assert.equal(find('help-owner').hidden, true, 'no address, no link to nowhere');
  assert.match(find('help-owner-note').textContent, /^Find your castle first/);
  hear({connected: true, host: '192.168.1.40', state: {pir: {fitted: true}}});
  assert.equal(find('help-owner').href, 'http://192.168.1.40/owner');
  assert.equal(find('help-owner').hidden, false);
  hear({connected: false, host: '192.168.1.40', state: null});
  assert.match(find('help-owner-note').textContent, /not answering right now/);
});

test('Copy diagnostics: the server’s text, on the clipboard and shown before it is shared', async () => {
  const {calls, copied, find, links} = page();
  find('help-copy').onclick();
  await settle(); await settle();
  assert.deepEqual(calls, ['/radio/diagnostics']);
  assert.deepEqual(copied, [REPORT.text]);
  assert.equal(find('help-text').value, REPORT.text);
  assert.equal(find('help-text').hidden, false);
  assert.match(find('help-message').textContent, /^Copied\./);
  assert.equal(links.length, 0, 'nothing downloaded when the copy worked');
  assert.equal(find('help-copy').disabled, false);
});

test('a browser that will not copy gets the file instead, and Save is always a file', async () => {
  const {find, links} = page({copies: false});
  find('help-copy').onclick();
  await settle(); await settle();
  assert.equal(links.length, 1);
  assert.equal(links[0].download, REPORT.name);
  assert.equal(links[0].href, `blob:${REPORT.text}`);
  assert.equal(links[0].clicked, 1);
  assert.match(find('help-message').textContent, /would not copy, so it was saved as castle-report-/);
  find('help-save').onclick();
  await settle(); await settle();
  assert.equal(links.length, 2);
  assert.equal(find('help-message').textContent, `Saved as ${REPORT.name}.`);
});

test('a failed gather says so and leaves the buttons usable', async () => {
  const {find, copied} = page({ok: false, answer: {error: 'boom'}});
  find('help-copy').onclick();
  await settle(); await settle();
  assert.deepEqual(copied, []);
  assert.equal(find('help-message').textContent, 'Could not gather diagnostics: boom');
  assert.equal(find('help-text').hidden, true);
  assert.equal(find('help-save').disabled, false);
});

test('no motion sensor: the controls go and the note says why; older firmware keeps them', () => {
  const {byId, hear} = page();
  const rows = ['motion', 'interrupt', 'cooldown'].map(id => byId(id).label);
  hear({connected: true, host: 'c', state: {pir: {fitted: false, armed: false}}});
  assert.deepEqual(rows.map(r => r.hidden), [true, true, true]);
  assert.match(byId('motion-note').textContent, /^Motion sensor: not fitted on this castle/);
  hear({connected: true, host: 'c', state: {pir: {armed: true, cooldown_s: 60}}});
  assert.deepEqual(rows.map(r => r.hidden), [false, false, false], 'no `fitted` is not "no sensor"');
  hear({connected: false, host: 'c', state: null});
  assert.deepEqual(rows.map(r => r.hidden), [false, false, false]);
});

test('the castle-served page links /owner and asks this computer for nothing', () => {
  const {card, find, calls, hear} = page({direct: true});
  assert.equal(find('help-owner').href, '/owner');
  assert.equal(find('help-copy'), undefined, 'no computer half to copy');
  hear({connected: true, host: '192.168.1.40', state: {}});
  assert.equal(find('help-owner').href, '/owner');
  assert.match(find('help-owner-note').textContent, /Report a problem/);
  assert.deepEqual(calls, []);
  assert.equal(card.children.length, 3);
});
