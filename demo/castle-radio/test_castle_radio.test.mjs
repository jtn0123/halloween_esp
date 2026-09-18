/* Behaviour tests for the castle-facing browser code.
 *
 *   node --test demo/castle-radio/test_castle_radio.test.mjs
 *
 * The page stub, the vm contexts and the canned castle answers live in
 * test_support.mjs; everything here is an assertion about what the castle is
 * actually sent when the control room is driving it. */
import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';

import {EVENTS, directContext, element, eventLog, fmt, healthRun, idle, landedShow, linkContext, page, playingAt, poll, pollAt, read, runFrames, settle, settlingOn, showContext, wordings} from './test_support.mjs';

/* ------------------------------------------------------------------- B01/B20 */

test('B01: a looping song advances the queue once per loop cycle, not once per poll', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  calls.next = 0;
  // Three polls inside the same finished cycle: 193.36 s song, threshold 192.86.
  for (const at of [193.0, 193.5, 200.0]) {await pollAt(ctx, at);}
  assert.equal(calls.next, 1, 'one advance for one finished loop cycle');
});

test('B20: a 4 s hidden-tab poll that lands past the window still advances, once', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  ctx.document.hidden = true;
  calls.next = 0;
  await pollAt(ctx, 196.9);   // 3.5 s past the end of cycle one
  assert.equal(calls.next, 1);
  await pollAt(ctx, 386.0);   // still inside cycle two
  assert.equal(calls.next, 1);
  await pollAt(ctx, 389.0);   // cycle two is over
  assert.equal(calls.next, 2);
});

/* ---------------------------------------------------------------------- B02 */

test('B02: the Your-castle stop ends the evening playlist, not just the scene', async () => {
  const {ctx, calls} = linkContext({payload: idle()});
  await settle();
  calls.commands.length = 0;
  ctx.$('live-show-stop').onclick();
  await settle();
  assert.equal(JSON.stringify(calls.commands[0]), '{"action":"show/stop"}');
});

/* ---------------------------------------------------------------------- B53 */

test('B53: starting the installed playlist parks the radio queue', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  await ctx.window.castlePlayer.play();        // the radio queue is live here
  await pollAt(ctx, 10);
  calls.commands.length = 0; calls.next = 0;
  ctx.$('live-show-start').onclick();
  await settle();
  assert.equal(JSON.stringify(calls.commands[0]), '{"action":"show/start"}');
  ctx.payload = idle();                        // the dark gap between scenes
  for (let i = 0; i < 4; i++) { await ctx.window.castleLink.refresh(); await settle(); }
  assert.equal(calls.next, 0, 'playlist gaps must not advance the radio queue');
});

/* ---------------------------------------------------------------------- B08 */

test('B08: a running sync throttles the status poll instead of stopping it', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10), syncing: true});
  await settle();
  assert.equal(calls.fetch.length, 1, 'the first poll went out during the sync');
  await ctx.window.castleLink.refresh(); await settle();
  assert.equal(calls.fetch.length, 1, 'a sync throttles the poll');
  ctx.now += 2100;
  await ctx.window.castleLink.refresh(); await settle();
  assert.equal(calls.fetch.length, 2, 'and the poll comes back on its own');
  ctx.now += 2100;
  await ctx.window.castleLink.refresh(); await settle();
  assert.equal(calls.fetch.length, 3, 'for as long as the transfer lasts');
});

/* ---------------------------------------------------------------------- B37 */

test('B37: a listing that has not answered yet does not open the sync dialog', async () => {
  const imported = {id: 2, key: 'radio_abc', file: '', kind: 'song', duration: 100, title: 'Imported'};
  const {ctx, calls} = linkContext({payload: idle(), current: 2, inventory: null,
    tracks: [{id: 0, file: '09_citizens.mp3', kind: 'song', duration: 193},
      {id: 1, file: '01_vigil.mp3', kind: 'scene', duration: 60}, imported]});
  await settle();
  ctx.$('output-target').value = 'castle';
  calls.commands.length = 0;
  await ctx.window.castlePlayer.play();
  assert.deepEqual(calls.offers, [], 'no "not synced" dialog for an unanswered listing');
  assert.equal(calls.retries, 1, 'the listing is retried');
  assert.match(calls.toasts.at(-1), /listing slow/i);
  assert.deepEqual(calls.commands, []);
});

