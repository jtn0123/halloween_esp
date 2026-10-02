/* What the page is allowed to claim.
 *
 *   node --test demo/castle-radio/test_castle_honesty.test.mjs
 *
 * Every test here is about a sentence or a counter the control room showed
 * that was not true: an offline castle that was answering, a song that was
 * over minutes ago, frames reported as sent that were never posted. The vm
 * contexts and the canned castle answers live in test_support.mjs. */
import assert from 'node:assert/strict';
import test from 'node:test';

import {EVENTS, eventLog, hung, idle, importsContext, lightsSent, linkContext, notFound, playingAt, poll, pollAt, settle, showContext, toolsContext} from './test_support.mjs';

const said = (calls, pattern) => calls.toasts.filter(t => pattern.test(t));

/* ---------------------------------------------------------------------- B1 */

test('B1: a TypeError in this page is a page fault, not a castle that went quiet', async () => {
  // current 1 while the castle plays track 0, so follow() calls load().
  const {ctx, calls} = linkContext({payload: playingAt(10), current: 1});
  await settle();
  ctx.$('output-target').value = 'castle';
  ctx.load = () => { throw new TypeError('renderQueue is not a function'); };
  for (const at of [12, 13, 14]) {await pollAt(ctx, at);}
  const h = ctx.window.castleLink.health();
  assert.equal(h.failures, 0, 'the castle answered every time: no strike belongs to it');
  assert.equal(h.missed_total, 0, 'and no poll was counted as missed');
  assert.doesNotMatch(ctx.$('castle-chip').textContent, /reconnecting/i);
  assert.doesNotMatch(ctx.$('current-detail').textContent, /reconnecting/i);
  assert.match(ctx.$('live-state').textContent, /^page error: renderQueue is not a function$/);
  assert.equal(calls.load.length, 0, 'the page fault is real: load() never got through');
  // The state on screen is a fresh one, so the transport keeps following it.
  assert.ok(ctx.window.castleLink.state(), 'a page fault must not blank the castle');
});

test('B1: the health line counts dropped polls only, and a page fault separately', async () => {
  const {ctx} = linkContext({payload: playingAt(10), current: 1});
  await settle();
  ctx.$('output-target').value = 'castle';
  ctx.load = () => { throw new TypeError('boom'); };
  await pollAt(ctx, 12);
  assert.match(ctx.window.castleLink.healthLine(), /missed 0/);
  ctx.fail = true;
  await poll(ctx, null);
  assert.match(ctx.window.castleLink.healthLine(), /missed 1 \(1 in a row\)/);
});

/* ---------------------------------------------------------------------- B3 */

test('B3: a throw while painting cannot stop the poll loop', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  ctx.fmt = () => { throw new TypeError('fmt is not a function'); };
  await assert.doesNotReject(pollAt(ctx, 12), 'refresh() must not reject into the scheduler');
  assert.match(ctx.$('live-state').textContent, /page error/);
  // And the scheduler re-arms itself even when the poll it ran threw.
  const armed = () => calls.timers.filter(t => t.ms === 1000).length;
  const before = armed();
  await calls.timers.filter(t => t.ms === 1000).at(-1).fn();
  assert.equal(armed(), before + 1, 'the loop asked for the next poll anyway');
});

test('B3: one panel that throws does not silence the others or the poll', async () => {
  const {ctx} = linkContext({payload: playingAt(10)});
  await settle();
  let second = 0;
  ctx.window.castleLink.subscribe(() => { throw new TypeError('bad panel'); });
  ctx.window.castleLink.subscribe(() => { second++; });
  await assert.doesNotReject(pollAt(ctx, 12));
  assert.ok(second >= 1, 'the panel after the broken one still got its update');
  assert.equal(ctx.window.castleLink.health().failures, 0, 'a broken panel is not a dropped poll');
});

/* ---------------------------------------------------------------------- B2 */

