/* Help with your castle — the card on Your castle for when something is
 * wrong, and the motion card's honesty about a castle with no sensor.
 *
 * - Copy diagnostics / Save as a file: /radio/diagnostics (diagnostics.py),
 *   the castle's own problem report (firmware/sd_web_owner.h, v5.75) with
 *   this computer's half after it, folder names cut to file names and no
 *   castle key. Nothing is sent anywhere: the text goes to the clipboard or
 *   a file the owner chooses to share, and is shown on the card first so
 *   they can read what they are sharing.
 * - The castle's own page, /owner (v5.75), linked at the address this app
 *   knows — the page that still works when this computer is not around.
 * - A castle with no motion sensor (status `pir.fitted` false, v5.75: the
 *   buyer build) has the motion switch and its two settings taken away, and
 *   the card says why. Older firmware does not say, and keeps them.
 *
 * On the castle-served page there is no computer half to report: the card
 * links /owner, whose "Report a problem" is the castle's report. */
(() => {
  const byId = id => document.getElementById(id);
  const direct = !!window.castleDirect;
  const NO_SENSOR = 'Motion sensor: not fitted on this castle — it has nothing to arm. The castle’s own page says the same.';

  function el(tag, props = {}, ...kids) {
    const node = document.createElement(tag);
    Object.assign(node, props);
    node.append(...kids);
    return node;
  }

  const owner = el('a', {id: 'help-owner', target: '_blank', rel: 'noopener', textContent: 'Open the castle’s own page'});
  const ownerNote = el('span', {id: 'help-owner-note', className: 'subtle'});
  const copy = el('button', {id: 'help-copy', textContent: '⧉ Copy diagnostics'});
  const save = el('button', {id: 'help-save', textContent: '⤓ Save as a file'});
  const message = el('output', {id: 'help-message', className: 'subtle'});
  const shown = el('textarea', {id: 'help-text', readOnly: true, hidden: true, rows: 10});
  const card = el('article', {id: 'castle-help'},
    el('div', {className: 'eyebrow', textContent: 'WHEN SOMETHING IS WRONG'}),
    el('h2', {textContent: 'Help with your castle'}),
    el('p', {}, owner, ownerNote),
    ...(direct ? [] : [
      el('div', {className: 'live-device-actions'}, copy, save),
      message, shown,
      el('p', {className: 'subtle', textContent: 'Diagnostics hold this app’s version, the castle’s status, health, restarts and recent events, the last imports and syncs, and the app’s log — folder names cut to file names, and never the castle key. Nothing is sent anywhere: you choose who sees it.'}),
    ]));
  message.setAttribute?.('aria-live', 'polite');
  const find = byId('castle-find');
  if (find?.parentNode) {find.parentNode.insertBefore(card, find.nextSibling);}

  function paintOwner(host, connected) {
    const href = direct ? '/owner' : host ? `http://${host}/owner` : '';
    owner.hidden = !href;
    if (href) {owner.href = href;}
    if (direct) { ownerNote.textContent = ' — settings, songs and “Report a problem”, straight from the castle.'; return; }
    if (!host) { ownerNote.textContent = 'Find your castle first; its own page is then one click from here.'; return; }
    ownerNote.textContent = connected === false
      ? ` — at ${host}, which is not answering right now.`
      : ` — at ${host}: settings, songs and “Report a problem”, straight from the castle.`;
  }

  // The motion card's controls, not the card: its note is where the reason goes.
  const motionRows = () => ['motion', 'interrupt', 'cooldown']
    .map(id => byId(id)?.closest?.('label')).filter(Boolean);
  function paintMotion(pir) {
    const missing = !!pir && typeof pir === 'object' && pir.fitted === false;
    for (const row of motionRows()) {row.hidden = missing;}
    if (missing && byId('motion-note')) {byId('motion-note').textContent = NO_SENSOR;}
  }

  async function diagnostics() {
    message.textContent = 'Gathering diagnostics…';
    const response = await fetch('/radio/diagnostics', {cache: 'no-store'});
    const answer = await response.json().catch(() => ({}));
    if (!response.ok || typeof answer.text !== 'string') {throw new Error(answer.error || `diagnostics failed (${response.status})`);}
    shown.value = answer.text;
    shown.hidden = false;
    return answer;
  }
  function download(answer) {
    const link = el('a', {href: URL.createObjectURL(new Blob([answer.text], {type: 'text/plain'})), download: answer.name});
    document.body.append(link);
    link.click();
    link.remove();
  }
  async function run(action) {
    copy.disabled = save.disabled = true;
    try { message.textContent = await action(await diagnostics()); }
    catch (e) { message.textContent = `Could not gather diagnostics: ${e?.message || e}`; }
    finally { copy.disabled = save.disabled = false; }
  }
  copy.onclick = () => run(async answer => {
    try {
      await navigator.clipboard.writeText(answer.text);
      return 'Copied. Paste it into a message to whoever is helping — it is shown below so you can read it first.';
    } catch {
      shown.select?.();
      download(answer);
      return `This browser would not copy, so it was saved as ${answer.name} instead — it is shown below as well.`;
    }
  });
  save.onclick = () => run(async answer => { download(answer); return `Saved as ${answer.name}.`; });

  paintOwner('', null);
  window.castleLink?.subscribe(snapshot => {
    paintOwner(snapshot.host || '', !!snapshot.connected);
    paintMotion(snapshot.state?.pir);
  });
  window.castleHelp = {paintOwner, paintMotion, NO_SENSOR};
})();
