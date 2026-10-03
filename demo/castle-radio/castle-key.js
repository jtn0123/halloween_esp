/* The castle key (firmware v5.74) — the card in Run settings.
 *
 * A castle with a key refuses changes (uploads, deletes, motion settings,
 * updates) from anything that does not send it, and every other surface of
 * this page then says "This castle has a key — enter it in Settings". This
 * card is where: GET /radio/device/key says whether the castle is locked and
 * whether a key is remembered for it; POST {action, key} uses one (checked
 * against the castle before it is remembered), sets a new one, or clears it.
 *
 * On a computer server.py answers, through tools/castle_keys.py, and the key
 * is remembered in the shared store for THIS castle (docs/notes/
 * 06-buyer-build.md). On the castle-served build castle-direct.js answers and
 * the key lives in this browser. Either way the key is typed, sent once, and
 * the field is emptied — it is never painted back onto the page. */
(() => {
  const byId = id => document.getElementById(id);
  const input = byId('key-input');
  if (!input) {return;}
  const state = byId('key-state');
  const message = byId('key-message');
  const LOCK = {true: 'This castle has a key.', false: 'This castle has no key.'};
  const DONE = {
    use: 'Key accepted · remembered for this castle',
    set: 'The castle has its new key · remembered for this castle',
    clear: 'The castle has no key now',
  };

  function describe(s) {
    if (s.pinned) {return 'This castle’s key is set in the app’s settings file (castle_key) · change it there';}
    const lock = LOCK[s.locked] ?? 'The castle is not answering.';
    return `${lock} ${s.remembered ? 'A key is remembered for it here.' : 'No key is remembered for it here.'}`;
  }

  async function ask(init = {}) {
    const response = await fetch('/radio/device/key', {cache: 'no-store', ...init});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {throw new Error(body.error || `Castle key request failed (${response.status})`);}
    return body;
  }

  async function refresh() {
    try { state.textContent = describe(await ask()); }
    catch (error) { state.textContent = error.message; }
  }

  async function act(action) {
    const key = input.value.trim();
    // Only the clear needs no key: the one remembered is what the castle gets.
    if (action !== 'clear' && !key) { message.textContent = 'Type the key first'; return; }
    message.textContent = 'Asking the castle…';
    try {
      const answer = await ask({method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action, key})});
      state.textContent = describe(answer);
      message.textContent = DONE[action];
    } catch (error) { message.textContent = error.message; }
    finally { input.value = ''; }
  }

  byId('key-use').onclick = () => act('use');
  byId('key-set').onclick = () => act('set');
  byId('key-clear').onclick = () => act('clear');
  void refresh();          // it says its own failure, on the state line
  window.castleKey = {refresh, act};
})();
