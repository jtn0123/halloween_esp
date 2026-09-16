/* Seeded storms against the two state machines the porch depends on:
   the follower in device-link.js (what the page believes the castle is
   doing) and the light-show scheduler in castle-direct.js (when a frame
   is posted). test_castle_radio.test.mjs asks the questions someone thought
   of; these draw thousands of poll answers and frame lists nobody did and
   hold a handful of invariants that must survive every one of them.

   Deterministic: CASTLE_LINK_FUZZ_SEED picks the first seed (default 1),
   CASTLE_LINK_FUZZ_RUNS how many follow it (default 60). Every failure
   names the seed and the step, so it replays. */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {idle, lightsSent, linkContext, playingAt, settle, settlingOn, showContext, toolsContext} from './test_support.mjs';

const SEED = Number(process.env.CASTLE_LINK_FUZZ_SEED || 1);
const RUNS = Number(process.env.CASTLE_LINK_FUZZ_RUNS || 60);
const FRAME_MS = 200;

/* mulberry32: small, seedable, good enough to be reproducible. */
function prng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const pick = (rng, xs) => xs[Math.floor(rng() * xs.length)];
const BAD_WORDS = /NaN|undefined|\[object|null/;

/* ── the follower ─────────────────────────────────────────────────────── */

/* One poll answer. `s` is the castle's true story (uptime, clock); the
   payload is usually honest, sometimes a field short, sometimes garbage. */
function draw(rng, s) {
  const r = rng();
  s.uptime += rng() < 0.05 ? 0 : 1 + Math.floor(rng() * 3);
  if (rng() < 0.04) {s.uptime = Math.floor(rng() * 5); s.rebooted = true;}
  if (rng() < 0.03) {s.version = pick(rng, ['5.55', '5.59', '5.60-dev']);}
  let payload;
  if (r < 0.45) {
    s.pos = rng() < 0.15 ? Math.floor(rng() * 400) : s.pos + 1 + rng();
    payload = playingAt(s.pos, s.uptime);
  } else if (r < 0.65) {payload = idle(s.uptime, rng() < 0.1 ? 'stop' : undefined);}
  else if (r < 0.75) {payload = settlingOn(pick(rng, ['citizens', 'vigil', 'stop']));}
  else if (r < 0.9) {
    payload = rng() < 0.5 ? playingAt(s.pos, s.uptime) : idle(s.uptime);
    const field = pick(rng, ['version', 'uptime_s', 'scenes', 'pir', 'volume', 'playing', 'scene', 'track']);
    const value = pick(rng, [undefined, null, 'x', -1, Number.NaN, {}, []]);
    if (value === undefined) {delete payload.state[field];} else {payload.state[field] = value;}
    if (rng() < 0.3) {delete payload[pick(rng, ['playback', 'capabilities', 'light_show'])];}
    s.mangled = true;
    return payload;
  } else {
    s.mangled = true;
    return pick(rng, [{}, {state: null}, null, 'garbage', {state: 'x'}, {state: {}, playback: null}]);
  }
  payload.state.version = s.version;
  if (rng() < 0.3) {payload.light_show = pick(rng, [null, {active: true, frames_sent: 3, frames_total: 9},
    {active: true, frames_sent: 3, frames_total: 9, frames_landed: 2, frames_evicted: 1, hidden: true},
    {active: false, frames_sent: Number.NaN}, {active: true, note: 'lights paused — screen off'}]);}
  return payload;
}

async function followerRun(seed) {
  const rng = prng(seed);
  const {ctx, calls, $} = linkContext({queue: [1, 0, 1]});
  $('output-target').value = 'castle';
  const link = ctx.window.castleLink;
  const s = {uptime: 100, pos: 0, version: '5.59', rebooted: false, mangled: false};
  let lastSeen = null, nexts = 0;
  const carried = p => (Number.isFinite(p?.state?.uptime_s) && p.state.uptime_s >= 0 ? p.state.uptime_s : null);
  for (let step = 0; step < 80; step++) {
    const where = `seed ${seed} step ${step}`;
    s.rebooted = false; s.mangled = false;
    const payload = draw(rng, s);
    ctx.fail = rng() < 0.15;
    ctx.document.hidden = rng() < 0.2;
    ctx.payload = payload;
    const toastsBefore = calls.toasts.length;
    await link.refresh().catch(e => assert.fail(`${where}: refresh rejected: ${e.stack}`));
    await settle();
    const h = link.health();
    assert.equal(typeof link.healthLine(), 'string', where);
    assert.doesNotMatch(link.healthLine(), BAD_WORDS, `${where}: ${link.healthLine()}`);
    assert.equal(typeof link.framesText(payload?.light_show), 'string', where);
    if (h.failures >= 3) {assert.equal(link.state(), null, `${where}: three strikes must clear the state`);}
    assert.ok(calls.next - nexts <= 1, `${where}: the queue moved ${calls.next - nexts} times on one poll`);
    nexts = calls.next;
    // The chip and the health line are read on every poll the castle
    // answered, however short of a field that answer was: a missing version
    // is "—", never "undefined" or "[object Object]".
    if (!ctx.fail) {
      for (const id of ['castle-chip', 'live-health']) {
        assert.doesNotMatch($(id).textContent, BAD_WORDS, `${where}: ${id} says "${$(id).textContent}"`);
      }
    }
    if (!ctx.fail && !s.mangled) {
      assert.equal(h.failures, 0, `${where}: an honest answer must reset the strikes`);
      assert.notEqual(link.state(), null, where);
      for (const id of ['live-state', 'live-health', 'current-detail', 'castle-chip']) {
        assert.doesNotMatch($(id).textContent, BAD_WORDS, `${where}: ${id} says "${$(id).textContent}"`);
      }
    }
    // The reboot oracle reads what the link was actually handed: a poll
    // that failed carried nothing, a mangled one may still carry the clock.
    const up = ctx.fail ? null : carried(payload);
    if (up !== null) {
      if (lastSeen !== null && up < lastSeen) {
        assert.ok(calls.toasts.slice(toastsBefore).some(t => /Castle restarted/.test(t)),
          `${where}: uptime fell ${lastSeen} → ${up} and nobody said reboot`);
      }
      lastSeen = up;
    }
  }
}

test('fuzz: the follower survives any order of honest, short and garbage polls', async () => {
  for (let seed = SEED; seed < SEED + RUNS; seed++) {await followerRun(seed);}
});

/* ── the light-show scheduler ─────────────────────────────────────────── */

function drawFrames(rng) {
  const n = Math.floor(rng() * 50);
  const frames = [];
  let at = rng() < 0.3 ? 0 : rng() * 2;
  for (let i = 0; i < n; i++) {
    frames.push([Number(at.toFixed(3)), pick(rng, ['ff0000', '00ff00', '0000ff', '111111', 'ffffff', '000000'])]);
    at += pick(rng, [0, 0.05, 0.1, 0.2, 0.25, 0.5, 1, 3]);
  }
  return frames;
}

async function untilQuiet(asked) {
  let seen = -1, calm = 0;
  for (let i = 0; i < 20000 && calm < 40; i++) {
    await settle();
    if (asked.length === seen) {calm++;} else {calm = 0; seen = asked.length;}
  }
}

async function showRun(seed) {
  const rng = prng(seed);
  const where = `seed ${seed}`;
  const frames = drawFrames(rng);
  const onCastle = rng() < 0.85;
  const extra = {track: onCastle ? 'radio_a.mp3' : 'other.mp3', position_ms: rng() < 0.5 ? 0 : Math.floor(rng() * 5000),
    light_applied: Math.floor(rng() * 100), light_evicted: Math.floor(rng() * 10)};
  if (rng() < 0.2) {delete extra.light_applied; delete extra.light_evicted;}
  const {ctx, asked, delays, peak} = showContext(frames, extra);
  await ctx.window.fetch('/radio/device/command', {method: 'POST',
    body: JSON.stringify({action: 'file', file: 'radio_a.mp3', key: 'radio_a'})});
  await untilQuiet(asked);
  const sent = lightsSent(asked);
  const colours = sent.filter(l => l.c !== 'off' && l.c !== 'show');
  const offs = sent.filter(l => l.c === 'off');
  assert.ok(delays.every(d => Number.isFinite(d) && d >= 0), `${where}: a sleep was ${delays.find(d => !(d >= 0))}`);
  assert.ok(peak() <= 2, `${where}: ${peak()} castle requests in flight at once`);
  // Order and pace: the frames posted are the frame list in order, never
  // two inside one 200 ms drain.
  let cursor = 0;
  for (const l of colours) {
    while (cursor < frames.length && frames[cursor][1] !== l.c) {cursor++;}
    assert.ok(cursor < frames.length, `${where}: posted ${l.c} out of order`);
    cursor++;
  }
  for (let i = 1; i < colours.length; i++) {
    assert.ok(colours[i].at - colours[i - 1].at >= FRAME_MS - 1e-6,
      `${where}: frames ${i - 1} and ${i} only ${colours[i].at - colours[i - 1].at} ms apart`);
  }
  // Off is the frame that must land: exactly once, and last.
  assert.equal(offs.length, 1, `${where}: ${offs.length} off frames`);
  assert.equal(sent.at(-1).c, 'off', `${where}: the last frame was ${sent.at(-1).c}`);
  const report = JSON.parse(await (await ctx.window.fetch('/radio/device')).text()).light_show;
  assert.equal(report.active, false, where);
  // Sent is what this page POSTed and coalesced is what the next frame
  // overtook inside one drain; together they are the whole frame list.
  assert.equal(report.frames_sent, onCastle ? colours.length : 0, `${where}: frames_sent`);
  assert.equal(report.frames_sent + (onCastle ? report.frames_coalesced : frames.length), frames.length,
    `${where}: ${report.frames_sent} sent + ${report.frames_coalesced} coalesced is not ${frames.length}`);
  assert.equal(report.frames_total, frames.length, where);
  if ('light_applied' in extra) {assert.equal(typeof report.frames_landed, 'number', where);}
  else {assert.equal(report.frames_landed, null, `${where}: old firmware must read as unknown, not zero`);}
}

test('fuzz: the light-show scheduler keeps order, pace and the final off for any frame list', async () => {
  for (let seed = SEED; seed < SEED + RUNS; seed++) {await showRun(seed);}
});

/* ── the events viewer ────────────────────────────────────────────────── */

function drawEvents(rng) {
  const r = rng();
  if (r < 0.1) {return pick(rng, [null, undefined, {}, 'x', 7, []]);}
  const rows = [];
  let t = Math.floor(rng() * 1e6);
  for (let i = Math.floor(rng() * 70); i > 0; i--) {
    t += Math.floor(rng() * 5000);
    const row = {t, e: pick(rng, ['play', 'scene', 'stop', 'light_evicted', 'sound', 'silent', '']), a: pick(rng, ['vigil', '3', '', 'a'.repeat(47)])};
    if (rng() < 0.15) {row.t = pick(rng, ['x', null, undefined, Number.NaN, -5, 1e15]);}
    if (rng() < 0.1) {delete row.a;}
    rows.push(rng() < 0.05 ? pick(rng, [null, 'row', 3]) : row);
  }
  return rows;
}

test('fuzz: the events viewer renders any answer without throwing or printing NaN', async () => {
  for (let seed = SEED; seed < SEED + RUNS; seed++) {
    const rng = prng(seed);
    const rows = drawEvents(rng);
    const {$} = toolsContext(async () => rows);
    await $('bench-events-load').onclick();
    const log = $('bench-events-log').textContent;
    assert.equal(typeof log, 'string', `seed ${seed}`);
    assert.doesNotMatch(log, /NaN|undefined|\[object/, `seed ${seed}: ${log}`);
    const honest = Array.isArray(rows) ? rows.filter(r => r && typeof r === 'object') : [];
    if (honest.length) {assert.doesNotMatch(log, /not supported/, `seed ${seed}: an answer with ${honest.length} rows was called unsupported`);}
  }
});
