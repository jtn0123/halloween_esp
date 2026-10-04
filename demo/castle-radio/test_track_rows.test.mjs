/* The collection's row numbers count the rows the listener sees.
 *
 *   node --test demo/castle-radio/test_track_rows.test.mjs
 *
 * They were the track id + 1, so a castle-served page that hides the
 * built-in songs its card lacks started its list at "03". */
import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';

import {fmt, page, read} from './test_support.mjs';

function rows(tracks, query = '') {
  const source = read('app.js');
  const line = source.split('\n').find(l => l.startsWith('function renderTracks(){'));
  assert.ok(line, 'renderTracks() is still one line of app.js');
  const {$} = page();
  $('search').value = query;
  const ctx = {
    $, tracks, fmt, filter: 'all', current: -1, audio: {paused: true},
    art: () => '', safe: s => String(s), window: {},
  };
  vm.createContext(ctx);
  vm.runInContext(`${line}\nrenderTracks();`, ctx);
  return [...$('tracks').innerHTML.matchAll(/<div class="track [^"]*"><span>([^<]*)<\/span>/g)]
    .map(m => m[1]);
}

const song = (id, title, extra = {}) =>
  ({id, title, artist: 'Castle', kind: 'song', style: 'eerie', duration: 60, ...extra});

test('rows number from 01 when the first songs are hidden', () => {
  const tracks = [song(0, 'Gone', {deleted: true}), song(1, 'Also gone', {deleted: true}),
    song(2, 'Vigil'), song(3, 'Storm')];
  assert.deepEqual(rows(tracks), ['01', '02']);
});

test('a search numbers what it found, not where it sits in the library', () => {
  const tracks = [song(0, 'Vigil'), song(1, 'Storm'), song(2, 'Seance')];
  assert.deepEqual(rows(tracks, 'seance'), ['01']);
});
