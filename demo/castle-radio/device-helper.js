/* A popup carries local jobs across origins without relaxing the castle CSP. */
(() => {
  if (!window.castleDirect) { return; }
  const ORIGIN = 'http://127.0.0.1:8871';
  const deviceFetch = window.fetch.bind(window);
  const pending = new Map();
  let popup = null, sequence = 0, connected = false, tools = null;
  let handshakeTimer = null;
  const localPaths = /^\/radio\/(?:tools$|jobs$|library(?:\/|$)|import$|retry$|reprocess$|restore\/|waveform\/|audio\/|device\/(?:library$|sync(?:-status)?(?:\?|$)))/;
  const byId = id => document.getElementById(id);

  function update(value) {
    connected = value;
    const form = byId('import-form');
    for (const control of form?.querySelectorAll('input, button') || []) { control.disabled = !value; }
    if (byId('import-message')) { byId('import-message').textContent = value ? 'Imports and voice separation run on your Mac.' : 'Connect Mac tools to import songs here.'; }
    window.dispatchEvent(new CustomEvent('castle-tools-connection'));
  }
  function disconnect(reason = 'Mac tools disconnected. Keep the connection window open.') {
    clearTimeout(handshakeTimer);
    update(false);
    for (const job of pending.values()) { clearTimeout(job.timer); job.reject(new Error(reason)); }
    pending.clear();
  }
  function connect() {
    disconnect();
    popup = window.open(`${ORIGIN}/companion.html?castle=${encodeURIComponent(location.origin)}`, 'castle-tools-companion', 'popup,width=480,height=440');
    if (!popup) { throw new Error('Allow the connection window to open, then try again.'); }
    handshakeTimer = setTimeout(() => { if (!connected) { disconnect(); } }, 12000);
  }
  function request(path, options = {}) {
    if (!connected || !popup || popup.closed) { disconnect(); return Promise.reject(new Error('Connect Mac tools first.')); }
    return new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('Mac tools did not answer. Check the connection window.')); }, 120000);
      pending.set(id, {resolve, reject, timer});
      /* The bridge replaces window.fetch, so a caller's AbortSignal.timeout is
         only honoured if this side listens for it (grade report 2026-09-17 pm C6). */
      options.signal?.addEventListener('abort', () => {
        const job = pending.get(id);
        if (!job) { return; }
        clearTimeout(job.timer); pending.delete(id);
        job.reject(new Error('Mac tools did not answer in time.'));
      }, {once: true});
      popup.postMessage({type:'castle-tools-request', id, path, method:options.method || 'GET', headers:options.headers || {}, body:options.body}, ORIGIN);
    });
  }
  window.addEventListener('message', event => {
    if (event.origin !== ORIGIN || event.source !== popup) { return; }
    const message = event.data;
    if (message?.type === 'castle-tools-ready' && message.service === 'castle-radio' && message.protocol === 1 && message.status?.castle_origin === location.origin) {
      clearTimeout(handshakeTimer);
      tools = message.status;
      update(true);
      return;
    }
    if (message?.type !== 'castle-tools-response') { return; }
    const job = pending.get(message.id);
    if (!job) { return; }
    clearTimeout(job.timer); pending.delete(message.id);
    job.resolve(new Response(message.body, {status:message.status, headers:message.headers}));
  });
  window.fetch = async (input, options) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    const path = url.pathname + url.search;
    if (!connected || url.origin !== location.origin || !localPaths.test(path)) { return deviceFetch(input, options); }
    const response = await request(path, options);
    if (path === '/radio/library' && response.ok) {
      const rows = await response.clone().json();
      // The direct physical player uses these reduced frames, while the
      // browser renderer keeps the original detailed cues from the catalog.
      for (const row of rows) {
        const index = window.castleDirect.library.findIndex(item => item.key === row.key);
        if (index < 0) { window.castleDirect.library.push(row); }
        else { window.castleDirect.library[index] = row; }
      }
    }
    return response;
  };
  window.castleDesktop = {
    get connected() { return connected; },
    get status() { return tools; },
    connect, request,
    async media(path) {
      const response = await request(path);
      if (!response.ok) { throw new Error('Mac audio unavailable'); }
      const url = URL.createObjectURL(await response.blob());
      return url;
    },
  };
  setInterval(() => { if (connected) { request('/radio/tools').then(response => { if (!response.ok) { disconnect(); } }).catch(() => disconnect()); } }, 10000);
  setInterval(() => { if (connected && (!popup || popup.closed)) { disconnect(); } }, 1000);
})();
