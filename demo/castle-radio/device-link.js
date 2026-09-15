/* One link to the castle: a single poll that every panel shares, the castle's
   own clock on the scrubber, and the queue carried from song to song. */
/* global stopped, blacked, switchEpoch, switching */
(() => {
  const target = $('output-target');
  const chip = $('castle-chip');
  let data = null, state = null, lightShow = null, clock = null, caps = {}, received = 0, error = '';
  let busy = false, polling = false, timer = null, epoch = 0;
  let wasPlaying = false, idlePolls = 0, userStopped = true, volumeTouched = 0, pirTouched = 0, advancedFor = '';
  const listeners = new Set();
  const onCastle = () => target.value === 'castle';
  const hasTrack = s => !!s && ((s.scene !== 'stop' && s.scene !== '') || !!s.track);
  // 5.52 firmware says whether the audio pipeline is running; older builds
  // only name a scene or a track, which never clears when a raw file ends.
  const isPlaying = s => !!s && (caps.position ? !!s.playing || !!s.settling : hasTrack(s));
  const sceneId = t => t.key ? null : t.file.replace(/^\d+_/, '').replace(/\.mp3$/, '');
  const friendly = message => /timed out/i.test(message) ? 'Castle not answering · request timed out'
    : /refused|unreachable|No route|Errno/i.test(message) ? 'Castle unreachable at 10.27.27.81'
    : /Failed to fetch|NetworkError/i.test(message) ? 'Control room server is not running' : message;
  function remoteScene() { return state && state.scene && state.scene !== 'stop' ? sceneData.find(s => s.id === state.scene) : null; }
  // What the castle is playing, as a library entry: an installed scene by its
  // id, an imported file by its synced name, or a scene track by its stem.
  function remoteTrack() {
    if (!state) {return null;}
    if (state.scene && state.scene !== 'stop') {return tracks.find(t => sceneId(t) === state.scene) || null;}
    if (!state.track) {return null;}
    return window.remoteLibrary?.trackByFilename(state.track)
      || tracks.find(t => !t.key && (t.file === state.track || t.file.replace(/\.mp3$/, '') === state.track)) || null;
  }
  function remoteTitle() {
    if (!state) {return '';}
    const what = state.track || (hasTrack(state) ? state.scene : '');
    return what ? (remoteTrack()?.title || what) : '';
  }
  function remoteTime() {
    if (!state || !clock || !isPlaying(state)) {return 0;}
    const elapsed = clock.position_s + (performance.now() - received) / 1000;
    const scene = remoteScene();
    const duration = (scene?.dur || tracks[current].duration * 1000) / 1000;
    if (!duration) {return elapsed;}
    return scene?.loop ? elapsed % duration : Math.min(elapsed, duration);
  }
  async function api(path, body) {
    const response = await fetch(path, body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {});
    const payload = await response.json();
    if (!response.ok || payload.error) {throw new Error(friendly(payload.error || 'Castle connection failed'));}
    return payload;
  }
  function paintMotion() {
    const note = $('motion-note');
    if (!state) { note.textContent = 'Motion settings are sent to the castle when it is connected. The castle is unavailable right now.'; return; }
    const pir = state.pir || {};
    if (performance.now() - pirTouched > 1500) {
      $('motion').checked = !!pir.armed;
      const cooldown = `${pir.cooldown_s} seconds`;
      if ([...$('cooldown').options].some(o => o.value === cooldown)) {$('cooldown').value = cooldown;}
    }
    note.textContent = `Live from the castle · motion ${pir.armed ? 'armed' : 'off'} · triggers “${pir.scene || '—'}” · ${pir.cooldown_s ?? '—'} s cooldown. “While music is playing” stays a preview preference.`;
  }
  function paint() {
    const online = !!state, playing = isPlaying(state), name = remoteTitle();
    chip.className = `castle-chip ${!online ? 'offline' : playing ? 'playing' : 'online'}`;
    chip.textContent = !online ? 'Castle offline' : state.settling ? 'Castle · starting…' : playing ? `Castle · ${name} · ${fmt(remoteTime())}` : `Castle ${state.version} · idle`;
    chip.title = online ? `Firmware ${state.version} · ${caps.position ? 'castle clock' : 'estimated clock'}` : error;
    target.options[1].textContent = `Porch castle · ${!online ? 'offline' : playing ? 'playing' : 'ready'}`;
    $('live-connection').textContent = online ? 'Connected' : 'Unavailable';
    $('live-health').textContent = online ? `Firmware ${state.version} · ${state.sd_mounted ? 'SD ready' : 'SD unavailable'} · ${caps.position ? 'castle clock' : 'estimated clock'}` : (error || 'Cannot reach castle');
    $('live-destination').textContent = onCastle() ? 'Porch castle' : 'This computer';
    $('live-ready').textContent = online ? `${state.scenes.split(',').filter(s => s !== 'stop').length} installed shows` : 'Connection needed';
    paintMotion();
    if (!onCastle()) {return;}
    $('preview-seek').disabled = true; $('seek').disabled = true;
    $('seek').title = 'Seeking is not supported by the castle firmware';
    $('split-state').textContent = !online ? (error || 'Castle unavailable') : caps.position ? 'Following the castle’s own clock · seeking unavailable' : 'Following castle · estimated timing · seeking unavailable';
    const label = busy ? 'Sending…' : playing ? '■ Stop castle' : '▶ Play on castle';
    $('preview-toggle').textContent = label;
    $('hero-play').textContent = label;
    $('toggle').textContent = playing ? '■' : '▶';
    $('toggle').setAttribute('aria-label', playing ? 'Stop castle' : 'Play on castle');
    $('queue-description').textContent = !caps.track_end ? 'Manual skip on castle · automatic queue needs firmware 5.52' : shuffle ? 'Shuffle is on · the castle plays what comes next' : 'The castle plays your queue in order';
    $('current-detail').textContent = busy ? 'Sending command to castle…' : !online ? `${error || 'Castle unavailable'} · retry Play` : state.settling ? 'Starting on castle…' : playing ? `Playing on castle · ${name}${lightShow?.active ? ' · generated lights live' : ''}` : 'Castle idle · press Play';
    for (const id of ['toggle', 'hero-play', 'shuffle', 'repeat']) {$(id).disabled = busy;}
    $('next').disabled = busy || (!queue.length && !repeat);
    $('previous').disabled = busy || !history.length;
    $('elapsed').textContent = fmt(remoteTime());
    $('duration').textContent = fmt(tracks[current].duration);
    $('seek').value = tracks[current].duration ? remoteTime() / tracks[current].duration * 1000 : 0;
    if (online && performance.now() - volumeTouched > 1500 && Number($('volume').value) !== state.volume) { $('volume').value = state.volume; syncVolume(); }
  }
  // Only what the castle can play moves the queue on: an installed scene or a
  // synced import. Anything else is skipped with a word, not a sync dialog.
  const playableOnCastle = t => !t.deleted && (sceneId(t) ? true : !!window.remoteLibrary?.item(t)?.audio);
  function advance() {
    if (remoteScene()?.loop && tracks[current].kind !== 'song') {return;}
    const skipped = [];
    while (queue.length && !playableOnCastle(tracks[queue[0]])) {skipped.push(tracks[queue.shift()].title);}
    if (skipped.length) {toast(`Skipped ${skipped.join(', ')} · not synced to the castle`);}
    if (queue.length || repeat) { if (!skipped.length) {toast('Song finished · the castle plays the next one');} next(); }
    else { toast('End of the queue · the castle is idle'); renderQueue(); updatePlayer(); }
  }
  function follow() {
    const playing = isPlaying(state);
    const known = remoteTrack();
    if (known && known.id !== current && !state.settling) {load(known.id);}
    if (playing) {
      wasPlaying = true; idlePolls = 0; stopped = false; blacked = false;
      // A song installed as a looping scene never ends on the castle; the
      // castle's clock says when the song itself is over, and the queue moves.
      const scene = remoteScene();
      if (scene?.loop && tracks[current].kind === 'song' && caps.position && !state.settling && (queue.length || repeat)) {
        const elapsed = clock.position_s + (performance.now() - received) / 1000;
        if (elapsed >= scene.dur / 1000 - 0.5 && advancedFor !== `${state.scene}@${clock.position_s - elapsed}`) { advancedFor = `${state.scene}@${clock.position_s - elapsed}`; advance(); }
      }
      return;
    }
    if (!wasPlaying || state.settling) { stopped = true; return; }
    // A looping scene re-fires its audio every 30 s; wait longer before calling that an ending.
    if (++idlePolls < (remoteScene()?.loop ? 5 : 2)) {return;}
    wasPlaying = false; idlePolls = 0; stopped = true;
    if (!userStopped && caps.track_end) {advance();}
  }
  async function refresh() {
    if (polling || busy || window.remoteLibrary?.syncing()) {return;}
    polling = true;
    const started = epoch;
    try {
      const next = await api('/radio/device');
      // A poll that left before a command and lands after it describes the
      // castle the user just changed; dropping it is what keeps Stop stopped.
      if (started !== epoch) {return;}
      data = next; state = data.state; lightShow = data.light_show; clock = data.playback; caps = data.capabilities || {}; received = performance.now(); error = '';
      if (onCastle()) {follow();}
      $('live-state').textContent = state.show_on ? 'Installed playlist running on castle' : state.track ? `Castle audio: ${state.track}${lightShow?.active ? ' · generated lights live' : ''}` : hasTrack(state) ? `Castle scene: ${state.scene}` : 'Castle idle';
    } catch (e) { state = null; lightShow = null; clock = null; error = e.message; $('live-state').textContent = e.message; }
    finally { polling = false; paint(); for (const fn of listeners) {fn({connected: !!state, state, data, caps, lightShow, error});} }
  }
  async function command(body) {
    if (busy) {return false;}
    busy = true; epoch++; paint();
    try { await api('/radio/device/command', body); return true; }
    catch (e) { $('live-state').textContent = e.message; toast(e.message); return false; }
    finally {
      busy = false;
      while (polling) {await new Promise(r => setTimeout(r, 40));}
      await refresh();
    }
  }
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(async () => { await refresh(); schedule(); }, document.hidden ? 4000 : 1000);
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) {refresh();} schedule(); });
  window.castleLink = {
    subscribe(fn) { listeners.add(fn); if (data || error) {fn({connected: !!state, state, data, caps, lightShow, error});} return () => listeners.delete(fn); },
    refresh, command, state: () => state, capabilities: () => caps
  };
  window.castlePlayer = {
    active: onCastle,
    time: remoteTime,
    playing: () => isPlaying(state),
    paint,
    async play() {
      audio.pause(); userStopped = false;
      const t = tracks[current], scene = sceneId(t);
      if (!scene) {
        await window.remoteLibrary?.ensure();
        const item = window.remoteLibrary?.item(t);
        if (item?.audio && item.filename) { await command({action: 'file', file: item.filename, key: t.key}); return; }
        window.remoteLibrary?.offer(t); return;
      }
      await command({action: 'scene', scene});
    },
    toggle() { return isPlaying(state) && !busy ? this.stop() : this.play(); },
    stop() { audio.pause(); userStopped = true; wasPlaying = false; idlePolls = 0; stopped = true; return command({action: 'stop'}); }
  };
  target.onchange = () => {
    audio.pause(); stopped = true;
    for (const id of ['seek', 'preview-seek', 'shuffle', 'repeat', 'previous', 'next']) { $(id).disabled = false; $(id).title = ''; }
    switchEpoch++; switching = false;
    syncLayer(); renderQueue();
    updatePlayer();
    if (onCastle() && state) { $('volume').value = state.volume; syncVolume(); }
    if (onCastle()) {toast(state ? 'Castle selected · Play sends the song to the porch and the queue follows' : 'Castle selected, but it is not answering right now');}
    else {toast('Computer preview selected · the castle keeps whatever it is doing');}
    refresh();
  };
  const localBlackout = $('blackout').onclick;
  $('blackout').onclick = () => { if (!onCastle()) {return localBlackout();} userStopped = true; wasPlaying = false; blacked = true; stopped = true; return command({action: 'blackout'}); };
  const localVolume = $('volume').oninput;
  const localSettingsVolume = $('settings-volume').oninput;
  $('volume').oninput = () => { volumeTouched = performance.now(); if (!onCastle()) {localVolume();} };
  $('volume').onchange = () => { if (onCastle()) {command({action: 'volume', volume: Number($('volume').value)});} };
  $('settings-volume').oninput = () => { volumeTouched = performance.now(); if (!onCastle()) {localSettingsVolume();} else { $('volume').value = $('settings-volume').value; syncVolume(); } };
  $('settings-volume').onchange = () => { if (onCastle()) {command({action: 'volume', volume: Number($('settings-volume').value)});} };
  const sendMotion = () => {
    pirTouched = performance.now();
    if (!state) { toast('Castle unavailable · motion settings were not sent'); return; }
    command({action: 'pir', armed: $('motion').checked, cooldown: parseInt($('cooldown').value, 10)}).then(ok => { if (ok) {toast(`Motion ${$('motion').checked ? 'armed' : 'off'} on the castle`);} });
  };
  $('motion').addEventListener('change', sendMotion);
  $('cooldown').addEventListener('change', sendMotion);
  $('live-show-start').onclick = () => command({action: 'show/start'});
  $('live-show-stop').onclick = () => { userStopped = true; wasPlaying = false; command({action: 'stop'}); };
  refresh().then(schedule);
  setInterval(() => { if (onCastle() || state) {paint();} if (onCastle()) {drawPlayheads();} }, 100);
})();
