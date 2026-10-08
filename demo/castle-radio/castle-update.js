/* "Update castle" — the firmware card in Your castle (PRODUCTION-TODO §9).
 *
 * GET /radio/castle/update says which firmware the castle runs and what the
 * owner's channel has for its board (castle_update_routes.py, which asks
 * tools/castle_update.py). The button POSTs the same path, and the card
 * follows the job — download, checksum, send, restart — until the castle
 * answers again and says which firmware it came back on. Nothing here sends
 * firmware but the button: the update is never automatic.
 *
 * On the castle-served page there is no computer to download and verify
 * the firmware, so castle-direct.js answers the route with its "on your
 * computer" sentence, the card shows it, and the button stays off. */
(() => {
  const byId = id => document.getElementById(id);
  const card = byId('castle-update');
  if (!card) {return;}
  const state = byId('fw-state');
  const current = byId('fw-current');
  const available = byId('fw-available');
  const button = byId('fw-update');
  const progress = byId('fw-progress');
  const message = byId('fw-message');
  const ROUTE = '/radio/castle/update';
  const POLL_MS = 1500;

  async function ask(init = {}) {
    const response = await fetch(ROUTE, {cache: 'no-store', ...init});
    const body = await response.json().catch(() => ({}));
    // A refusal that still carries the job (the castle went quiet, GitHub
    // is out of reach) is shown like an answer; one without it is an error.
    if (!response.ok && !body.job) {throw new Error(body.error || `Castle update request failed (${response.status})`);}
    return body;
  }

  /* Paint one answer; true while an update is running (keep polling). */
  function show(body) {
    const job = body.job || {};
    progress.hidden = !job.running;
    if (job.running) {
      state.textContent = 'Updating the castle — leave it switched on.';
      message.textContent = job.phase ? `${job.phase}…` : '';
      button.disabled = true;
      return true;
    }
    if (job.done) {message.textContent = job.error || job.result || '';}
    current.textContent = body.current || '—';
    available.textContent = body.available ? `${body.available} (${body.tag})` : '—';
    state.textContent = body.error || body.message || '';
    button.disabled = !body.update;
    button.textContent = body.update ? `Update castle to ${body.available}` : 'Update castle';
    return false;
  }

  async function refresh() {
    let body;
    try { body = await ask(); }
    catch (error) {
      state.textContent = error.message;
      button.disabled = true;
      return;
    }
    if (show(body)) {setTimeout(refresh, POLL_MS);}
  }

  async function update() {
    button.disabled = true;
    message.textContent = 'Starting the update…';
    const init = {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action: 'update'})};
    try { show(await ask(init)); }
    catch (error) {
      message.textContent = error.message;
      return refresh();
    }
    setTimeout(refresh, POLL_MS);
  }

  button.onclick = update;
  void refresh();          // it says its own failure, on the state line
  window.castleUpdate = {refresh, update};
})();
