/* One link to the castle: a single poll that every panel shares, the castle's
   own clock on the scrubber, and the queue carried from song to song. */
/* global $, audio, blacked, current, drawPlayheads, fmt, history, load, next, queue, renderQueue, repeat, sceneData, shuffle, stopped, switchEpoch, switching, syncLayer, syncVolume, toast, tracks, updatePlayer */
(() => {
  const target = $('output-target');
  const chip = $('castle-chip');
  let data = null, state = null, lightShow = null, clock = null, caps = {}, received = 0, error = '';
  let busy = false, inflight = null, timer = null, epoch = 0, polled = -Infinity, lastError = '';
  let wasPlaying = false, idlePolls = 0, userStopped = true, volumeTouched = 0, pirTouched = 0, advancedFor = '';
  const listeners = new Set();
  const onCastle = () => target.value === 'castle';
  const hasTrack = s => !!s && ((s.scene !== 'stop' && s.scene !== '') || !!s.track);
  // 5.52 firmware says whether the audio pipeline is running; older builds
  // only name a scene or a track, which never clears when a raw file ends.
  const isPlaying = s => !!s && (caps.position ? !!s.playing : hasTrack(s));
  // B51: a command that has not landed yet is STARTING, not playing. Folding
  // it into isPlaying offered "Stop castle" for a scene the castle had not
  // left: the press stopped the old scene and the new one arrived anyway.
  const isStarting = s => !!s && !!s.settling;
  const isLive = s => isPlaying(s) || isStarting(s);
  const sceneId = t => t.key ? null : t.file.replace(/^\d+_/, '').replace(/\.mp3$/, '');
  function friendly(message) {
    if (/timed out/i.test(message)) {return 'Castle not answering · request timed out';}
    if (/refused|unreachable|No route|Errno/i.test(message)) {return 'Castle unreachable at 10.27.27.81';}
    if (/Failed to fetch|NetworkError/i.test(message)) {return 'Control room server is not running';}
    return message;
  }
  function remoteScene() {
    return state?.scene && state.scene !== 'stop' ? sceneData.find(s => s.id === state.scene) : null;
  }
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
  const castleClockLabel = () => caps.position ? 'castle clock' : 'estimated clock';
  function chipStatus(online, playing) {
    if (!online) {return 'offline';}
    return playing ? 'playing' : 'online';
  }
  function chipText(online, playing, name) {
    if (!online) {return 'Castle offline';}
    if (state.settling) {return 'Castle · starting…';}
    if (playing) {return `Castle · ${name} · ${fmt(remoteTime())}`;}
    return `Castle ${state.version} · idle`;
  }
  function targetStatus(online, playing) {
    if (!online) {return 'offline';}
    return playing ? 'playing' : 'ready';
  }
  function paintChip(online, playing, name) {
    chip.className = `castle-chip ${chipStatus(online, playing)}`;
    chip.textContent = chipText(online, playing, name);
    chip.title = online ? `Firmware ${state.version} · ${castleClockLabel()}` : error;
    target.options[1].textContent = `Porch castle · ${targetStatus(online, playing)}`;
  }
  function healthText(online) {
    if (!online) {return error || 'Cannot reach castle';}
    const sd = state.sd_mounted ? 'SD ready' : 'SD unavailable';
    return `Firmware ${state.version} · ${sd} · ${castleClockLabel()}`;
  }
  function paintDevicePanel(online) {
    $('live-connection').textContent = online ? 'Connected' : 'Unavailable';
    $('live-health').textContent = healthText(online);
    $('live-destination').textContent = onCastle() ? 'Porch castle' : 'This computer';
    $('live-ready').textContent = online
      ? `${state.scenes.split(',').filter(s => s !== 'stop').length} installed shows`
      : 'Connection needed';
  }
  function splitStateText(online) {
    if (!online) {return error || 'Castle unavailable';}
    if (caps.position) {return 'Following the castle’s own clock · seeking unavailable';}
    return 'Following castle · estimated timing · seeking unavailable';
  }
  function playLabel(playing, starting) {
    if (busy) {return 'Sending…';}
    if (starting) {return '… Starting on castle';}
    return playing ? '■ Stop castle' : '▶ Play on castle';
  }
  function queueDescription() {
    if (!caps.track_end) {return 'Manual skip on castle · automatic queue needs firmware 5.52';}
    return shuffle ? 'Shuffle is on · the castle plays what comes next' : 'The castle plays your queue in order';
  }
  function currentDetail(online, playing, name) {
    if (busy) {return 'Sending command to castle…';}
    if (!online) {return `${error || 'Castle unavailable'} · retry Play`;}
    if (state.settling) {return 'Starting on castle…';}
    if (playing) {return `Playing on castle · ${name}${lightShow?.active ? ' · generated lights live' : ''}`;}
    return 'Castle idle · press Play';
  }
  function paintTransport(online, playing, name) {
    $('preview-seek').disabled = true; $('seek').disabled = true;
    $('seek').title = 'Seeking is not supported by the castle firmware';
    $('split-state').textContent = splitStateText(online);
    const starting = isStarting(state);
    const label = playLabel(playing, starting);
    $('preview-toggle').textContent = label;
    $('hero-play').textContent = label;
    $('toggle').textContent = starting ? '…' : (playing ? '■' : '▶');
    $('toggle').setAttribute('aria-label', starting ? 'Starting on castle' : (playing ? 'Stop castle' : 'Play on castle'));
    $('queue-description').textContent = queueDescription();
    $('current-detail').textContent = currentDetail(online, playing, name);
    // Nothing to press while the command is in the air: the transport waits
    // for the castle rather than offering a stop of the scene it is leaving.
    for (const id of ['toggle', 'hero-play', 'preview-toggle']) {$(id).disabled = busy || starting;}
    for (const id of ['shuffle', 'repeat']) {$(id).disabled = busy;}
    $('next').disabled = busy || (!queue.length && !repeat);
    $('previous').disabled = busy || (!history.length && !(playing && remoteTime() > 3));
  }
  function paintClock(online) {
    $('elapsed').textContent = fmt(remoteTime());
    $('duration').textContent = fmt(tracks[current].duration);
    $('seek').value = tracks[current].duration ? remoteTime() / tracks[current].duration * 1000 : 0;
    if (online && performance.now() - volumeTouched > 1500 && Number($('volume').value) !== state.volume) {
      $('volume').value = state.volume;
      syncVolume();
    }
  }
  function paint() {
    const online = !!state, playing = isPlaying(state), name = remoteTitle();
    paintChip(online, playing, name);
    paintDevicePanel(online);
    paintMotion();
    if (!onCastle()) {return;}
    paintTransport(online, playing, name);
    paintClock(online);
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
        // The key counts whole loop cycles, not the live clock: a key built
        // from position_s changed on every poll and advanced the queue once a
        // second, and "past the threshold this cycle" still fires when a
        // hidden tab polls every 4 s and lands well after the 0.5 s window.
        const cycle = Math.floor((elapsed + 0.5) / (scene.dur / 1000));
        if (cycle >= 1 && advancedFor !== `${state.scene}@${cycle}`) { advancedFor = `${state.scene}@${cycle}`; advance(); }
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
    if (inflight || busy) {return;}
    // A background copy must not blind the header clock, the scrubber or the
    // queue for the length of the transfer; it only slows the poll down.
    if (window.remoteLibrary?.syncing() && performance.now() - polled < 2000) {return;}
    polled = performance.now();
    inflight = poll();
    try { await inflight; } finally { inflight = null; }
  }
  function liveStateText() {
    if (state.show_on) {return 'Installed playlist running on castle';}
    if (state.track) {return `Castle audio: ${state.track}${lightShow?.active ? ' · generated lights live' : ''}`;}
    if (hasTrack(state)) {return `Castle scene: ${state.scene}`;}
    return 'Castle idle';
  }
  async function poll() {
    const started = epoch;
    try {
      const next = await api('/radio/device');
      // A poll that left before a command and lands after it describes the
      // castle the user just changed; dropping it is what keeps Stop stopped.
      if (started !== epoch) {return;}
      data = next; state = data.state; lightShow = data.light_show; clock = data.playback; caps = data.capabilities || {}; received = performance.now(); error = '';
      if (onCastle()) {follow();}
      $('live-state').textContent = liveStateText();
    } catch (e) { state = null; lightShow = null; clock = null; error = e.message; $('live-state').textContent = e.message; }
    finally { paint(); for (const fn of listeners) {fn({connected: !!state, state, data, caps, lightShow, error});} }
  }
  async function command(body) {
    if (busy) {return false;}
    busy = true; epoch++; paint();
    try { lastError = ''; await api('/radio/device/command', body); return true; }
    catch (e) { lastError = e.message; $('live-state').textContent = e.message; toast(e.message); return false; }
    finally {
      busy = false;
      // A poll already in flight finishes (and is dropped by the epoch guard) before the fresh one.
      if (inflight) {await inflight;}
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
    refresh, command, state: () => state, capabilities: () => caps, lastError: () => lastError
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
        const inventory = await window.remoteLibrary?.ensure();
        const item = window.remoteLibrary?.item(t);
        if (item?.audio && item.filename) { await command({action: 'file', file: item.filename, key: t.key}); return; }
        // ensure() gives up after 6 s. A slow SD listing then looks exactly
        // like a missing file, and the sync dialog is the wrong answer.
        if (!inventory) { toast('Castle listing slow · press Play again'); window.remoteLibrary?.retry(); return; }
        window.remoteLibrary?.offer(t); return;
      }
      await command({action: 'scene', scene});
    },
    toggle() { if (isStarting(state)) {return false;} return isPlaying(state) && !busy ? this.stop() : this.play(); },
    // Previous on the castle cannot read audio.currentTime (nothing is loaded
    // here), so the castle's own clock decides restart-or-go-back.
    previous() { if (busy || !isPlaying(state) || remoteTime() <= 3) {return false;} this.play(); return true; },
    owns(t) { return !userStopped && isLive(state) && !!t && remoteTrack()?.id === t.id; },
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
    command({action: 'pir', armed: $('motion').checked, cooldown: Number.parseInt($('cooldown').value, 10)}).then(ok => { if (ok) {toast(`Motion ${$('motion').checked ? 'armed' : 'off'} on the castle`);} });
  };
  $('motion').addEventListener('change', sendMotion);
  $('cooldown').addEventListener('change', sendMotion);
  // B53: playlist mode is not radio mode. Starting the installed show parks
  // the radio queue (userStopped) and clears the follow state, so the dark
  // gaps between playlist scenes are not read as "this song ended".
  $('live-show-start').onclick = () => { userStopped = true; wasPlaying = false; idlePolls = 0; advancedFor = ''; command({action: 'show/start'}); };
  // /api/stop is scene_stop only; the generated playlist steps on after the
  // gap. Ending the evening is /api/show/stop.
  $('live-show-stop').onclick = () => { userStopped = true; wasPlaying = false; idlePolls = 0; advancedFor = ''; command({action: 'show/stop'}); };
  refresh().then(schedule);
  setInterval(() => { if (onCastle() || state) {paint();} if (onCastle()) {drawPlayheads();} }, 100);
})();
