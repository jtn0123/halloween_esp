import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import {element, read, settle} from './test_support.mjs';

function boot({href = 'http://127.0.0.1:8871/companion.html?castle=http%3A%2F%2Fcastle.local',
  status = {service: 'castle-radio', protocol: 1, castle_origin: 'http://castle.local'}} = {}) {
  const nodes = {'companion-state': element('companion-state'), 'companion-reconnect': element('companion-reconnect')};
  nodes['companion-state'].dataset = {};
  const opener = {messages: [], postMessage(...args) { this.messages.push(args); }};
  const calls = [];
  let onmessage;
  const ctx = {
    URL, Headers, TextEncoder, ArrayBuffer, Error, Object, String, console, AbortSignal,
    location: new URL(href), document: {getElementById: id => nodes[id]},
    window: {opener, addEventListener(type, fn) { if (type === 'message') {onmessage = fn;} }},
    fetch: async (path, init) => {
      calls.push([path, init]);
      if (path === '/radio/tools') {
        return {ok: true, json: async () => status};
      }
      return {status: 206, headers: new Headers({'Content-Type': 'audio/mpeg'}),
        arrayBuffer: async () => Uint8Array.from([1, 2, 3]).buffer};
    },
  };
  vm.runInNewContext(read('companion.js'), ctx, {filename: 'companion.js'});
  return {ctx, nodes, opener, calls, send: event => onmessage(event)};
}

test('handshake requires an exact castle origin from local status', async () => {
  const good = boot();
  await settle();
  assert.equal(good.opener.messages[0][0].type, 'castle-tools-ready');
  assert.equal(good.opener.messages[0][1], 'http://castle.local');

  for (const options of [
    {status: {service: 'castle-radio', protocol: 1, castle_origin: 'http://other.local'}},
    {href: 'http://192.168.1.4:8871/companion.html?castle=http%3A%2F%2Fcastle.local'},
  ]) {
    const refused = boot(options);
    await settle();
    assert.equal(refused.opener.messages.length, 0);
    assert.equal(refused.nodes['companion-state'].dataset.state, 'error');
  }
});

test('source and origin mismatches are ignored', async () => {
  const bridge = boot();
  await settle();
  const before = bridge.calls.length;
  await bridge.send({source: {}, origin: 'http://castle.local', data: {type:'castle-tools-request', id:1, path:'/radio/library'}});
  await bridge.send({source: bridge.opener, origin: 'http://evil.local', data: {type:'castle-tools-request', id:2, path:'/radio/library'}});
  assert.equal(bridge.calls.length, before);
  assert.equal(bridge.opener.messages.length, 1);
});

test('unlisted paths, methods, and headers never reach the service', async () => {
  const bridge = boot();
  await settle();
  const cases = [
    {id: 1, path: '/radio/device/command', method: 'POST'},
    {id: 2, path: 'http://evil.local/radio/library'},
    {id: 3, path: '/radio/library', method: 'POST'},
    {id: 4, path: '/radio/import', method: 'POST', headers: {Authorization: 'secret'}},
  ];
  for (const data of cases) {
    await bridge.send({source: bridge.opener, origin: 'http://castle.local', data: {type:'castle-tools-request', ...data}});
  }
  assert.equal(bridge.calls.length, 1);
  assert.deepEqual(bridge.opener.messages.slice(1).map(row => row[0].status), [400, 400, 400, 400]);
});

test('allowed upload and media response preserve bytes and transfer ownership', async () => {
  const bridge = boot();
  await settle();
  const upload = Uint8Array.from([8, 9]).buffer;
  await bridge.send({source: bridge.opener, origin: 'http://castle.local', data: {
    type:'castle-tools-request', id:'upload', path:'/radio/import', method:'POST',
    headers: {'Content-Type':'application/octet-stream', 'X-Filename':'song.mp3', 'X-Split':'true'}, body: upload,
  }});
  await bridge.send({source: bridge.opener, origin: 'http://castle.local', data: {
    type:'castle-tools-request', id:'audio', path:'/radio/audio/song.mp3', method:'GET',
  }});
  assert.equal(bridge.calls[1][1].body, upload);
  const [message, target, transfer] = bridge.opener.messages.at(-1);
  assert.equal(message.status, 206);
  assert.equal(message.headers['Content-Type'], 'audio/mpeg');
  assert.deepEqual([...new Uint8Array(message.body)], [1, 2, 3]);
  assert.equal(target, 'http://castle.local');
  assert.equal(transfer[0], message.body);
});
