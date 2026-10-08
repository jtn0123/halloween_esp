/* First run (first_run.py): the collection as it really is on this computer.
 *
 * app.js lists ten demo tracks whose audio lives in media/, which only the
 * demo's own checkout has; an installed app has none of it. So app.js starts
 * every one of them hidden (`absent`), and this script shows the ones the
 * server says are here: nothing is loaded, probed or asked a waveform of
 * before that answer, so an app with no demo never shows "This audio file is
 * unavailable" or "Waveform unavailable" under its welcome. When nothing is
 * here — no demo, nothing imported — the Listen page leads with one card:
 * add your first song, which opens Import. The card goes the moment a song
 * arrives (imports.js re-renders the list, and this follows every render).
 *
 * A built-in the owner removed stays removed: preview.js records those in
 * 'castle-radio-hidden', and a row recorded there is never shown.
 *
 * On the page the castle serves (castle-direct.js), importing happens on the
 * computer, so the card says that instead and asks the server nothing; every
 * row shows at once, and the ones whose audio is not on the castle's card
 * are hidden when its listing answers. */
/* global $, current, history, load, probeDuration, queue, renderQueue, tracks, updatePlayer */
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

  // Until the server has said which built-ins are here, an empty list is not
  // yet "no songs": the card waits for the answer rather than flash past.
  let known = false;
  const left = () => tracks.filter(t => !t.deleted).length;
  function paint() {
    const n = left();
    card.hidden = !known || n > 0;
    const preview = document.getElementById('split-preview');
    if (preview) {preview.hidden = n === 0;}
    $('collection-count').textContent = n;
    $('collection-caption').textContent = caption(n);
  }
  function caption(n) {
    if (n) {return `${n} tracks · automatic light shows`;}
    return known ? 'No songs yet · add your first' : 'Looking for your songs…';
  }
  const render = window.renderTracks;
  window.renderTracks = (...args) => { render(...args); paint(); };

  function ownerHidden() {
    try { return new Set(JSON.parse(localStorage.getItem('castle-radio-hidden') || '[]')); } catch { return new Set(); }
  }
  function settle() {
    queue = queue.filter(id => !tracks[id].deleted);
    history = history.filter(id => !tracks[id].deleted);
    renderQueue();
    const first = tracks.find(t => !t.deleted);
    if (tracks[current]?.deleted && first) { load(first.id); } else { updatePlayer(); }
  }
  // Show the built-ins whose audio is here: `files` lists them, and null is
  // "all of them" (the castle's page, or a server that did not answer). The
  // player was handed a hidden row, so no source: the first song now shown
  // is loaded properly, and the rest follow it in the queue.
  function reveal(files) {
    const here = files && new Set(files);
    const hidden = ownerHidden();
    const vacant = !!tracks[current]?.deleted;
    const shown = [];
    for (const t of tracks) {
      if (!t.absent || (here && !here.has(t.file))) {continue;}
      t.absent = false;
      if (hidden.has(t.file)) {continue;}
      t.deleted = false;
      shown.push(t.id);
      probeDuration(t);
    }
    known = true;
    queue = [...shown, ...queue.filter(id => !shown.includes(id))];
    const first = tracks.find(t => !t.deleted);
    if (vacant && first) {
      queue = queue.filter(id => id !== first.id);
      load(first.id);
    }
    settle();
  }
  function hideAbsent(present) {
    const have = new Set(present);
    for (const t of tracks) {
      if (!t.key && t.file && !t.deleted && !have.has(t.file)) {t.deleted = true; t.absent = true;}
    }
    settle();
  }
  paint();
  // On the castle's own page, "here" is the card. Every row shows at once; a
  // built-in row whose audio the card does not hold is then hidden, as it is
  // on a computer without the demo's media: a castle sold with the shipped
  // show (tools/buyer_card.py) has the eight scenes and none of the yard's
  // songs. A card that does not answer hides nothing.
  if (direct) {
    reveal(null);
    Promise.resolve(window.remoteLibrary?.ensure())
      .then(inv => { if (inv) {hideAbsent(tracks.filter(t => inv.tracks[t.file]?.audio).map(t => t.file));} })
      .catch(() => {});
    return;
  }
  fetch('/radio/first-run', {cache: 'no-store'})
    .then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then(answer => reveal(answer?.demo || []), () => reveal(null));
})();