test('B37: an inventory that does answer still offers a sync for a missing file', async () => {
  const imported = {id: 2, key: 'radio_abc', file: '', kind: 'song', duration: 100, title: 'Imported'};
  const {ctx, calls} = linkContext({payload: idle(), current: 2, inventory: {tracks: {}},
    tracks: [{id: 0, file: '09_citizens.mp3', kind: 'song'}, {id: 1, file: '01_vigil.mp3', kind: 'scene'}, imported]});
  await settle();
  ctx.$('output-target').value = 'castle';
  await ctx.window.castlePlayer.play();
  assert.deepEqual(calls.offers, [imported]);
});

/* ---------------------------------------------------------------------- B44 */

test('B44: previous restarts the castle track when its own clock is past 3 s', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  await pollAt(ctx, 12);
  calls.commands.length = 0;
  assert.equal(ctx.window.castlePlayer.previous(), true);
  await settle();
  assert.equal(JSON.stringify(calls.commands[0]), '{"action":"scene","scene":"citizens"}', 'the same song again');
});

test('B44: previous inside the first 3 s leaves the history walk alone', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  await pollAt(ctx, 1.2);
  calls.commands.length = 0;
  assert.equal(ctx.window.castlePlayer.previous(), false);
  assert.deepEqual(calls.commands, []);
});

/* ---------------------------------------------------------------------- B54 */

test('B54: owns() is true only for the song this page started on the castle', async () => {
  const {ctx} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  await pollAt(ctx, 10);
  // Nobody pressed Play here yet: the castle is playing something of its own.
  assert.equal(ctx.window.castlePlayer.owns(ctx.tracks[0]), false);
  await ctx.window.castlePlayer.play();
  await pollAt(ctx, 10);
  assert.equal(ctx.window.castlePlayer.owns(ctx.tracks[0]), true);
  assert.equal(ctx.window.castlePlayer.owns(ctx.tracks[1]), false);
});

test('B54: a library refresh only stops the castle for a song this page started', () => {
  const source = read('imports.js');
  const start = source.indexOf('function integrate(rows){');
  const end = source.indexOf('renderImports();}', start) + 'renderImports();}'.length;
  assert.ok(start >= 0 && end > start, 'integrate() is still one function');
  const run = owns => {
    const {$} = page();
    const calls = {stops: 0, loads: []};
    const ctx = {
      $, console, imported: new Map(), current: 0,
      tracks: [{id: 0, key: 'radio_gone', title: 'Gone'}, {id: 1, key: 'radio_keep', title: 'Keep'}],
      queue: [1], history: [],
      stop() { calls.stops++; }, load(id) { calls.loads.push(id); },
      renderTracks() {}, renderQueue() {}, renderImports() {},
      window: {castlePlayer: {active: () => true, owns: () => owns}},
    };
    vm.createContext(ctx);
    vm.runInContext(`${source.slice(start, end)}\nintegrate([{key:'radio_keep',title:'Keep'}]);`, ctx);
    return calls;
  };
  assert.equal(run(true).stops, 1, 'our own song is stopped before it is replaced');
  assert.equal(run(false).stops, 0, 'someone else’s song on the porch is left alone');
  assert.deepEqual(run(false).loads, [1]);
});

/* ------------------------------------------------------------------ B36/B12 */