test('B2: a frame the next one overtook is coalesced, never counted as sent', async () => {
  const frames = [[0, '111111'], [0.05, '222222'], [0.06, '333333'], [0.07, '444444'], [1, '555555']];
  const {ctx, asked} = showContext(frames, {light_applied: 10, light_evicted: 0});
  await ctx.window.fetch('/radio/device/command', {method: 'POST',
    body: JSON.stringify({action: 'file', file: 'radio_a.mp3', key: 'radio_a'})});
  for (let i = 0; i < 200; i++) {await settle();}
  const posted = lightsSent(asked).filter(l => l.c !== 'off' && l.c !== 'show');
  const report = JSON.parse(await (await ctx.window.fetch('/radio/device')).text()).light_show;
  assert.equal(report.frames_sent, posted.length, 'sent is what went down the wire');
  assert.ok(report.frames_coalesced > 0, 'and the overtaken frame is still accounted for');
  assert.equal(report.frames_sent + report.frames_coalesced, frames.length);
});

test('B2: “landed of sent” reconciles, with the coalesced frames named', async () => {
  const {ctx} = linkContext({payload: playingAt(10)});
  await settle();
  const text = ctx.window.castleLink.framesText;
  assert.equal(text({frames_sent: 38, frames_coalesced: 2, frames_total: 40, frames_landed: 38, frames_evicted: 0}),
    '38 landed of 38 sent (2 coalesced)');
  assert.equal(text({frames_sent: 40, frames_coalesced: 0, frames_total: 40, frames_landed: 40, frames_evicted: 0}),
    '40 landed of 40 sent', 'nothing coalesced, nothing said');
});

/* ---------------------------------------------------------------------- B6 */

test('B6: a command that is still sending is not a command that failed', async () => {
  const {$} = toolsContext(async () => [], {command: async () => 'busy'});
  await $('bench-color-send').onclick();
  assert.match($('bench-result').textContent, /waiting/);
  assert.doesNotMatch($('bench-result').textContent, /failed/);
  assert.match($('bench-detail').textContent, /Still sending the previous command/);
});

test('B6: Play says why nothing happened when the previous command is in the air', async () => {
  const {ctx, calls} = linkContext({payload: idle()});
  await settle();
  ctx.$('output-target').value = 'castle';
  const first = ctx.window.castlePlayer.play();
  const second = ctx.window.castlePlayer.play();
  await Promise.all([first, second]);
  assert.equal(said(calls, /Still sending the previous command/).length, 1, calls.toasts.join(' | '));
  assert.equal(said(calls, /failed/i).length, 0, 'busy is not a failure');
});

/* --------------------------------------------------------------------- B10 */

test('B10: a castle that names no firmware reads as “—”, never as undefined', async () => {
  const nameless = idle();
  delete nameless.state.version;
  const {ctx} = linkContext({payload: nameless});
  await settle();
  assert.equal(ctx.$('castle-chip').textContent, 'Castle — · idle');
  assert.match(ctx.$('castle-chip').title, /^Firmware — · /);
  assert.match(ctx.$('live-health').textContent, /^Firmware — · SD ready/);
});

