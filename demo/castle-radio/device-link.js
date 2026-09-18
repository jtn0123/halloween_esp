/* One link to the castle: a single poll that every panel shares, the castle's
   own clock on the scrubber, and the queue carried from song to song. */
/* global $, audio, blacked, current, drawPlayheads, fmt, history, load, next, queue, renderQueue, repeat, sceneData, shuffle, stopped, switchEpoch, switching, syncLayer, syncVolume, toast, tracks, updatePlayer */
(() => {
  const target = $('output-target');
  const chip = $('castle-chip');
  const words = window.castleWords;
  const {booting, chipStatus, clockLabel, framesText, friendly, playLabel, queueDescription, readyText,
    splitStateText, targetStatus, textOf, toggleWords, upFor, uptimeOf, versionOf} = words;
  let data = null, state = null, lightShow = null, clock = null, caps = {}, received = 0, error = '';
  let busy = false, inflight = null, timer = null, epoch = 0, polled = -Infinity, lastError = '';
  let wasPlaying = false, userStopped = true, volumeTouched = 0, pirTouched = 0, advancedFor = '';
  // B61: one dropped poll is a hiccup, not an offline castle. The last good
  // state stays on screen for three strikes, with a word about reconnecting.
  let failures = 0;
  // A TypeError in this page is not a castle that stopped answering. It gets
  // its own word and never touches the strike count or the missed-poll tally.
  let pageFault = '';
  // How the link itself is doing: one round trip, the worst of the last ten,
  // and every poll that never came back, counted for the whole evening.
  let rtt = null, missed = 0;
  const rtts = [];
  // B62: a restart of the SAME scene has to clear the advance guard, or a
  // looping song re-queued later never advances again. B65: an uptime that
  // goes backwards is a reboot, not a song.
  let followKey = '', lastElapsed = 0, playStartedAt = 0, pendingAdvance = false;
  let lastUptime = null, lastVersion = '', resumeBoot = false;
  // When the castle was last heard playing, so the end of a song is judged on
  // wall-clock silence rather than on a poll count a hidden tab stretches.
  let lastLiveAt = 0, shownShowError = '';
  const QUIET_MS = 2000, QUIET_LOOP_MS = 5000, API_TIMEOUT_MS = 8000;
  const BUSY = 'busy';
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
  const reconnecting = () => failures > 0 && !!state;
  const firmware = () => versionOf(state) || '—';
  // A phone with its screen off freezes the frame loop while the castle's
  // audio runs on; the castle page says so rather than claiming lights.
  const lightNote = () => (lightShow?.active ? ` · ${lightShow.note || framesText(lightShow) || 'generated lights live'}` : '');
  // The generated-light streamer records why it gave up; before this nothing
  // read it, so the lights stopped and the page kept saying they were live.
  const showFault = () => (shownShowError ? ` · lights stopped: ${shownShowError} · press Play to restart` : '');
  const isLive = s => isPlaying(s) || isStarting(s);
  const sceneId = t => t.key ? null : t.file.replace(/^\d+_/, '').replace(/\.mp3$/, '');
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
    const what = textOf(state.track) || (hasTrack(state) ? textOf(state.scene) : '');
    return what ? (remoteTrack()?.title || what) : '';
  }
  // Whether anything here knows how long what the castle plays runs for. An
  // unmapped file or a PIR scene is nobody's song in this library.
  const mapped = () => !isPlaying(state) || !!remoteScene() || !!remoteTrack();
  function remoteTime() {
    if (!state || !clock || !isPlaying(state)) {return 0;}
    const elapsed = clock.position_s + (performance.now() - received) / 1000;
    const scene = remoteScene();
    // Borrowing the previous song's length pinned the bar at 100% for a track
    // this page cannot name; the castle's own clock runs unclamped instead.
    if (!mapped()) {return elapsed;}
    const duration = (scene?.dur || tracks[current].duration * 1000) / 1000;
    if (!duration) {return elapsed;}
    return scene?.loop ? elapsed % duration : Math.min(elapsed, duration);
  }
  async function api(path, body) {
    // A hung socket must not pin the page on "Connected" forever: the castle
    // build aborts at 8 s inside castle-direct.js, and the computer build
    // needs the same guard of its own or refresh() never clears inflight.
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), API_TIMEOUT_MS);
    const init = {signal: abort.signal};
    if (body) { Object.assign(init, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}); }
    let response;
    try { response = await fetch(path, init); }
    catch (e) { throw new Error(e?.name === 'AbortError' ? 'Castle not answering · request timed out' : friendly(e?.message || 'Castle connection failed')); }
    finally { clearTimeout(timer); }
    const payload = await response.json();
    if (!response.ok || payload?.error) {throw new Error(friendly(payload?.error || 'Castle connection failed'));}
    return payload;
  }
  function paintMotion() {
    const note = $('motion-note');
    if (!state) { note.textContent = 'Motion settings are sent to the castle when it is connected. The castle is unavailable right now.'; return; }
    const pir = state.pir;
    if (!pir || typeof pir !== 'object') {
      // The checkbox is the control the next change event sends BACK to the
      // castle: an answer that carries no motion block must not untick it.
      note.textContent = 'Live from the castle · motion state unknown · this answer carried no motion settings, so the switch is left as you set it.';
      return;
    }
    if (performance.now() - pirTouched > 1500) {
      $('motion').checked = !!pir.armed;
      const cooldown = `${pir.cooldown_s} seconds`;
      if ([...$('cooldown').options].some(o => o.value === cooldown)) {$('cooldown').value = cooldown;}
    }
    note.textContent = `Live from the castle · motion ${pir.armed ? 'armed' : 'off'} · triggers “${pir.scene || '—'}” · ${pir.cooldown_s ?? '—'} s cooldown. “While music is playing” stays a preview preference.`;
  }
  function timed(ms, ok) {
    rtt = Math.round(ms);
    rtts.push(rtt);
    if (rtts.length > 10) {rtts.shift();}
    if (!ok) {missed++;}
  }
  const health = () => ({
    rtt_ms: rtt, worst_ms: rtts.length ? Math.max(...rtts) : null,
    failures, missed_total: missed,
    uptime_s: uptimeOf(state), uptime: upFor(uptimeOf(state)), version: versionOf(state) || null,
  });
  const healthLine = () => words.healthLine(health());
  function chipText(online, playing, name) {
    if (!online) {return 'Castle offline';}
    if (reconnecting()) {return 'Castle · reconnecting…';}
    if (state.settling) {return 'Castle · starting…';}
    // B11: the clock BEFORE the title. The chip is one ellipsised nowrap
    // line, ~200 px of 9 px text on a phone, so something has to go — and
    // it used to be the clock: "Castle · The Ballad of the Witc…".
    if (playing) {return `Castle · ${fmt(remoteTime())} · ${name}`;}
    return `Castle ${firmware()} · idle`;
  }
  function paintChip(online, playing, name) {
    chip.className = `castle-chip ${chipStatus(online, playing)}`;
    chip.textContent = chipText(online, playing, name);
    chip.title = online ? `Firmware ${firmware()} · ${clockLabel(caps)}` : error;
    target.options[1].textContent = `Porch castle · ${targetStatus(online, playing)}`;
  }
  function healthText(online) {
    if (!online) {return error || 'Cannot reach castle';}
    if (reconnecting()) {return `${error} · reconnecting (${failures} of 3)`;}
    if (booting(state)) {return `Firmware ${firmware()} · castle is starting up`;}
    const sd = state.sd_mounted ? 'SD ready' : 'SD unavailable';
    return `Firmware ${firmware()} · ${sd} · ${clockLabel(caps)}`;
  }
  function paintDevicePanel(online) {
    $('live-connection').textContent = online ? 'Connected' : 'Unavailable';
    $('live-health').textContent = healthText(online);
    $('live-destination').textContent = onCastle() ? 'Porch castle' : 'This computer';
    $('live-ready').textContent = readyText(online, state);
  }
  function currentDetail(online, playing, name) {
    if (busy) {return 'Sending command to castle…';}
    if (!online) {return `${error || 'Castle unavailable'} · retry Play`;}
    if (reconnecting()) {return 'Castle did not answer · reconnecting…';}
    if (pageFault) {return `${pageFault} · the castle is still answering`;}
    if (booting(state)) {return 'Castle is starting up · its light shows are not loaded yet';}
    if (state.settling) {return 'Starting on castle…';}
    if (playing) {return `Playing on castle · ${name}${lightNote()}${showFault()}`;}
    return `Castle idle · press Play${showFault()}`;
  }
  function paintTransport(online, playing, name) {
    $('preview-seek').disabled = true; $('seek').disabled = true;
    $('seek').title = 'Seeking is not supported by the castle firmware';
    $('split-state').textContent = splitStateText(online, error, caps);
    const starting = isStarting(state);
    const label = playLabel(busy, playing, starting);
    $('preview-toggle').textContent = label;
    $('hero-play').textContent = label;
    const [glyph, hint] = toggleWords(playing, starting);
    $('toggle').textContent = glyph;
    $('toggle').setAttribute('aria-label', hint);
    $('queue-description').textContent = queueDescription(caps, shuffle);
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
    // A track this library cannot name has no length here, and a scrubber
    // scaled by the previous song's length simply lies.
    const duration = mapped() ? tracks[current].duration : 0;
    $('duration').textContent = mapped() ? fmt(duration) : '—';
    $('seek').value = duration ? remoteTime() / duration * 1000 : 0;
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
  // B63: item() is null both for "not on the card" and for "the listing has
  // not answered yet". Only the first is a reason to throw a song away.
  const unknownOnCastle = t => !t.deleted && !sceneId(t) && !window.remoteLibrary?.known?.();
  function advance() {
    if (remoteScene()?.loop && tracks[current].kind !== 'song') {return;}
    const skipped = [];
    while (queue.length && !playableOnCastle(tracks[queue[0]])) {
      if (unknownOnCastle(tracks[queue[0]])) {
        // Wait for the listing rather than discarding the rest of the evening.
        pendingAdvance = true; window.remoteLibrary?.retry();
        toast(skipped.length ? `Skipped ${skipped.join(', ')} · not synced to the castle`
          : 'Castle listing slow · the queue is waiting');
        return;
      }
      skipped.push(tracks[queue.shift()].title);
    }
    if (skipped.length) {toast(`Skipped ${skipped.join(', ')} · not synced to the castle`);}
    if (queue.length || repeat) { if (!skipped.length) {toast('Song finished · the castle plays the next one');} next(); }
    else { toast('End of the queue · the castle is idle'); renderQueue(); updatePlayer(); }
  }
  // The castle names no track id, so a fresh start of the same scene shows up
  // only as its clock going backwards. Both are a new song for the guard.
  function watchStart(elapsed) {
    const key = `${state.scene}|${state.track}`;
    if (key !== followKey || elapsed + 1 < lastElapsed) {
      followKey = key; advancedFor = ''; playStartedAt = performance.now() - elapsed * 1000;
    }
    lastElapsed = elapsed;
  }
  // A non-looping scene whose lights outlast its audio is not over when the
  // speaker stops; only a song ends with its audio.
  function ended() {
    if (tracks[current].kind === 'song') {return true;}
    // Not remoteScene(): a castle that has fallen quiet already says "stop",
    // and the authored duration is the library's, not the status line's.
    const scene = sceneData.find(s => s.id === sceneId(tracks[current]));
    return !scene?.dur || performance.now() - playStartedAt >= scene.dur;
  }
  // The screen lock belongs to the queue, not only to a generated light show:
  // a phone that sleeps between two installed scenes stops polling, and iOS
  // drops the lock on every hide, so this is re-asked for on every poll.
  function holdForQueue() {
    const want = onCastle() && !userStopped && isLive(state);
    if (want) {window.castleDirect?.holdScreen?.('queue');}
    else {window.castleDirect?.releaseScreen?.('queue');}
  }
  // The castle is playing: the clock is live, and the watch runs. A song
  // installed as a looping scene never ends on the castle either, so the
  // castle's clock is also what says the song itself is over.
  function followPlaying() {
    wasPlaying = true; lastLiveAt = performance.now(); stopped = false; blacked = false;
    watchStart(clock.position_s + (performance.now() - received) / 1000);
    const scene = remoteScene();
    if (!(scene?.loop && tracks[current].kind === 'song' && caps.position && !state.settling && (queue.length || repeat))) {return;}
    const elapsed = clock.position_s + (performance.now() - received) / 1000;
    // The key counts whole loop cycles, not the live clock: a key built
    // from position_s changed on every poll and advanced the queue once a
    // second, and "past the threshold this cycle" still fires when a
    // hidden tab polls every 4 s and lands well after the 0.5 s window.
    const cycle = Math.floor((elapsed + 0.5) / (scene.dur / 1000));
    if (cycle >= 1 && advancedFor !== `${state.scene}@${cycle}`) { advancedFor = `${state.scene}@${cycle}`; advance(); }
  }
  function follow() {
    const playing = isPlaying(state);
    const known = remoteTrack();
    if (known && known.id !== current && !state.settling) {load(known.id);}
    if (pendingAdvance && window.remoteLibrary?.known?.()) { pendingAdvance = false; advance(); }
    if (resumeBoot && !booting(state) && !playing && !state.settling) {
      resumeBoot = false; toast('Castle restarted · starting the song again');
      window.castlePlayer.play().catch(e => toast(`Could not restart the song · ${e.message}`));
      return;
    }
    if (playing) { followPlaying(); return; }
    if (!wasPlaying || state.settling) { stopped = true; return; }
    // Two polls of silence was 2 s in a visible tab and 8 s in a hidden one
    // (20 s for a looping scene, which re-fires its audio every 30 s). The
    // wall clock is the same fact wherever the tab is.
    if (performance.now() - lastLiveAt < (remoteScene()?.loop ? QUIET_LOOP_MS : QUIET_MS)) {return;}
    wasPlaying = false; stopped = true;
    if (!userStopped && caps.track_end && ended()) {advance();}
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
  // An OTA or a brownout restarts the castle mid-song: uptime goes backwards
  // (or the version changes) and everything this page believed is stale.
  function rebooted(s) {
    const up = uptimeOf(s);
    const back = lastUptime !== null && up !== null && up < lastUptime;
    const version = versionOf(s);
    const changed = !!lastVersion && !!version && version !== lastVersion;
    if (up !== null) {lastUptime = up;}
    if (version) {lastVersion = version;}
    return back || changed;
  }
  function reboot() {
    resumeBoot = wasPlaying && !userStopped;
    wasPlaying = false; lastLiveAt = 0; advancedFor = ''; followKey = ''; lastElapsed = 0; pendingAdvance = false;
    window.castleDirect?.forget?.();
    toast(resumeBoot ? 'Castle restarted · the song will start again' : 'Castle restarted');
  }
  // Said once per failure, not once per poll: the streamer keeps reporting the
  // same reason until the next show starts.
  function watchShow() {
    const reason = lightShow?.error || '';
    if (reason === shownShowError) {return;}
    shownShowError = reason;
    if (reason) {toast(`Lights stopped: ${reason} · press Play to restart`);}
  }
  function liveStateText() {
    if (booting(state)) {return 'Castle is starting up · light shows are still loading';}
    if (state.show_on) {return `Installed playlist running on castle${showFault()}`;}
    if (state.track) {return `Castle audio: ${state.track}${lightNote()}${showFault()}`;}
    if (hasTrack(state)) {return `Castle scene: ${state.scene}${showFault()}`;}
    return `Castle idle${showFault()}`;
  }
  // A fault in this page's own bookkeeping or painting. It is named for what
  // it is and never claims the castle went away, because it did not.
  function fault(e) {
    pageFault = `page error: ${e?.message || e}`;
    $('live-state').textContent = pageFault;
  }
  function finish() {
    try { paint(); } catch (e) { fault(e); }
    publish();
  }
  async function poll() {
    const started = epoch, sent = performance.now();
    let answer;
    // Only the transport lives in this try. Everything after it is this
    // page's own work, and a TypeError there used to be counted as a missed
    // poll: the strike counter oscillated 0→1 forever and the page said the
    // castle was not answering while the state on screen was a second old.
    try { answer = await api('/radio/device'); }
    catch (e) {
      timed(performance.now() - sent, false);
      error = e.message;
      // Three strikes: a single dropped poll must not blank the transport or
      // zero the elapsed clock for a second.
      if (++failures >= 3) { state = null; lightShow = null; clock = null; }
      $('live-state').textContent = failures >= 3 ? e.message : `${e.message} · reconnecting…`;
      return finish();
    }
    timed(performance.now() - sent, true);
    // A poll that left before a command and lands after it describes the
    // castle the user just changed; dropping it is what keeps Stop stopped.
    if (started !== epoch) {return finish();}
    try {
      data = answer || {}; state = data.state || null; lightShow = data.light_show; clock = data.playback;
      caps = data.capabilities || {}; received = performance.now(); error = ''; failures = 0; pageFault = '';
      if (rebooted(state)) {reboot();}
      watchShow();
      if (onCastle()) {follow();}
      holdForQueue();
      $('live-state').textContent = liveStateText();
    } catch (e) { fault(e); }
    return finish();
  }
  async function command(body) {
    // Busy is not failed: the console used to paint red and say the command
    // had failed because a previous one was still in the air.
    if (busy) {return BUSY;}
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
  // The transport's own commands say the same thing the bench console does.
  async function send(body) {
    const result = await command(body);
    if (result === BUSY) {toast('Still sending the previous command · press Play again in a moment');}
    return result;
  }
  function schedule() {
    clearTimeout(timer);
    // The loop re-arms whatever happened: a page fault that escaped refresh()
    // used to leave the scheduler unarmed and the page frozen on stale state.
    timer = setTimeout(async () => {
      try { await refresh(); } catch (e) { fault(e); }
      finally { schedule(); }
    }, document.hidden ? 4000 : 1000);
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { refresh(); holdForQueue(); } schedule(); });
  const snapshot = () => ({connected: !!state, state, data, caps, lightShow, error, pageFault,
    health: health(), healthLine: healthLine(), framesText: framesText(lightShow)});
  // One panel that throws must not silence the others or stop the poll,
  // whether it is hearing the shared snapshot or its own first one.
  const deliver = (fn, s) => { try { fn(s); } catch (e) { fault(e); } };
  function publish() {
    const s = snapshot();
    for (const fn of listeners) {deliver(fn, s);}
  }
  window.castleLink = {
    BUSY,
    subscribe(fn) { listeners.add(fn); if (data || error) {deliver(fn, snapshot());} return () => listeners.delete(fn); },
    refresh, command, state: () => state, capabilities: () => caps, lastError: () => lastError,
    health, healthLine, framesText,
    // The firmware's own record of what it did; 404 on anything older.
    events: () => api('/radio/device/events'),
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
        // ensure() rejects when the listing request itself fails; unhandled,
        // that left Play doing nothing at all with nothing said about it.
        const inventory = await Promise.resolve(window.remoteLibrary?.ensure()).catch(() => null);
        const item = window.remoteLibrary?.item(t);
        if (item?.audio && item.filename) { await send({action: 'file', file: item.filename, key: t.key}); return; }
        // ensure() gives up after 6 s. A slow SD listing then looks exactly
        // like a missing file, and the sync dialog is the wrong answer.
        if (!inventory) { toast('Castle listing slow · press Play again'); window.remoteLibrary?.retry(); return; }
        window.remoteLibrary?.offer(t); return;
      }
      await send({action: 'scene', scene});
    },
    toggle() { if (isStarting(state)) {return false;} return isPlaying(state) && !busy ? this.stop() : this.play(); },
    // Previous on the castle cannot read audio.currentTime (nothing is loaded
    // here), so the castle's own clock decides restart-or-go-back.
    previous() { if (busy || !isPlaying(state) || remoteTime() <= 3) {return false;} this.play(); return true; },
    owns(t) { return !userStopped && isLive(state) && !!t && remoteTrack()?.id === t.id; },
    stop() { audio.pause(); userStopped = true; wasPlaying = false; stopped = true; return command({action: 'stop'}); }
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
    holdForQueue();
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
    command({action: 'pir', armed: $('motion').checked, cooldown: Number.parseInt($('cooldown').value, 10)})
      .then(ok => { if (ok === true) {toast(`Motion ${$('motion').checked ? 'armed' : 'off'} on the castle`);} });
  };
  $('motion').addEventListener('change', sendMotion);
  $('cooldown').addEventListener('change', sendMotion);
  // B53: playlist mode is not radio mode. Starting the installed show parks
  // the radio queue (userStopped) and clears the follow state, so the dark
  // gaps between playlist scenes are not read as "this song ended".
  $('live-show-start').onclick = () => { userStopped = true; wasPlaying = false; advancedFor = ''; command({action: 'show/start'}); };
  // /api/stop is scene_stop only; the generated playlist steps on after the
  // gap. Ending the evening is /api/show/stop.
  $('live-show-stop').onclick = () => { userStopped = true; wasPlaying = false; advancedFor = ''; command({action: 'show/stop'}); };
  refresh().catch(fault).then(schedule);
  setInterval(() => { if (onCastle() || state) {paint();} if (onCastle()) {drawPlayheads();} }, 100);
})();