function benchContext() {
  const {$} = page();
  const calls = {commands: [], subscribed: []};
  const bench = element('bench');
  bench.querySelectorAll = () => [];
  const grid = element('grid');
  grid.parentNode = {insertBefore() {}};
  const device = element('device');
  device.querySelector = () => grid;
  const ctx = {
    console, toast() {}, AbortSignal,
    $: id => (id === 'device' ? device : $(id)),
    document: {createElement: () => bench, addEventListener() {}},
    fetch() { throw new Error('the bench must not open its own request'); },
    window: {castleLink: {
      command: async body => { calls.commands.push(body); return true; },
      lastError: () => '',
      subscribe: fn => calls.subscribed.push(fn),
    }},
  };
  vm.createContext(ctx);
  vm.runInContext(read('device-tools.js'), ctx, {filename: 'device-tools.js'});
  return {ctx, calls, bench};
}

test('B36: bench commands go through castleLink so busy and the epoch apply', async () => {
  const {ctx, calls} = benchContext();
  await ctx.$('bench-audio-stop').onclick();
  assert.equal(JSON.stringify(calls.commands), '[{"action":"stop"}]', 'no private fetch of its own');
});

test('B12: the bench stop does not claim the installed playlist is over', async () => {
  const {ctx} = benchContext();
  await ctx.$('bench-audio-stop').onclick();
  const detail = ctx.$('bench-detail').textContent;
  assert.match(detail, /does not end the installed playlist/i);
});

/* ---------------------------------------------------------------------- B47 */

test('B47: load() gives the audio element no source while the castle is the output', () => {
  const source = read('app.js');
  const line = source.split('\n').find(l => l.startsWith('function load(id){'));
  assert.ok(line, 'load() is still one line of app.js');
  const run = output => {
    const {$} = page();
    $('output-target').value = output;
    const calls = {src: [], removed: 0, loads: 0};
    const ctx = {
      $, console, current: -1, fmt, updatePlayer() {},
      tracks: [{id: 0, file: '09_citizens.mp3', duration: 193}],
      audio: {
        set src(value) { calls.src.push(value); },
        get src() { return calls.src.at(-1) || ''; },
        removeAttribute(name) { if (name === 'src') {calls.removed++;} },
        load() { calls.loads++; },
      },
      window: {radioLayer: '', dispatchEvent() {}},
      CustomEvent: class { constructor(type) { this.type = type; } },
    };
    vm.createContext(ctx);
    vm.runInContext(`${source.slice(source.indexOf("let audioSourceEpoch"), source.indexOf("function load(id)"))}\n${line}\nload(0);`, ctx);
    return calls;
  };
  const castle = run('castle');
  assert.deepEqual(castle.src, [], 'the control-room laptop fetches nothing either');
  assert.equal(castle.removed, 1);
  assert.deepEqual(run('computer').src, ['media/09_citizens.mp3']);
});

/* ---------------------------------------------------------------------- B51 */


test('B51: a command still settling reads as starting, and offers no stop', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  await pollAt(ctx, 10);
  ctx.payload = settlingOn('vigil');
  await ctx.window.castleLink.refresh(); await settle();
  assert.equal(ctx.window.castlePlayer.playing(), false, 'settling is not playing');
  assert.match(ctx.$('hero-play').textContent, /starting/i);
  assert.match(ctx.$('preview-toggle').textContent, /starting/i);
  assert.equal(ctx.$('toggle').disabled, true, 'nothing to press while it starts');
  calls.commands.length = 0; calls.next = 0;
  ctx.window.castlePlayer.toggle();
  await settle();
  assert.deepEqual(calls.commands, [], 'the toggle must not stop the scene being left');
  assert.equal(calls.next, 0, 'and a settling poll is not the end of a song');
});
/* ---------------------------------------------------------------------- B60 */