test('B10: a firmware field that is not a string is not printed as one', async () => {
  const odd = idle();
  odd.state.version = {major: 5};
  const {ctx} = linkContext({payload: odd});
  await settle();
  for (const id of ['castle-chip', 'live-health']) {
    assert.doesNotMatch(ctx.$(id).textContent, /\[object|undefined|NaN/, `${id}: ${ctx.$(id).textContent}`);
  }
});

/* --------------------------------------------------------------------- B11 */

test('B11: the chip puts the clock where an ellipsis cannot reach it', async () => {
  const {ctx} = linkContext({payload: playingAt(201)});
  await settle();
  const chip = ctx.$('castle-chip').textContent;
  assert.match(chip, /^Castle · \d+:\d\d/, chip);
  // One nowrap line with text-overflow:ellipsis, ~200 px of 9 px text on a
  // phone: whatever is last is what the porch never gets to read, and a
  // song title is longer than a clock.
  assert.ok(chip.indexOf(':') < chip.length - 3, chip);
});

/* --------------------------------------------------------------------- B12 */

test('B12: an answer with no motion block leaves the motion switch alone', async () => {
  const {ctx} = linkContext({payload: idle()});
  await settle();
  ctx.$('motion').checked = true;
  const quiet = idle();
  delete quiet.state.pir;
  await poll(ctx, quiet);
  assert.equal(ctx.$('motion').checked, true, 'unticking it is what the next change event sends back');
  assert.match(ctx.$('motion-note').textContent, /motion state unknown/);
});

/* ---------------------------------------------------------------------- B4 */

test('B4: a hidden tab calls a song over on the wall clock, not on a poll count', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  await ctx.window.castlePlayer.play();
  await pollAt(ctx, 12);
  calls.next = 0;
  ctx.document.hidden = true;
  ctx.now += 900;                       // still inside the 2 s of silence
  await poll(ctx, idle());
  assert.equal(calls.next, 0, 'a second of quiet is not the end of a song');
  ctx.now += 3100;                      // one hidden-tab poll, 4 s apart
  await poll(ctx, idle());
  assert.equal(calls.next, 1, 'and the queue moves on the same 2 s it would visible');
});

test('B4: the screen lock has two owners and only the last one drops it', async () => {
  const {ctx} = showContext([]);
  const direct = ctx.window.castleDirect;
  await direct.holdScreen('queue');
  assert.equal(ctx.locks.taken, 1);
  await direct.holdScreen('show');
  assert.equal(ctx.locks.taken, 1, 'one screen, one lock');
  direct.releaseScreen('show');
  await settle();
  assert.equal(ctx.locks.released, 0, 'the queue still needs the screen awake');
  direct.releaseScreen('queue');
  await settle();
  assert.equal(ctx.locks.released, 1);
});

test('B4: the queue holds the screen awake, installed scenes included', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  await ctx.window.castlePlayer.play();
  await pollAt(ctx, 12);
  assert.ok(calls.screen.includes('hold:queue'), calls.screen.join(' | '));
  calls.screen.length = 0;
  await ctx.window.castlePlayer.stop();
  await poll(ctx, idle());
  assert.ok(calls.screen.length > 0 && calls.screen.every(s => s === 'free:queue'),
    `a stopped castle lets the phone sleep (${calls.screen.join(' | ')})`);
});

/* ---------------------------------------------------------------------- B7 */

test('B7: a socket that never answers aborts at 8 s instead of pinning Connected', async () => {
  const {ctx, calls} = linkContext({payload: idle()});
  await settle();
  const real = ctx.fetch;
  ctx.fetch = (url, init) => new Promise((_, reject) => {
    calls.fetch.push(url);
    init.signal.addEventListener('abort', () => {
      const error = new Error('aborted'); error.name = 'AbortError'; reject(error);
    });
  });
  const hung = ctx.window.castleLink.refresh();
  await settle();
  const guard = calls.timers.filter(t => t.ms === 8000).at(-1);
  assert.ok(guard, 'the request carries a guard of its own');
  guard.fn();
  await hung;
  await settle();
  assert.match(ctx.$('live-state').textContent, /timed out/);
  assert.equal(ctx.window.castleLink.health().failures, 1, 'a hung socket is a dropped poll');
  // And the next poll goes out: the abort cleared inflight.
  ctx.fetch = real;
  await poll(ctx, idle());
  assert.equal(ctx.window.castleLink.health().failures, 0);
});

/* ---------------------------------------------------------------------- B9 */

