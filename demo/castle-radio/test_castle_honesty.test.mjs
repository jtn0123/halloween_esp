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

import {idle, lightsSent, linkContext, playingAt, poll, pollAt, settle, showContext, toolsContext} from './test_support.mjs';

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