test('B60: the direct frame clock waits for the speaker, like the Python bridge', async () => {
  const row = {key: 'radio_a', filename: 'radio_a.mp3', bytes: 9, duration: 30,
    frames: [[0, 'towerL:a832ff@50']]};
  // No position_ms: this castle never reports a clock, so the seeded estimate
  // is what every frame is sent on — 0.24 s of command latency plus the half
  // second the speaker takes to run (SPEAKER_START_S).
  const {ctx, delays} = directContext({
    '/api/status': {version: '5.55', scene: 'stop', track: 'radio_a.mp3', playing: true, scenes: 'stop'},
    '/api/files': [{name: 'radio_a.mp3', size: 9, dir: false}],
    '/api/light': {ok: true},
    '/api/play': {ok: true},
  }, [row]);
  await ctx.window.fetch('/radio/device/command', {method: 'POST',
    body: JSON.stringify({action: 'file', file: 'radio_a.mp3', key: 'radio_a'})});
  for (let i = 0; i < 40; i++) {await settle();}
  assert.ok(delays.some(ms => Math.abs(ms - 740) < 1),
    `the first frame waits 0.74 s, not 0.24 s (waits seen: ${delays})`);
  assert.ok(!delays.some(ms => Math.abs(ms - 240) < 1), 'no speaker-blind 0.24 s seed');
});

/* ---------------------------------------------------------------------- B61 */

test('B61: one dropped poll keeps the castle on screen; three go offline', async () => {
  const {ctx} = linkContext({payload: playingAt(10)});
  await settle();
  ctx.$('output-target').value = 'castle';
  await pollAt(ctx, 30);
  ctx.fail = true;
  await poll(ctx, null);
  assert.ok(ctx.window.castleLink.state(), 'a single failure is a hiccup, not an offline castle');
  assert.notEqual(ctx.$('elapsed').textContent, '0:00', 'and it does not zero the clock');
  assert.match(ctx.$('castle-chip').textContent, /reconnecting/i);
  assert.match(ctx.$('live-state').textContent, /reconnecting/i);
  await poll(ctx, null);
  assert.ok(ctx.window.castleLink.state(), 'two is still a hiccup');
  await poll(ctx, null);
  assert.equal(ctx.window.castleLink.state(), null, 'three strikes is offline');
  assert.match(ctx.$('castle-chip').textContent, /offline/i);
  ctx.fail = false;
  await pollAt(ctx, 34);
  assert.ok(ctx.window.castleLink.state(), 'and one good poll is back online');
  assert.doesNotMatch(ctx.$('castle-chip').textContent, /reconnecting/i);
});

/* ---------------------------------------------------------------------- B62 */

test('B62: the same looping scene queued again still advances', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10), queue: [1, 1]});
  await settle();
  ctx.$('output-target').value = 'castle';
  calls.next = 0;
  await pollAt(ctx, 193.5);                    // the first play finishes a cycle
  assert.equal(calls.next, 1);
  await pollAt(ctx, 2);                        // the same scene, started over
  assert.equal(calls.next, 1, 'a restart is not itself an advance');
  await pollAt(ctx, 193.5);
  assert.equal(calls.next, 2, 'the guard from the first play must not outlive it');
});

/* ---------------------------------------------------------------------- B63 */

const IMPORTED = {id: 2, key: 'radio_x', file: '', kind: 'song', duration: 100, title: 'Imported'};
const withImport = () => [
  {id: 0, file: '09_citizens.mp3', kind: 'song', duration: 193, title: 'Citizens'},
  {id: 1, file: '01_vigil.mp3', kind: 'scene', duration: 60, title: 'Vigil'},
  IMPORTED,
];

test('B63: a listing that has not answered does not throw the queue away', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10), queue: [2], tracks: withImport(), known: false});
  await settle();
  ctx.$('output-target').value = 'castle';
  calls.next = 0;
  await pollAt(ctx, 193.5);
  assert.deepEqual(ctx.queue, [2], 'the song is still queued');
  assert.equal(calls.next, 0, 'and nothing was advanced past');
  assert.equal(calls.retries, 1, 'the listing is retried instead');
  assert.match(calls.toasts.at(-1), /listing slow/i);
  // The listing answers: the queue moves on its own, without a second end.
  ctx.known = true; ctx.item = {audio: true, filename: 'radio_x.mp3'};
  await pollAt(ctx, 200);
  assert.equal(calls.next, 1);
  assert.deepEqual(ctx.queue, [2], 'nothing was discarded on the way');
});

