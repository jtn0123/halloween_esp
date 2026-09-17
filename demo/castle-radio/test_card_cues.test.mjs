/* Firmware 5.63 runs a song's own light show from a cue file on the card.
   What that changes for the page the castle serves: it must keep its four
   solid colours a second to itself, it must never post "off" over a show it
   does not own, and the card — not the computer that built the page — says
   which songs there are. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {directContext, lightsSent, settle, showContext} from './test_support.mjs';

const play = (ctx, file, key) => ctx.window.fetch('/radio/device/command', {method: 'POST',
  body: JSON.stringify({action: 'file', file, key})});

test('a castle running the card show gets no frames, and no "off" over it', async () => {
  const {ctx, asked} = showContext([[0, '111111'], [1, '222222'], [2, '333333']], {version: '5.63', cues: 1258});
  await play(ctx, 'radio_a.mp3', 'radio_a');
  for (let i = 0; i < 200; i++) {await settle();}
  // The one frame allowed is the hand-back every imported play starts with.
  assert.deepEqual(lightsSent(asked).map(l => l.c), ['show']);
});

test('the same song on a castle with no cue file streams as it always did', async () => {
  const {ctx, asked} = showContext([[0, '111111'], [1, '222222']], {version: '5.63', cues: 0});
  await play(ctx, 'radio_a.mp3', 'radio_a');
  for (let i = 0; i < 200; i++) {await settle();}
  assert.ok(lightsSent(asked).some(l => l.c === '111111'));
});

test('a song with a cue file beside it is in the library, known to this page or not', async () => {
  const {ctx} = directContext({
    '/api/status': {version: '5.63', scene: 'stop', track: '', scenes: 'stop'},
    '/api/files': [
      {name: 'halloween_songs___ghostbusters_t.mp3', size: 2956061, dir: false},
      {name: 'halloween_songs___ghostbusters_t.cue', size: 34112, dir: false},
      {name: 'test_1k.mp3', size: 129148, dir: false},   // a tone: no show, not a song
      {name: 'orphan.cue', size: 40, dir: false},         // a show with no song
      {name: 'site', size: 0, dir: true},
    ],
  }, []);
  const rows = JSON.parse(await (await ctx.window.fetch('/radio/library')).text());
  assert.deepEqual(rows.map(r => [r.key, r.title, r.filename, r.style]),
    [['halloween_songs___ghostbusters_t', 'Halloween Songs Ghostbusters T',
      'halloween_songs___ghostbusters_t.mp3', 'Card light show']]);
});

test('a song this page already lists is not listed twice', async () => {
  const {ctx} = showContext([], {version: '5.63'});
  const rows = JSON.parse(await (await ctx.window.fetch('/radio/library')).text());
  assert.deepEqual(rows.map(r => r.key), ['radio_a']);
});
