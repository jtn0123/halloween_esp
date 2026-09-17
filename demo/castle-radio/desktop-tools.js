/* Desktop readiness is informational: ordinary playback stays available. */
(() => {
  const byId = id => document.getElementById(id);
  const state = byId('tools-state');
  const summary = byId('tools-summary');
  const details = byId('tools-setup');
  const checks = byId('tools-checks');
  const retry = byId('tools-recheck');
  const open = byId('tools-open');
  const start = byId('tools-start');
  // A navigation link, not an external dependency of the self-contained page.
  open.href = 'http://127.0.0.1:8871/';
  let busy = false;

  function show(label, message, attention) {
    state.textContent = label;
    state.dataset.state = attention ? 'attention' : 'ready';
    summary.textContent = message;
  }

  async function refresh() {
    if (busy) { return; }
    const connect = byId('tools-connect');
    connect.hidden = !window.castleDirect;
    start.hidden = !window.castleDirect || !!window.castleDesktop?.connected;
    if (window.castleDirect && !window.castleDesktop?.connected) {
      show('Connect your Mac',
        'Click Start Mac tools, allow Castle Tools to open, then click Connect Mac tools. You can import and split songs here while the castle handles playback.', false);
      open.hidden = true;
      retry.hidden = true;
      connect.textContent = 'Connect Mac tools';
      return;
    }
    retry.hidden = false;
    if (window.castleDirect) { connect.textContent = 'Reconnect Mac tools'; }
    busy = true;
    retry.disabled = true;
    try {
      const response = await fetch('/radio/tools', {signal: AbortSignal.timeout(15000), cache: 'no-store'});
      if (!response.ok) { throw new Error('Unavailable'); }
      const result = await response.json();
      if (result.service !== 'castle-radio' || result.protocol !== 1 || !Array.isArray(result.checks)) {
        throw new Error('Different service');
      }
      checks.replaceChildren();
      for (const check of result.checks) {
        const row = document.createElement('li');
        row.textContent = `${check.ok ? '✓' : 'Needs attention:'} ${check.name} — ${check.detail}`;
        checks.append(row);
      }
      if (result.install_command) { byId('tools-install').textContent = result.install_command; }
      show(result.ready ? 'Desktop tools connected' : 'Connected · setup needs attention',
        result.ready ? 'Ready to import music, split voice and background, and generate light previews.'
          : 'Your studio is running. Review the checks below before preparing a new song. Existing songs remain available.',
        !result.ready);
      if (!result.ready) { details.open = true; }
      open.hidden = true;
    } catch {
      checks.replaceChildren();
      show('Desktop tools not connected',
        'Click Start Mac tools, then Connect Mac tools. If the browser cannot open Castle Tools, follow Setup & startup below.', true);
      details.open = true;
      open.hidden = false;
    } finally {
      busy = false;
      retry.disabled = false;
    }
  }

  start.addEventListener('click', () => {
    show('Starting Mac tools…', 'Allow your browser to open Castle Tools, then click Connect Mac tools. If nothing opens, expand Setup & startup to enable this once on your Mac.', false);
    details.open = true;
  });

  byId('tools-connect').addEventListener('click', () => {
    try { window.castleDesktop.connect(); show('Connecting to your Mac…', 'Keep the small connection window open. You can return to this page while it works.', false); }
    catch (error) { show('Connection window blocked', error.message, true); }
  });
  window.addEventListener('castle-tools-connection', refresh);
  retry.addEventListener('click', refresh);
  window.addEventListener('focus', refresh);
  refresh();
})();