test('B63: a song that really is missing is still skipped', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10), queue: [2], tracks: withImport(), known: true});
  await settle();
  ctx.$('output-target').value = 'castle';
  calls.next = 0;
  await pollAt(ctx, 193.5);
  assert.deepEqual(ctx.queue, []);
  assert.ok(calls.toasts.some(t => /not synced to the castle/i.test(t)), calls.toasts.join(' | '));
});

/* ---------------------------------------------------------------------- B65 */

test('B65: an uptime that goes backwards is a reboot, not the end of a song', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10, 400)});
  await settle();
  ctx.$('output-target').value = 'castle';
  await ctx.window.castlePlayer.play();           // this page owns the song
  await pollAt(ctx, 30, 420);
  calls.next = 0; calls.commands.length = 0;
  // An OTA lands mid-song: the castle comes back with no scene table yet.
  await poll(ctx, idle(3, ''));
  assert.equal(calls.next, 0, 'a reboot is not a finished song');
  assert.match(ctx.$('live-state').textContent, /starting up/i);
  assert.match(ctx.$('live-ready').textContent, /starting up/i);
  assert.deepEqual(calls.commands, [], 'nothing is sent to a castle that is still booting');
  // Once the shows are loaded the interrupted song is started again.
  await poll(ctx, idle(6));
  assert.equal(JSON.stringify(calls.commands.at(-1)), '{"action":"scene","scene":"citizens"}');
});

test('B65: a castle nobody here started is not restarted for us', async () => {
  const {ctx, calls} = linkContext({payload: playingAt(10, 400)});
  await settle();
  ctx.$('output-target').value = 'castle';
  await pollAt(ctx, 30, 420);
  calls.commands.length = 0;
  await poll(ctx, idle(2, ''));
  await poll(ctx, idle(5));
  assert.deepEqual(calls.commands, []);
});

/* ---------------------------------------------------------------------- B66 */

test('B66: a scene whose lights outlast its audio is not over when it falls quiet', async () => {
  const vigil = {id: 'vigil', dur: 60000, loop: false};
  const playingVigil = position_s => {
    const payload = playingAt(position_s);
    payload.state.scene = 'vigil';
    return payload;
  };
  const {ctx, calls} = linkContext({payload: playingVigil(1), current: 1, queue: [0], scene: vigil});
  await settle();
  ctx.$('output-target').value = 'castle';
  await ctx.window.castlePlayer.play();
  await poll(ctx, playingVigil(2));
  calls.next = 0;
  for (let i = 0; i < 3; i++) { ctx.now += 1000; await poll(ctx, idle()); }  // the audio ends early
  assert.equal(calls.next, 0, 'the light script still has 58 s to run');
  // Past the scene's authored duration, the queue does move on.
  ctx.now += 61000;
  await poll(ctx, playingVigil(61));
  for (let i = 0; i < 3; i++) { ctx.now += 1000; await poll(ctx, idle()); }
  assert.equal(calls.next, 1);
});

/* ---------------------------------------------------------------- B64/B67/B68 */

test('B64: a castle that never reports a clock does not get every frame at once', async () => {
  // position_ms stays 0 through the whole give-up budget: the baseline has to
  // move to now, or frames 0…4 s all come due the moment align returns.
  const sent = await runFrames([[0, '111111'], [1, '222222'], [2, '333333']]);
  const colours = sent.filter(l => l.c !== 'off');
  assert.equal(colours.length, 3, `every frame goes out once (${JSON.stringify(sent)})`);
  for (let i = 1; i < colours.length; i++) {
    assert.ok(colours[i].at - colours[i - 1].at >= 200,
      `frames are never faster than the 200 ms drain (${JSON.stringify(colours)})`);
  }
  assert.ok(colours[1].at - colours[0].at >= 900, 'and the authored 1 s gap survives');
});

