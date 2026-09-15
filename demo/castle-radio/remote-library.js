/* Inventory is read from the SD card; syncing audio is not firmware deployment. */
(() => {
  let inventory = null, polling = false, signature = '', selected = null, activeJob = null, inventoryError = '';
  let inflight = null;
  const panel = document.createElement('dialog');
  panel.className = 'sync-dialog';
  panel.innerHTML = '<h2 id="sync-title">Sync to castle</h2><p id="sync-explanation"></p><p id="sync-progress" role="status"></p><progress id="sync-busy" max="100" value="0" hidden></progress><small id="sync-measure"></small><div><button id="sync-start">Sync audio to castle</button><button id="sync-close">Close</button></div>';
  document.body.append(panel);
  panel.setAttribute('aria-labelledby','sync-title');
  const key = t => t.key || t.file;
  function label(t) {
    const item = inventory?.tracks[key(t)], job = inventory?.jobs[key(t)];
    if (job && !job.done) return 'Syncing to castle…';
    if (job?.error) return 'Sync failed · retry';
    if (!item) return inventoryError ? 'Castle check failed · retrying' : 'Checking castle…';
    return item.status === 'ready' ? 'On castle · audio + lights' : item.audio ? 'On castle · audio only' : 'Not synced to castle';
  }
  function paintDialog() {
    if (!selected) return;
    const item = inventory?.tracks[key(selected)], job = activeJob?.key === key(selected) ? activeJob : inventory?.jobs[key(selected)];
    $('sync-title').textContent = selected.title;
    $('sync-explanation').textContent = selected.key
      ? 'Sync copies the song audio to the castle’s SD card. Its generated light show still requires a firmware update; this does not make the full show ready to play.'
      : 'Sync restores this installed scene’s audio to the castle’s SD card. The original light show is already in the firmware.';
    $('sync-progress').textContent = job?.error || (job && !job.done ? job.phase : item?.audio ? 'Audio is on the castle. Select the castle output and press Play.' : label(selected));
    $('sync-busy').hidden = !job || job.done;
    $('sync-busy').value = job?.percent || 0;
    $('sync-measure').textContent = job && !job.done
      ? `${formatBytes(job.sent_bytes || 0)} of ${formatBytes(job.bytes || 0)} · ${Math.round(job.percent || 0)}%`
      : job?.done && !job.error ? 'Transfer complete · byte count and CRC verified' : '';
    $('sync-start').disabled = !item || !item.can_sync || (!!item.audio && !job?.error) || !!(job && !job.done);
    $('sync-start').textContent = job?.error ? 'Retry audio sync' : item?.audio ? 'Audio is on castle' : 'Sync audio to castle';
  }
  function offer(t) {selected=t;paintDialog();if(!panel.open)panel.showModal();refreshInventory();}
  window.remoteLibrary = {
    badge:t=>`<span class="remote-badge">${safe(label(t))}</span>`,
    button:t=>`<button data-sync-song="${t.id}" aria-label="Castle sync for ${safe(t.title)}">${inventory?.tracks[key(t)]?.status==='ready'?'✓ Castle':inventory?.tracks[key(t)]?.audio?'✓ Audio':'⇧ Sync'}</button>`,
    offer,
    syncing:()=>!!activeJob && !activeJob.done,
    item:t=>inventory?.tracks[key(t)]||null,
    trackByFilename:name=>tracks.find(t=>inventory?.tracks[key(t)]?.filename===name)||null,
    // Play must not offer a sync dialog just because the first inventory
    // sweep (three SD listings on the castle) has not answered yet.
    ensure:async()=>{if(inventory)return inventory;if(!inflight)refreshInventory(true);await Promise.race([inflight,new Promise(r=>setTimeout(r,6000))]);return inventory;}
  };
  $('sync-close').onclick = () => panel.close();
  $('sync-start').onclick = async () => {
    const song=selected;
    $('sync-start').disabled=true;
    try {
      activeJob = await request('/radio/device/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:key(song)})});
      paintDialog();
      await monitorSync(activeJob.key);
    } catch(error) {$('sync-progress').textContent=error.message;$('sync-start').disabled=false;}
  };
  const formatBytes = value => value >= 1024*1024 ? `${(value/1024/1024).toFixed(1)} MB` : `${Math.round(value/1024)} KB`;
  async function monitorSync(syncKey) {
    try {
      while (activeJob && !activeJob.done) {
        await new Promise(resolve=>setTimeout(resolve,400));
        activeJob = await request(`/radio/device/sync-status?key=${encodeURIComponent(syncKey)}`);
        paintDialog(); renderTracks(); renderImports();
      }
      if (!activeJob?.error) await refreshInventory();
    } catch(error) {
      activeJob = {...(activeJob||{}),done:true,error:error.message};
      paintDialog();
    }
  }
  document.addEventListener('click',e=>{const button=e.target.closest('[data-sync-song]');if(button)offer(tracks[Number(button.dataset.syncSong)]);});
  const remote = document.createElement('article');
  remote.className='remote-inventory';
  remote.innerHTML='<h2>Other audio on castle</h2><p class="subtle">Files already on the SD card, outside this demo’s synced library. Matching titles may be separate copies.</p><ul id="remote-audio-list"></ul>';
  $('device').append(remote);
  async function refreshInventory(force=false) {
    if(polling || window.remoteLibrary.syncing())return;
    if(document.hidden && !force && !panel.open)return;
    polling=true;
    try {
      inflight=request('/radio/device/library');inventory=await inflight;inventoryError='';
      const next=JSON.stringify(inventory);
      if(next!==signature){signature=next;renderTracks();renderImports();$('remote-audio-list').innerHTML=inventory.other_audio.map(file=>`<li><span><b>${safe(file.name)}</b><small>${formatBytes(file.bytes)}</small></span><button data-delete-remote="${safe(file.name)}" aria-label="Delete ${safe(file.name)} from castle">Delete</button></li>`).join('')||'<li>No additional audio files</li>';}
      paintDialog();
    } catch(error){inventoryError=error.message;if(!inventory)$('remote-audio-list').textContent='Castle inventory unavailable · retrying';if(panel.open)$('sync-progress').textContent=error.message;}
    finally{polling=false;inflight=null;}
  }
  $('remote-audio-list').onclick=async e=>{const button=e.target.closest('[data-delete-remote]');if(!button)return;const name=button.dataset.deleteRemote;if(!confirm(`Delete ${name} from the castle SD card?`))return;button.disabled=true;try{await request(`/radio/device/audio/${encodeURIComponent(name)}`,{method:'DELETE'});signature='';await refreshInventory();toast(`${name} deleted from castle`);}catch(error){toast(`Could not delete: ${error.message}`);button.disabled=false;}};
  refreshInventory(true);setInterval(()=>refreshInventory(),5000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshInventory(true);});
})();
