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

// docs/PRODUCTION-TODO.md §4.2: the Mac-only setup steps (the .command files,
// the ♜ menu) are offered where the computer says it has them, and only there.
for (const [platform, startup, hidden, install] of [
  ['darwin', 'Enable Website Startup.command', false, 'sh installer/install.sh'],
  ['win32', null, true, 'installer\\install.cmd'],
  ['linux', null, true, 'sh installer/install.sh --from-source'],
]) {
  test(`setup offers website startup only where it exists (${platform})`, async () => {
    const ctx = boot({service:'castle-radio', protocol:1, ready:true, checks:[],
      install_command:install, website_startup:startup});
    await settle();
    assert.equal(ctx.$('tools-mac').hidden, hidden);
    assert.equal(ctx.$('tools-install').textContent, install);
    assert.equal(ctx.$('tools-installer').hidden, false, 'a checkout repairs with the installer');
    assert.equal(ctx.$('tools-repair').hidden, true);
  });
}

test('inside the desktop app the card points at its own Repair, not a script', async () => {
  const words = 'To repair Castle Tools, choose Repair Castle Tools… from the ♜ in the menu bar.';
  const ctx = boot({service:'castle-radio', protocol:1, ready:false, checks:[],
    install_command:null, website_startup:null, repair:words, app_version:'Castle Tools v1.4.0'});
  await settle();
  assert.equal(ctx.$('tools-repair').textContent, words);
  assert.equal(ctx.$('tools-repair').hidden, false);
  assert.equal(ctx.$('tools-installer').hidden, true, 'no installer folder to run a command in');
  assert.equal(ctx.$('tools-mac').hidden, true, 'and no website-startup double-click');
});

test('an older helper that never says keeps the Mac steps visible', async () => {
  const ctx = boot({service:'castle-radio', protocol:1, ready:true, checks:[]});
  await settle();
  assert.equal(ctx.$('tools-mac').hidden, false);
  assert.equal(ctx.$('tools-version').hidden, true, 'no version line it cannot fill');
});

test('the tools card names the release this copy came from', async () => {
  const ctx = boot({service:'castle-radio', protocol:1, ready:true, checks:[],
    app_version:'Castle Tools v1.4.0'});
  await settle();
  assert.equal(ctx.$('tools-version').textContent, 'Castle Tools v1.4.0');
  assert.equal(ctx.$('tools-version').hidden, false);
});