test('B9: a listing request that rejects still gets Play a word', async () => {
  const imported = {id: 0, key: 'radio_a', file: '', kind: 'song', duration: 30, title: 'Imported'};
  const {ctx, calls} = linkContext({payload: idle(), tracks: [imported], queue: []});
  await settle();
  ctx.$('output-target').value = 'castle';
  ctx.window.remoteLibrary.ensure = async () => { throw new Error('Castle listing failed'); };
  await assert.doesNotReject(ctx.window.castlePlayer.play());
  assert.equal(said(calls, /press Play again/).length, 1, calls.toasts.join(' | '));
  assert.equal(calls.retries, 1, 'and another listing is asked for');
});

test('B9: a restart after a reboot that cannot start says so', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10, 400)});
  await settle();
  ctx.$('output-target').value = 'castle';
  await ctx.window.castlePlayer.play();
  await pollAt(ctx, 30, 420);
  ctx.window.castlePlayer.play = async () => { throw new Error('nothing to play'); };
  await poll(ctx, idle(3, ''));           // the castle reboots, still booting
  await poll(ctx, idle(6));               // its shows are loaded: resume
  assert.equal(said(calls, /Could not restart the song · nothing to play/).length, 1,
    calls.toasts.join(' | '));
});

/* ---------------------------------------------------------------------- B5 */

test('B5: the reason the generated lights stopped is said once, not swallowed', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  const broken = playingAt(12);
  broken.light_show = {active: false, track: 'radio_a.mp3', error: 'Castle answered 500'};
  await poll(ctx, broken);
  assert.equal(said(calls, /Lights stopped: Castle answered 500/).length, 1, calls.toasts.join(' | '));
  assert.match(ctx.$('current-detail').textContent, /lights stopped: Castle answered 500 · press Play to restart/);
  assert.match(ctx.$('live-state').textContent, /lights stopped: Castle answered 500/);
  await poll(ctx, broken);
  assert.equal(said(calls, /Lights stopped/).length, 1, 'said once, not once a second');
});

/* ---------------------------------------------------------------------- B8 */

test('B8: a castle track this library cannot name blanks the length and the bar', async () => {
  const {ctx} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  await pollAt(ctx, 10);
  assert.equal(ctx.$('duration').textContent, '3:13', 'a song this page knows keeps its length');
  const mystery = playingAt(500);
  mystery.state.scene = 'stop';
  mystery.state.track = 'someone_elses.mp3';
  await poll(ctx, mystery);
  assert.equal(ctx.$('duration').textContent, '—', 'nothing here knows how long that runs');
  assert.equal(ctx.$('seek').value, 0, 'and the bar is not pinned at 100%');
  assert.match(ctx.$('elapsed').textContent, /^8:2\d$/, 'the castle clock runs on, unclamped');
});

/* ------------------------------------------------------------- B72/B73 */

/* L7/L9 (v5.62). The runbook has told the operator to "look at heap in the
   panel" since the panel was the eInk wing; the Castle Radio page never had
   one, and /api/status's heap_free_kb would not have answered the question
   anyway — it is what is free NOW, after the allocation that failed was
   handed back. This is the row that carries the low-water mark. */
test('B72: the bench shows heap, the card and the season beside the events', async () => {
  const health = {boots: 41, crashes: 2, last_reset: 'PANIC', was_crash: true,
    sd_read_errors: 3, heap_min_kb: 9, sd_last_error: 'scenes/03_storm.mp3@81920'};
  const state = {heap_free_kb: 84, sd_free_kb: 12 * 1024, sd_mounted: true, rssi: -71};
  const {row} = await eventLog(async () => EVENTS, {health, state});
  assert.match(row, /41 boots · 2 crashes/);
  assert.match(row, /last reset PANIC/);
  assert.match(row, /heap now 84 KB · lowest 9 KB/, 'the low-water mark, not the free mark');
  assert.match(row, /SD 12 MB free/);
  assert.match(row, /3 card read errors · last scenes\/03_storm\.mp3@81920/);
  assert.match(row, /signal -71 dBm/);
  /* A firmware with no /api/health, or a page with no bridge to ask it
     through, must leave the row empty and still print the events. */
  const old = await eventLog(async () => EVENTS);
  assert.equal(old.row, '');
  assert.match(old.log, /play  radio_a\.mp3/);
});

