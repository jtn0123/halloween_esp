/* Update the downloader (downloader.js), and a failed import as the owner
   reads it in the queue (imports.js): one sentence, the tools' own words
   behind Details, and the fix as a button when the downloader is the cause. */
import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import {page, read, settle, importsContext} from './test_support.mjs';

const OLD = 'The downloader may be out of date — Update the downloader, then try the link again.';

function boot(answers, {direct = false, connected = false} = {}) {
  const {$} = page();
  const asked = [];
  const ctx = {
    window: {castleDirect: direct ? {} : undefined, castleDesktop: {connected}, addEventListener() {}},
    document: {getElementById: $},
    AbortSignal: {timeout: () => undefined},
    setTimeout: () => 0, clearTimeout() {},
    fetch: async (url, init) => {
      asked.push([url, init?.method || 'GET', init?.body]);
      const [status, body] = answers.shift() || [200, {installed: true, version: '2026.10.01', managed: true, update: {phase: 'done', changed: true, version: '2026.10.01'}}];
      return {ok: status < 400, status, json: async () => body};
    },
  };
  vm.runInNewContext(read('downloader.js'), ctx);
  return {$, asked, ctx};
}

test('the state says what is installed, and an old copy can be updated', async () => {
  const {$, asked} = boot([[200, {installed: true, version: '2026.09.01', managed: true, update: {phase: 'idle'}}]]);
  await settle();
  assert.equal($('downloader-state').textContent, 'Song downloader · version 2026.09.01');
  assert.equal($('downloader-update').disabled, false);
  assert.equal($('downloader-detail').hidden, true);
  await $('downloader-update').onclick();
  const [url, method, body] = asked[1];
  assert.deepEqual([url, method, body], ['/radio/downloader/update', 'POST', '{}']);
  assert.match($('downloader-note').textContent, /Updated to 2026\.10\.01/);
});

test('a missing downloader asks for the update that installs it', async () => {
  const {$} = boot([[200, {installed: false, version: null, managed: false, update: {phase: 'idle'}}]]);
  await settle();
  assert.equal($('downloader-state').textContent, 'Song downloader · not installed yet');
  assert.match($('downloader-note').textContent, /Update the downloader to install it/);
});

test('queued behind an import, the button waits with it', async () => {
  const {$} = boot([[200, {installed: true, version: '1', update: {phase: 'waiting'}}]]);
  await settle();
  assert.equal($('downloader-update').disabled, true);
  assert.match($('downloader-note').textContent, /Waiting for the import in progress/);
});

test('a failed update is one sentence, its own words behind Details', async () => {
  const update = {phase: 'failed', error: 'The downloaded update did not match its published checksum, so it was not installed — try again later.', error_detail: 'checksum mismatch for yt-dlp_macos'};
  const {$} = boot([[200, {installed: true, version: '1', update}]]);
  await settle();
  assert.equal($('downloader-note').textContent, update.error);
  assert.equal($('downloader-detail').hidden, false);
  assert.equal($('downloader-log').textContent, update.error_detail);
  assert.equal($('downloader-update').disabled, false);
});

test("on the castle's page it is hidden until the Mac tools are connected", async () => {
  const off = boot([], {direct: true});
  await settle();
  assert.equal(off.$('downloader').hidden, true);
  assert.deepEqual(off.asked, []);
  const on = boot([], {direct: true, connected: true});
  await settle();
  assert.equal(on.$('downloader').hidden, false);
  assert.equal(on.asked[0][0], '/radio/downloader');
});

test('the companion relays the downloader routes and nothing more', () => {
  const source = read('companion.js');
  assert.match(source, /\['GET', \/\^\\\/radio\\\/downloader\$\/\]/);
  assert.match(source, /\['POST', \/\^\\\/radio\\\/downloader\\\/update\$\/\]/);
  assert.match(read('device-helper.js'), /downloader\(\?:\\\/update\)\?\$/);
});

const served = jobs => url => ({ok: true, status: 200, json: async () => (url === '/radio/jobs' ? jobs : [])});

test('a failed link reads as its sentence, with Details and the fix', async () => {
  const jobs = [{id: 'radio_x', title: 'Song', phase: 'Import failed', done: true, finished_at: 3,
    error: OLD, error_detail: '    ERROR: [youtube] x: <nsig> extraction failed', action: 'update-downloader'}];
  const {ctx} = importsContext(served(jobs));
  let pressed = 0;
  ctx.window.castleDownloader = {available: () => true, update: async () => { pressed++; }};
  await settle();
  ctx.renderJobs();
  const html = ctx.$('import-jobs').innerHTML;
  assert.match(html, /<p class="import-error">The downloader may be out of date — Update the downloader/);
  assert.match(html, /<details class="import-detail" data-detail="radio_x"><summary>Details<\/summary><pre>    ERROR: \[youtube\] x: &lt;nsig&gt; extraction failed<\/pre><\/details>/);
  assert.match(html, /<button data-update-downloader>Update the downloader<\/button>/);
  await ctx.$('import-jobs').onclick({target: {closest: q => (q === '[data-update-downloader]' ? {} : null)}});
  assert.equal(pressed, 1);
});

test('no fix button where the downloader cannot be reached, and none for other failures', async () => {
  const jobs = [
    {id: 'radio_a', title: 'A', phase: 'Import failed', done: true, finished_at: 1, error: OLD, action: 'update-downloader'},
    {id: 'radio_b', title: 'B', phase: 'Import failed', done: true, finished_at: 2, error: 'That video is private — try a different link.', action: null},
  ];
  const {ctx} = importsContext(served(jobs));
  ctx.window.castleDownloader = {available: () => false, update() {}};
  await settle();
  ctx.renderJobs();
  assert.doesNotMatch(ctx.$('import-jobs').innerHTML, /data-update-downloader/);
  ctx.window.castleDownloader.available = () => true;
  ctx.renderJobs();
  assert.equal((ctx.$('import-jobs').innerHTML.match(/data-update-downloader/g) || []).length, 1);
});

test('a cancelled import reads as cancelled, never as a failure', async () => {
  const jobs = [{id: 'radio_c', title: 'C', phase: 'Cancelled', done: true, finished_at: 4, error: null}];
  const {ctx} = importsContext(served(jobs));
  await settle();
  const html = ctx.$('import-jobs').innerHTML;
  assert.match(html, /<span>Cancelled<\/span>/);
  assert.doesNotMatch(html, /import-error|Details/);
});
