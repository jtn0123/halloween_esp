import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import {page, read, element, settle} from './test_support.mjs';

function boot(payload, {direct = false, fail = false, connected = false} = {}) {
  const {$} = page();
  const rows = [];
  $('tools-checks').replaceChildren = () => { rows.length = 0; };
  $('tools-checks').append = row => rows.push(row.textContent);
  $('tools-state').dataset = {};
  let calls = 0;
  const ctx = {
    window: {castleDirect: direct ? {} : undefined, castleDesktop: {connected}, addEventListener() {}},
    document: {getElementById: $, createElement: () => element('li')},
    AbortSignal,
    fetch: async () => { calls++; if (fail) {throw Error('offline');} return {ok: true, json: async () => payload}; },
  };
  vm.runInNewContext(read('desktop-tools.js'), ctx);
  return {$, rows, calls: () => calls};
}

test('ready tools are identified without hiding playback or import controls', async () => {
  const ctx = boot({service: 'castle-radio', protocol: 1, ready: true, checks: [{name:'Model',ok:true,detail:'Cached'}]});
  await settle();
  assert.equal(ctx.$('tools-state').textContent, 'Desktop tools connected');
  assert.equal(ctx.$('tools-open').hidden, true);
  assert.deepEqual(ctx.rows, ['✓ Model — Cached']);
  assert.equal(ctx.$('import-submit').disabled, false);
});

test('missing optional model exposes repair details and leaves ordinary import available', async () => {
  const ctx = boot({service:'castle-radio', protocol:1, ready:false, checks:[{name:'Voice model',ok:false,detail:'Run installer'}]});
  await settle();
  assert.match(ctx.$('tools-state').textContent, /setup needs attention/);
  assert.equal(ctx.$('tools-setup').open, true);
  assert.match(ctx.rows[0], /Needs attention/);
  assert.equal(ctx.$('import-submit').disabled, false);
});

for (const payload of [null, {service:'unrelated', protocol:1, checks:[]}]) {
  test(`offline or unrelated service gives launch instructions (${payload?.service || 'offline'})`, async () => {
    const ctx = boot(payload, {fail:payload === null});
    await settle();
    assert.equal(ctx.$('tools-state').textContent, 'Desktop tools not connected');
    assert.equal(ctx.$('tools-recheck').disabled, false);
    assert.equal(ctx.$('tools-setup').open, true);
  });
}

test('castle page offers a user-initiated Mac connection without fetching localhost', async () => {
  const ctx = boot(null, {direct:true});
  await settle();
  assert.equal(ctx.calls(), 0);
  assert.match(ctx.$('tools-summary').textContent, /Mac/);
  assert.equal(ctx.$('tools-connect').hidden, false);
  assert.equal(ctx.$('tools-recheck').hidden, true);
  assert.equal(ctx.$('tools-start').hidden, false);
});

test('website startup gives honest setup guidance without claiming a connection', async () => {
  const ctx = boot(null, {direct:true});
  ctx.$('tools-start').dispatch('click');
  assert.equal(ctx.$('tools-state').textContent, 'Starting Mac tools…');
  assert.equal(ctx.$('tools-setup').open, true);
  assert.match(ctx.$('tools-summary').textContent, /If nothing opens/);
  assert.equal(ctx.calls(), 0);
});

test('an established device connection hides the unnecessary startup action', async () => {
  const ctx = boot({service:'castle-radio', protocol:1, ready:true, checks:[]},
    {direct:true, connected:true});
  await settle();
  assert.equal(ctx.$('tools-start').hidden, true);
  assert.equal(ctx.$('tools-state').textContent, 'Desktop tools connected');
});
