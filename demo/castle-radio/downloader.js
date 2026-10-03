/* Update the downloader: yt-dlp, the program that fetches a pasted link.
   Websites change under it every few weeks and an old copy stops working; a
   link that fails that way says "Update the downloader", and this is the
   button (here, and on that job in the import queue). The server queues the
   update behind the imports — never in the middle of one — fetches yt-dlp's
   latest release from GitHub, checks it against the release's own
   SHA2-256SUMS, and keeps the old copy if anything goes wrong
   (downloader_routes.py, tools/ytdlp_update.py). Nothing updates by itself. */
(() => {
  const byId = id => document.getElementById(id);
  const box = byId('downloader');
  if (!box) { return; }
  const button = byId('downloader-update');
  const note = byId('downloader-note');
  const detail = byId('downloader-detail');
  let timer = null;

  /* One line for the state, one for the note: what is installed, then what
     the last press did. A failure is the server's one sentence; its own
     words go behind Details. */
  function words(s) {
    const u = s.update || {phase: 'idle'};
    let have = 'not installed yet';
    if (s.installed) { have = s.version ? `version ${s.version}` : 'installed'; }
    const state = `Song downloader · ${have}`;
    if (u.phase === 'waiting') { return {state, note: 'Waiting for the import in progress to finish, then updating…', busy: true}; }
    if (u.phase === 'updating') { return {state, note: 'Updating the downloader…', busy: true}; }
    if (u.phase === 'failed') { return {state, note: u.error, detail: u.error_detail || ''}; }
    if (u.phase === 'done') { return {state, note: u.changed ? `Updated to ${u.version}. Try the link again.` : `Already up to date (${u.version}).`}; }
    if (!s.installed) { return {state, note: 'Links need the downloader. Update the downloader to install it.'}; }
    return {state, note: s.managed ? '' : 'Update the downloader to keep a copy Castle Tools can update.'};
  }

  function show(s) {
    const w = words(s);
    byId('downloader-state').textContent = w.state;
    note.textContent = w.note;
    button.disabled = !!w.busy;
    detail.hidden = !w.detail;
    byId('downloader-log').textContent = w.detail || '';
    clearTimeout(timer);
    if (w.busy) { timer = setTimeout(refresh, 2000); }
  }

  /* On the castle's own page the requests reach the Mac only through its
     connected tools; with none connected there is nothing to update. */
  const reachable = () => !window.castleDirect || !!window.castleDesktop?.connected;

  /* Never rejects: a check that fails says so in the panel, which is all a
     caller (the page load, a poll, a reconnect) could do with it. */
  async function refresh() {
    try {
      box.hidden = !reachable();
      if (box.hidden) { return; }
      const r = await fetch('/radio/downloader', {cache: 'no-store', signal: AbortSignal.timeout(20000)});
      if (!r.ok) { throw new Error(`the import service answered ${r.status}`); }
      show(await r.json());
    } catch {
      byId('downloader-state').textContent = 'Song downloader';
      note.textContent = 'The import service is not answering, so the downloader cannot be checked.';
    }
  }

  async function update() {
    button.disabled = true;
    note.textContent = 'Asking for the update…';
    try {
      const r = await fetch('/radio/downloader/update', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}', signal: AbortSignal.timeout(15000)});
      const body = await r.json().catch(() => ({}));
      if (!r.ok) { throw new Error(body.error || 'The update could not be started. Try again.'); }
      await refresh();
    } catch (error) {
      note.textContent = error.message;
      button.disabled = false;
    }
  }

  button.onclick = update;
  window.addEventListener('castle-tools-connection', refresh);
  window.castleDownloader = {update, refresh, words, available: reachable};
  // Fire and forget is right here: nothing waits on the first check, and
  // refresh() reports its own failure on the page rather than rejecting.
  void refresh();
})();
