/* A deliberately narrow postMessage bridge from the castle page to Mac tools. */
(() => {
  'use strict';

  const state = document.getElementById('companion-state');
  const reconnect = document.getElementById('companion-reconnect');
  const allowedHeaders = new Set([
    'content-type', 'x-filename', 'x-split', 'x-audio-format', 'x-audio-quality',
  ]);
  const rules = [
    ['GET', /^\/radio\/tools$/],
    ['GET', /^\/radio\/(?:jobs|library)$/],
    ['DELETE', /^\/radio\/library\/[^/]+$/],
    ['POST', /^\/radio\/(?:import|retry|reprocess)$/],
    ['POST', /^\/radio\/restore\/[^/]+$/],
    ['GET', /^\/radio\/(?:waveform|audio)\/[^/].*$/],
    ['POST', /^\/radio\/device\/sync$/],
    ['GET', /^\/radio\/device\/(?:sync-status|library)$/],
  ];
  let castleOrigin = '';
  let connected = false;

  function localService() {
    const url = new URL(location.href);
    return url.protocol === 'http:' &&
      (url.hostname === '127.0.0.1' || url.hostname === 'localhost' || url.hostname === '[::1]');
  }

  function requestedCastle() {
    const value = new URL(location.href).searchParams.get('castle') || '';
    try {
      const url = new URL(value);
      return url.origin === value && /^https?:$/.test(url.protocol) ? value : '';
    } catch {
      return '';
    }
  }

  function show(message, kind) {
    state.textContent = message;
    state.dataset.state = kind;
  }

  function tellOpener(message, transfer) {
    if (window.opener && castleOrigin) {
      window.opener.postMessage(message, castleOrigin, transfer || []);
    }
  }

  async function connect() {
    connected = false;
    reconnect.disabled = true;
    show('Connecting to Castle Radio tools…', 'connecting');
    try {
      if (!localService()) { throw new Error('Open this helper from Castle Studio on this Mac.'); }
      const expected = requestedCastle();
      if (!expected) { throw new Error('The castle address is missing or invalid.'); }
      const response = await fetch('/radio/tools', {cache: 'no-store'});
      if (!response.ok) { throw new Error('Castle Radio tools did not answer.'); }
      const status = await response.json();
      if (status.service !== 'castle-radio' || status.protocol !== 1 || status.castle_origin !== expected) {
        throw new Error('This helper was opened by a different castle.');
      }
      castleOrigin = expected;
      connected = true;
      show('Connected to the castle. Keep this window open.', 'ready');
      tellOpener({type: 'castle-tools-ready', service: 'castle-radio', protocol: 1, status});
    } catch (error) {
      castleOrigin = '';
      show(error instanceof Error ? error.message : 'Could not connect.', 'error');
    } finally {
      reconnect.disabled = false;
    }
  }

  function checkedRequest(message) {
    if (message?.type !== 'castle-tools-request' ||
        !['string', 'number'].includes(typeof message.id) || typeof message.path !== 'string') {
      throw new Error('Invalid bridge request.');
    }
    if (!message.path.startsWith('/') || message.path.startsWith('//')) {
      throw new Error('That tool path is not available.');
    }
    const url = new URL(message.path, location.origin);
    if (url.origin !== location.origin) { throw new Error('That tool path is not available.'); }
    const method = String(message.method || 'GET').toUpperCase();
    if (!rules.some(([verb, pattern]) => verb === method && pattern.test(url.pathname))) {
      throw new Error('That tool path is not available.');
    }
    const headers = new Headers();
    for (const [name, value] of Object.entries(message.headers || {})) {
      if (!allowedHeaders.has(name.toLowerCase()) || typeof value !== 'string') {
        throw new Error('That request header is not available.');
      }
      headers.set(name, value);
    }
    const init = {method, headers, cache: 'no-store'};
    if (!['GET', 'HEAD'].includes(method) && message.body != null) { init.body = message.body; }
    return {id: message.id, url: url.pathname + url.search, init};
  }

  async function relay(event) {
    if (!connected || event.source !== window.opener || event.origin !== castleOrigin) { return; }
    let request;
    try {
      request = checkedRequest(event.data);
      const response = await fetch(request.url, request.init);
      const body = await response.arrayBuffer();
      tellOpener({
        type: 'castle-tools-response', id: request.id, status: response.status,
        headers: {'Content-Type': response.headers.get('Content-Type') || ''}, body,
      }, [body]);
    } catch (error) {
      const id = request?.id ?? event.data?.id;
      const body = new TextEncoder().encode(JSON.stringify({
        error: error instanceof Error ? error.message : 'Bridge request failed.',
      })).buffer;
      tellOpener({
        type: 'castle-tools-response', id, status: 400,
        headers: {'Content-Type': 'application/json'}, body,
      }, [body]);
    }
  }

  window.addEventListener('message', relay);
  reconnect.addEventListener('click', connect);
  connect();
})();