/* L2 (v5.62). The ring only ever stamps uptime, and uptime cannot be lined
   up against the one thing the operator remembers. `epoch` is the base. */
test('B73: event times read as clock times once the castle has a clock', async () => {
  const state = {epoch: 1789700000, uptime_s: 3600};
  const {log} = await eventLog(async () => EVENTS, {state});
  const times = log.split('\n').map(line => line.slice(0, 8));
  assert.ok(times.every(t => /^\d\d:\d\d:\d\d$/.test(t)), `wall clock, got ${times}`);
  /* Ten seconds of ring is ten seconds of clock, whatever the timezone. */
  const [first, last] = [times[0], times[2]].map(t => t.split(':').map(Number));
  assert.equal((last[2] - first[2] + 60) % 60, 10);
  /* Before SNTP answers, epoch is 0 and the relative stamps come back. */
  const blind = await eventLog(async () => EVENTS, {state: {epoch: 0, uptime_s: 3600}});
  assert.match(blind.log, /\+00:00\.0  stop/);
});

/* ---------------------------------------------- grade report 2026-09-17 pm C6 */

test('C6: a request that never answers ends, and the 2 s poll runs again', async () => {
  const {ctx, asked, timeouts, busy, fireAll} = importsContext(hung);
  await settle();
  // The load-time refresh is in flight and holding the poll gate.
  assert.equal(busy(), true);
  assert.deepEqual(asked, ['/radio/jobs', '/radio/library']);
  assert.deepEqual(timeouts.map(t => t.ms), [6000, 6000], 'a poll is given seconds, not for ever');

  fireAll();
  await settle();
  assert.equal(busy(), false, 'the gate is open again, so setInterval(refresh) still works');
  assert.match(ctx.$('service-status').textContent, /Import service unavailable/);

  const next = ctx.refresh();
  await settle();
  assert.equal(asked.length, 4, 'the next tick actually polls');
  fireAll();
  await next;
  assert.equal(busy(), false);
});

test('C6: a hung upload is given its own budget, not the poll budget', async () => {
  const {ctx, timeouts} = importsContext(hung);
  await settle();
  timeouts.length = 0;
  ctx.request('/radio/import', {method: 'POST', body: 'x'}, 15 * 60 * 1000).catch(() => {});
  ctx.request('/radio/retry', {method: 'POST'}).catch(() => {});
  assert.deepEqual(timeouts.map(t => t.ms), [15 * 60 * 1000, 15000]);
});

test('C6: a 404 that is not JSON reads as the page it is, not as a parse error', async () => {
  const {ctx} = importsContext(notFound);
  await settle();
  await assert.rejects(() => ctx.request('/radio/waveform/radio_a'), error => {
    assert.match(error.message, /404: .*Not Found/, 'the response text is the report');
    assert.doesNotMatch(error.message, /JSON|Unexpected token/, 'r.ok is read before the body');
    return true;
  });
});

test('C6: a JSON failure still speaks in the service’s own words', async () => {
  const {ctx} = importsContext(() => ({ok: false, status: 429,
    text: async () => JSON.stringify({error: 'Too many songs are being prepared.'})}));
  await settle();
  await assert.rejects(() => ctx.request('/radio/import', {method: 'POST'}),
    /Too many songs are being prepared\./);
});

test('C6: the timeout message names the budget it gave up after', async () => {
  const {ctx, timeouts} = importsContext(hung);
  await settle();
  const call = ctx.request('/radio/jobs');
  timeouts.at(-1).fire();
  await assert.rejects(() => call, /did not answer within 6s/);
});

/* ------------------------------------------------------- the import queue */

const served = (jobs, rows = []) => url => ({ok: true, status: 200,
  json: async () => (url === '/radio/jobs' ? jobs : rows)});

