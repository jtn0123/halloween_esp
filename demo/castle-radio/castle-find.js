/* Find my castle — the card on Your castle (castle_finder.py).
 *
 * One clear state for "the castle is not answering": the card says whether
 * no castle has been found yet or the one this app knows has gone quiet at
 * its address, and offers the same two ways forward either way — Find my
 * castle (a one-shot mDNS browse from this computer, tools/castle_find.py)
 * and an address typed by hand. Whatever the owner picks is asked first and
 * only then remembered (tools/castle_address.py), in the store the castle
 * key lives in — which the light desk reads too, so both follow.
 *
 * The page the castle serves has no use for it (the castle IS that page's
 * host) and hides it. The header chip, while the castle is offline, opens
 * this card. */
(() => {
  const byId = id => document.getElementById(id);
  const card = byId('castle-find');
  if (!card) {return;}
  if (window.castleDirect) { card.hidden = true; return; }
  const state = byId('find-state');
  const results = byId('find-results');
  const message = byId('find-message');
  const typed = byId('find-address');
  const chip = byId('castle-chip');
  let where = {host: '', pinned: false, store: true};
  let online = null;

  async function ask(path, body) {
    const init = body === undefined ? {cache: 'no-store'}
      : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)};
    const response = await fetch(path, init);
    const answer = await response.json().catch(() => ({}));
    if (!response.ok) {throw new Error(answer.error || `Castle request failed (${response.status})`);}
    return answer;
  }

  function describe() {
    if (where.pinned) {return `This app is set to the castle at ${where.host} (castle_host in its settings file) · change it there.`;}
    if (!where.host) {return 'No castle found yet. Switch it on, then find it on this network.';}
    if (online === false) {return `The castle at ${where.host} is not answering. It may be switched off, or your router may have given it a new address — find it again.`;}
    return online ? `Connected to your castle at ${where.host}.` : `Your castle: ${where.host}.`;
  }

  function paint() {
    state.textContent = describe();
    byId('find-run').disabled = where.pinned;
    byId('find-use').disabled = where.pinned;
  }

  function row(castle) {
    const item = document.createElement('li');
    const label = document.createElement('span');
    const variant = castle.fw_variant ? ` · ${castle.fw_variant}` : '';
    label.textContent = `${castle.name || 'castle'} · ${castle.address} · v${castle.version}${variant}`;
    const use = document.createElement('button');
    use.textContent = castle.current ? 'This is your castle' : 'Use this castle';
    use.disabled = castle.current || where.pinned;
    use.onclick = () => adopt(castle.address, castle.name);
    item.append(label, use);
    return item;
  }

  async function find() {
    message.textContent = 'Looking for castles on this network…';
    results.replaceChildren();
    try {
      const answer = await ask('/radio/device/find', {});
      where = answer;
      paint();
      results.replaceChildren(...answer.found.map(row));
      const n = answer.found.length;
      message.textContent = n ? `${n === 1 ? 'One castle' : `${n} castles`} answered.`
        : 'No castle answered. Check it is switched on and on the same Wi-Fi as this computer, or type its address below.';
    } catch (error) { message.textContent = error.message; }
  }

  async function adopt(host, name = '') {
    message.textContent = `Asking ${host}…`;
    try {
      where = await ask('/radio/device/address', {host, name});
      online = null;
      paint();
      results.replaceChildren();
      typed.value = '';
      message.textContent = `This is your castle now · ${name || host}`;
      window.castleLink?.refresh?.();
      window.castleKey?.refresh?.();
    } catch (error) { message.textContent = error.message; }
  }

  function open() {
    document.querySelector('nav button[data-page="device"]')?.click();
    card.scrollIntoView?.({block: 'center'});
  }

  byId('find-run').onclick = find;
  byId('find-use').onclick = () => {
    const host = typed.value.trim();
    if (host) { adopt(host); } else { message.textContent = 'Type the castle’s address first'; }
  };
  chip?.addEventListener('click', () => { if (online === false) {open();} });
  window.castleLink?.subscribe(snapshot => {
    online = !!snapshot.connected;
    if (!online && chip) {chip.title = `${snapshot.error || 'Castle offline'} · click to find it`;}
    paint();
  });
  ask('/radio/device/address').then(answer => { where = answer; paint(); })
    .catch(error => { state.textContent = error.message; });
  window.castleFind = {find, adopt, open};
})();