test('B64: frames the next one has already overtaken are dropped, not burst', async () => {
  const sent = await runFrames([[0, '111111'], [0.05, '222222'], [0.06, '333333'], [0.07, '444444'], [1, '555555']]);
  const colours = sent.filter(l => l.c !== 'off').map(l => l.c);
  assert.ok(colours.length < 5, `a frame overtaken inside one drain is skipped (${colours})`);
  assert.equal(colours.at(-1), '555555', 'the last authored colour is still the one on the strips');
  const times = sent.filter(l => l.c !== 'off').map(l => l.at);
  for (let i = 1; i < times.length; i++) {assert.ok(times[i] - times[i - 1] >= 200);}
});

test('B67: stop waits for the light-off to be drained before it evicts it', async () => {
  const {ctx, asked} = showContext([[0, '111111']]);
  await ctx.window.fetch('/radio/device/command', {method: 'POST', body: JSON.stringify({action: 'stop'})});
  const paths = asked.map(a => a.path);
  const off = asked.findIndex(a => a.path === '/api/light?c=off');
  const stop = asked.findIndex(a => a.path === '/api/stop');
  assert.ok(off >= 0 && stop > off, `the strips are darkened first (${paths})`);
  assert.ok(asked[stop].at - asked[off].at >= 250,
    'and STOP waits out the 200 ms drain, or it evicts the pending LIGHT');
});

test('B68: the inventory sweep is serial, so it cannot purge its own socket', async () => {
  const {ctx, peak} = showContext([]);
  await ctx.window.fetch('/radio/device/library');
  assert.equal(peak(), 1, 'the castle keeps four sockets with an LRU purge: no fan-out');
});

/* ---------------------------------------------------------------- B69/B70/B71 */

test('B69: a show is judged by the frames the castle drew, not the frames posted', async () => {
  const live = await landedShow({light_applied: 100, light_evicted: 3}, {light_applied: 106, light_evicted: 5});
  assert.deepEqual([live.frames_landed, live.frames_evicted], [6, 2]);
  const old = await landedShow({}, {});
  assert.deepEqual([old.frames_landed, old.frames_evicted], [null, null], 'unknown, never a confident zero');
  const {ctx} = linkContext({payload: playingAt(10)});
  await settle();
  assert.deepEqual(wordings(ctx.window.castleLink.framesText), ['40 of 50 light frames sent', '38 landed of 40 sent', '36 landed of 40 sent (2 overwritten before the castle drew them)']);
});

test('B70: the health line times a fast poll, a slow one, and one that never lands', async () => {
  const {ctx} = linkContext({payload: playingAt(10, 3671)});
  await settle();
  const [fast, slow, lost] = await healthRun(ctx);
  assert.ok(fast.rtt_ms < 100 && fast.missed_total === 0, `a fast poll is milliseconds (${fast.rtt_ms})`);
  assert.deepEqual([fast.uptime, fast.version], ['1:01:11', '5.55']);
  assert.ok(slow.rtt_ms >= 500, 'a slow poll is reported slow');
  assert.deepEqual([lost.failures, lost.missed_total], [1, 1]);
  assert.ok(lost.worst_ms >= 500, 'the worst of the last ten still remembers the slow poll');
  assert.match(ctx.window.castleLink.healthLine(), /missed 1 \(1 in a row\) · up 1:01:1\d · firmware 5\.55$/);
});

test('B71: recent castle events read oldest first, the newest at +00:00.0', async () => {
  const {log, health} = await eventLog(async () => EVENTS);
  assert.deepEqual(log.split('\n'), ['-00:10.0  play  radio_a.mp3', '-00:04.5  light_evicted  3', '+00:00.0  stop']);
  assert.equal(health, 'link 4 ms', 'the panel takes its health line from the shared poll');
  const failed = await eventLog(async () => { throw new Error('Castle answered 404'); });
  assert.match(failed.log, /not supported by this firmware/);
});
