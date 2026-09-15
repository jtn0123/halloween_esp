/* The physical player uses installed firmware scenes and its own audio clock. */
(() => {
  const target = $('output-target');
  let state = null, lightShow = null, busy = false, polling = false, clock = null, received = 0;
  const onCastle = () => target.value === 'castle';
  function remoteTime() {
    if (!state || !clock || (state.scene === 'stop' && !state.track)) return 0;
    const elapsed = clock.position_s + (performance.now()-received)/1000;
    const scene = state.track ? null : sceneData.find(s=>s.id===state.scene);
    const duration = (scene?.dur || tracks[current].duration*1000)/1000;
    return duration ? (scene?.loop ? elapsed % duration : Math.min(elapsed,duration)) : elapsed;
  }
  const sceneId = t => t.key ? null : t.file.replace(/^\d+_/, '').replace(/\.mp3$/, '');
  async function api(path, body) {
    const response = await fetch(path, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {});
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || 'Castle connection failed');
    return data;
  }
  function paint() {
    $('live-connection').textContent = state ? 'Connected' : 'Unavailable';
    $('live-health').textContent = state ? `Firmware ${state.version} · ${state.sd_mounted ? 'SD ready' : 'SD unavailable'}` : 'Cannot reach castle';
    $('live-destination').textContent = onCastle() ? 'Porch castle' : 'This computer';
    $('live-ready').textContent = state ? `${state.scenes.split(',').filter(s=>s!=='stop').length} installed shows` : 'Connection needed';
    if (!onCastle()) return;
    $('preview-seek').disabled = true;
    $('split-state').textContent = clock?.origin === 'observed' ? 'Following castle · timing starts when detected, actual position unknown' : 'Following castle · estimated timing · seeking unavailable';
    $('preview-toggle').textContent = '▶ Play on castle';
    $('queue-description').textContent = 'Manual skip on castle · automatic queue is not deployed';
    const playing = state && (state.scene !== 'stop' || state.track);
    $('current-detail').textContent = busy ? 'Sending command to castle…' : !state ? 'Castle unavailable · retry Play' : playing ? `Playing on castle · ${state.track||state.scene}${lightShow?.active?' · generated lights live':''}` : 'Castle stopped · ready to play';
    $('toggle').textContent = '▶';
    $('toggle').setAttribute('aria-label','Play on castle');
    $('hero-play').textContent = '▶ Play on castle';
    for (const id of ['seek','shuffle','repeat','previous']) {
      $(id).disabled = true;
      $(id).title = 'Not supported by the installed castle firmware';
    }
    $('toggle').disabled = busy;
    $('hero-play').disabled = busy;
    $('elapsed').textContent = fmt(remoteTime());
    $('duration').textContent = fmt(tracks[current].duration);
    $('seek').value = tracks[current].duration ? remoteTime()/tracks[current].duration*1000 : 0;
  }
  async function refresh() {
    if (polling || busy || window.remoteLibrary?.syncing()) return;
    polling = true;
    try {
      const data = await api('/radio/device');
      state = data.state; lightShow = data.light_show; clock = data.playback; received = performance.now();
      if (onCastle()) {
        const remote = state.track ? window.remoteLibrary?.trackByFilename(state.track) : tracks.find(t=>sceneId(t)===state.scene);
        if (remote && remote.id !== current) load(remote.id);
        stopped = state.scene === 'stop' && !state.track; blacked = false;
      }
      $('live-state').textContent = state.show_on ? 'Installed playlist running on castle' : state.track ? `Castle audio: ${state.track}${lightShow?.active?' · generated lights live':''}` : `Castle scene: ${state.scene}`;
    } catch (error) {state = null; lightShow = null; $('live-state').textContent = error.message;}
    finally {polling = false; paint();}
  }
  async function command(body) {
    if (busy) return;
    busy = true; paint();
    try { await api('/radio/device/command',body); }
    catch(error) { $('live-state').textContent = error.message; toast(error.message); }
    finally {busy = false; await refresh();}
  }
  window.castlePlayer = {
    active:onCastle,
    time:remoteTime,
    playing:()=>!!state && (state.scene !== "stop" || !!state.track),
    async play() {
      audio.pause();
      const scene = sceneId(tracks[current]);
      if (!scene) {
        const item=window.remoteLibrary?.item(tracks[current]);
        if(item?.audio&&item.filename){await command({action:'file',file:item.filename,key:tracks[current].key});return;}
        window.remoteLibrary?.offer(tracks[current]);return;
      }
      await command({action:'scene',scene});
    },
    stop() {audio.pause(); return command({action:'stop'});},
    paint
  };
  target.onchange = () => {
    audio.pause(); stopped = true;
    for (const id of ['seek','preview-seek','shuffle','repeat','previous']) $(id).disabled = false;
    switchEpoch++; switching = false;
    syncLayer(); renderQueue();
    updatePlayer();
    if (onCastle() && state) {$('volume').value=state.volume;syncVolume();}
    if (onCastle()) toast('Castle selected. Press Play; synced imports play audio and installed scenes add physical lights.');
    else toast('Computer preview selected. Use Stop castle to stop any running device show.');
    refresh();
  };
  const localBlackout = $('blackout').onclick;
  $('blackout').onclick = () => onCastle() ? command({action:'blackout'}) : localBlackout();
  const localVolume = $('volume').oninput;
  const localSettingsVolume = $('settings-volume').oninput;
  $('volume').oninput = () => {if (!onCastle()) localVolume();};
  $('volume').onchange = () => {if (onCastle()) command({action:'volume',volume:Number($('volume').value)});};
  $('settings-volume').oninput = () => {if (!onCastle()) localSettingsVolume(); else {$('volume').value=$('settings-volume').value;syncVolume();}};
  $('settings-volume').onchange = () => {if (onCastle()) command({action:'volume',volume:Number($('settings-volume').value)});};
  $('live-show-start').onclick = () => command({action:'show/start'});
  $('live-show-stop').onclick = () => command({action:'stop'});
  refresh(); setInterval(refresh,1000);
  setInterval(()=>{if(onCastle()){paint();drawPlayheads();}},100);
})();