test('a job is shown by name — the link itself while it downloads, never "Linked song"', async () => {
  const jobs = [
    {id: 'radio_a', title: '', phase: 'Ready in demo', done: true, finished_at: 9, started_at: 1},
    {id: 'radio_b', title: '', phase: 'Downloading audio', done: false, percent: 12,
      source: 'https://www.youtube.com/watch?v=abc'},
    {id: 'radio_c', title: 'Thriller', phase: 'Queued', done: false, source: 'https://e.com/t'},
    {id: 'radio_d', title: '', phase: 'Queued', done: false, source: '/tmp/radio_d.mp3',
      source_name: 'spooky.mp3'},
  ];
  const {ctx} = importsContext(served(jobs, [{key: 'radio_a', title: 'A'}]));
  await settle();
  const html = ctx.$('import-jobs').innerHTML;
  assert.doesNotMatch(html, /Linked song/);
  assert.match(html, /<b>A<\/b>/, 'a finished job with no title borrows the library’s');
  assert.match(html, /youtube\.com\/watch\?v=abc/, 'a link still downloading is named by its link');
  // What runs first is first: preparing, then the line in order, then finished.
  const order = ['youtube.com', 'Thriller', 'spooky.mp3', '<b>A</b>'].map(text => html.indexOf(text));
  assert.deepEqual(order, order.slice().sort((a, b) => a - b));
  assert.match(html, /Thriller<\/b><span>Next up/);
  assert.match(html, /spooky\.mp3<\/b><span>Number 2 in line/);
  assert.match(html, /1 preparing · 2 waiting · 1 finished/);
  assert.equal((html.match(/data-cancel=/g) || []).length, 3, 'everything unfinished can be cancelled');
});

test('clearing finished jobs keeps failures, and a retried job comes back when it finishes again', async () => {
  const jobs = [
    {id: 'radio_a', title: 'Fine', phase: 'Ready in demo', done: true, finished_at: 5},
    {id: 'radio_b', title: 'Broken', phase: 'Import failed', done: true, finished_at: 6, error: 'no audio'},
  ];
  const {ctx} = importsContext(served(jobs));
  await settle();
  await ctx.$('import-jobs').onclick({target: {closest: query => (query === '[data-clear-finished]' ? {} : null)}});
  let html = ctx.$('import-jobs').innerHTML;
  assert.doesNotMatch(html, /Fine/);
  assert.match(html, /Broken/, 'a failure stays until it is dismissed or retried');
  jobs[0].finished_at = 50;
  await ctx.refresh();
  html = ctx.$('import-jobs').innerHTML;
  assert.match(html, /Fine/, 'hidden by id AND finish time, so the second result is shown');
});

test('several links are queued one by one, and the ones refused stay in the box with the reason', async () => {
  const posted = [];
  const {ctx} = importsContext((url, init) => {
    if (init?.method !== 'POST') {return served([])(url);}
    const link = JSON.parse(init.body).url;
    posted.push(link);
    return link.endsWith('/dup')
      ? {ok: false, status: 400, text: async () => JSON.stringify({error: 'That link is already in the queue.'})}
      : {ok: true, status: 202, json: async () => ({})};
  });
  await settle();
  ctx.$('import-file').files = [];
  ctx.$('import-url').value = 'https://e.com/1\nhttps://e.com/dup\n\nhttps://e.com/1  https://e.com/2';
  await ctx.$('import-form').onsubmit({preventDefault() {}});
  assert.deepEqual(posted, ['https://e.com/1', 'https://e.com/dup', 'https://e.com/2']);
  assert.equal(ctx.$('import-url').value, 'https://e.com/dup');
  assert.match(ctx.$('import-message').textContent, /Queued 2 songs/);
  assert.match(ctx.$('import-message').textContent, /Not queued · https:\/\/e\.com\/dup — That link is already in the queue\./);
});
