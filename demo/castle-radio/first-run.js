/* First run (first_run.py): the collection as it really is on this computer.
 *
 * app.js lists ten demo tracks whose audio lives in media/, which only the
 * demo's own checkout has; an installed app has none of it. So the rows whose
 * file is not here are hidden, the count says what is left, and when nothing
 * is — no demo, nothing imported — the Listen page leads with one card: add
 * your first song, which opens Import. The card goes the moment a song
 * arrives (imports.js re-renders the list, and this follows every render).
 *
 * On the page the castle serves (castle-direct.js), importing happens on the
 * computer, so the card says that instead and asks the server nothing. */
/* global $, current, history, load, queue, renderQueue, tracks, updatePlayer */
(() => {
  const list = document.getElementById('tracks');
  if (!list || typeof tracks === 'undefined') {return;}
  const direct = !!window.castleDirect;
  const card = document.createElement('div');
  card.id = 'first-run';
  card.className = 'first-run';
  card.hidden = true;
  const title = document.createElement('h3');
  title.textContent = 'Add your first song';
  const words = document.createElement('p');
  words.textContent = direct
    ? 'No songs on this castle yet. Add them from Castle Radio on your computer: import a song, then Sync it to the castle.'
    : 'Paste a link or choose a file, and Castle Radio prepares its light show. Sync then puts it on your castle.';
  card.append(title, words);
  if (!direct) {
    const go = document.createElement('button');
    go.id = 'first-run-import';
    go.className = 'primary';
    go.textContent = 'Import a song';
    go.onclick = () => { location.hash = 'import'; };
    card.append(go);
  }
  list.parentNode.insertBefore(card, list);

  const left = () => tracks.filter(t => !t.deleted).length;
  function paint() {
    const n = left();
    card.hidden = n > 0;
    $('collection-count').textContent = n;
    $('collection-caption').textContent = n ? `${n} tracks · automatic light shows` : 'No songs yet · add your first';
  }
  const render = window.renderTracks;
  window.renderTracks = (...args) => { render(...args); paint(); };

  function hideAbsent(present) {
    const have = new Set(present);
    for (const t of tracks) { if (!t.key && t.file && !have.has(t.file)) {t.deleted = true;} }
    queue = queue.filter(id => !tracks[id].deleted);
    history = history.filter(id => !tracks[id].deleted);
    renderQueue();
    const first = tracks.find(t => !t.deleted);
    if (tracks[current]?.deleted && first) { load(first.id); } else { updatePlayer(); }
  }
  if (direct) { paint(); return; }
  fetch('/radio/first-run', {cache: 'no-store'})
    .then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then(answer => hideAbsent(answer.demo || []))
    .catch(() => paint());
})();
