/* One audio element owns full-song and stem playback, seeking, and both visuals. */
/* global $, REQUEST_MS, audio, blacked, CastleVisuals, current, fmt, history, imported, lastLibrary, load, queue, refresh, renderImports, renderQueue, renderTracks, request, stopped, toast, toggle, tracks, updatePlayer */
const V = CastleVisuals;
const heroStage = new V.Stage($('hero-canvas'));
const detailStage = new V.Stage($('preview-canvas'));
let waveformData=null, waveformEpoch=0, switchEpoch=0, switching=false, pendingTime=0;
let sceneData=[], lightState=null, lightScene=null, lightKey='', lightTick=-16;
let lastRemoved=null, lastFrame=0;
const waveformCache=new Map();
const waveColor=layer=>getComputedStyle(document.documentElement).getPropertyValue(`--wave-${layer}`).trim()||'#c9a7ff';
const layerNames={combined:'Full song',vocals:'Voice',backing:'Background'};
const params=V.defaultParams();
fetch('/scenes.json',{signal:AbortSignal.timeout(REQUEST_MS.act)}).then(r=>r.ok?r.json():Promise.reject(Error(`scenes.json: ${r.status}`))).then(s=>{sceneData=s;lightKey='';}).catch(()=>toast('Built-in light scenes could not load. Reload to retry.'));

function mountPreview(name=location.hash.slice(1)||'play'){
  const host=name==='play'?$('listen-preview-host'):$('import-preview-host');
  if($('split-preview').parentElement!==host){host.append($('split-preview'));}
}
function openPreview(){mountPreview();$('split-preview').scrollIntoView({behavior:'smooth',block:'start'});}
window.addEventListener('radio-page',e=>{mountPreview(e.detail);drawWaveforms();});
window.addEventListener('radio-theme',()=>drawWaveforms());

function syncLayer(){
  const t=tracks[current], layer=window.radioLayer;
  $('split-title').textContent=t.title;
  $('layer-badge').textContent=layerNames[layer];
  document.querySelectorAll('[data-layer]').forEach(b=>{
    b.disabled=b.dataset.layer!=='combined'&&!t.split;
    b.setAttribute('aria-pressed',String(b.dataset.layer===layer));
  });
  $('preview-toggle').disabled=!!t.deleted;$('preview-toggle').textContent=audio.paused?'▶ Play':'Ⅱ Pause';
  const playState=audio.paused?'paused':'playing';
  $('split-state').textContent=switching?'Loading layer…':`${layerNames[layer]} · ${playState} on this computer`;
  updatePlayer();
}
function layerSource(t,layer){
  if(layer==='combined'){return t.url||`media/${t.file}`;}
  return `/radio/audio/stems/${t.key}/${layer}.mp3`;
}
// Resolves once the new layer's metadata is in; rejects on a media error or after 12 s.
function waitForLayer(){
  return new Promise((resolve,reject)=>{
    const finish=error=>{
      clearTimeout(timer);
      audio.removeEventListener('loadedmetadata',ready);
      audio.removeEventListener('error',failed);
      if(error){reject(error);}else{resolve();}
    };
    const ready=()=>finish();
    const failed=()=>finish(new Error('Layer audio unavailable'));
    const timer=setTimeout(()=>finish(new Error('Layer loading timed out')),12000);
    audio.addEventListener('loadedmetadata',ready);
    audio.addEventListener('error',failed);
    if(audio.readyState>=1){ready();}
  });
}
async function resumeLayer(token,position,shouldPlay){
  await waitForLayer();
  if(token!==switchEpoch){return;}
  audio.currentTime=Math.min(position,Number.isFinite(audio.duration)?audio.duration:position);
  switching=false;
  if(shouldPlay&&!window.castlePlayer?.active()){blacked=false;stopped=false;await audio.play();}
  if(token===switchEpoch){syncLayer();}
}
async function changeLayer(layer){
  if(window.castlePlayer?.active()){toast("Choose This computer to audition separated audio.");return;}
  const t=tracks[current];
  if(layer!=='combined'&&!t.split){return;}
  if(layer===window.radioLayer&&!switching){return;}
  const token=++switchEpoch;
  const position=switching?pendingTime:audio.currentTime;
  const shouldPlay=!audio.paused||(switching&&window.layerWasPlaying);
  window.layerWasPlaying=shouldPlay;pendingTime=position;switching=true;
  audio.pause();window.radioLayer=layer;
  syncLayer();drawWaveforms();
  try{
    await setAudioSource(layerSource(t,layer));
    if(token!==switchEpoch){return;}
    await resumeLayer(token,position,shouldPlay);
  }catch(e){
    if(token!==switchEpoch){return;}
    switching=false;
    if(e.name==='AbortError'){syncLayer();return;}
    $('split-state').textContent=`${e.message}. Press Play to retry.`;
  }
}
document.querySelectorAll('[data-layer]').forEach(b=>b.onclick=()=>changeLayer(b.dataset.layer));
$('preview-toggle').onclick=toggle;$('split-stop').onclick=()=>{switchEpoch++;switching=false;pendingTime=0;stop();};
function seekTo(seconds){if(window.castlePlayer?.active()){return;}if(!Number.isFinite(audio.duration)){return;}audio.currentTime=Math.max(0,Math.min(seconds,audio.duration));pendingTime=audio.currentTime;drawPlayheads();}
$('preview-seek').oninput=()=>seekTo(Number($('preview-seek').value)/1000*(audio.duration||0));
$('seek').addEventListener('input',()=>{pendingTime=audio.currentTime;drawPlayheads();});

async function loadWaveforms(){
  const token=++waveformEpoch,t=tracks[current],key=t.key||t.file;
  waveformData=null;$('wave-status').textContent='Loading analyzed waveform…';$('waveforms').innerHTML='';
  try{
    if(!waveformCache.has(key)){waveformCache.set(key,request(`/radio/waveform/${encodeURIComponent(key)}`,undefined,REQUEST_MS.analysis));}
    const data=await waveformCache.get(key);if(token!==waveformEpoch){return;}
    waveformData=data;drawWaveforms();
    $('wave-status').textContent=t.split?'Voice, background, and full-song waveforms · click or drag to seek':'Full-song waveform · click or drag to seek';
  }catch{ // the status line is the report; the cache entry is dropped so a retry refetches
    waveformCache.delete(key);if(token===waveformEpoch){$('wave-status').textContent='Waveform unavailable. Reopen this song to retry.';}}
}
function drawWaveforms(){
  if(!waveformData){return;}
  const layers=waveformData.layers;
  $('waveforms').innerHTML='';
  for(const layer of ['combined','vocals','backing']){
    if(!layers[layer]){continue;}
    const row=document.createElement('div');row.className='wave-row';row.dataset.waveLayer=layer;
    row.innerHTML=`<div class="wave-label"><b>${layerNames[layer]}</b><span>${layer===window.radioLayer?'LISTENING':''}</span></div><div class="wave-surface"><canvas aria-label="${layerNames[layer]} waveform"></canvas><div class="playhead"></div></div>`;
    $('waveforms').append(row);
    const canvas=row.querySelector('canvas'),box=canvas.getBoundingClientRect();
    canvas.width=Math.max(300,Math.round(box.width*devicePixelRatio));canvas.height=90*devicePixelRatio;
    const g=canvas.getContext('2d');
    V.drawSingle(g,layers[layer].both,undefined,waveformData.duration,'both',canvas.width,canvas.height,waveColor(layer));
    const surface=row.querySelector('.wave-surface');
    const scrub=e=>{const rect=surface.getBoundingClientRect();seekTo((e.clientX-rect.left)/rect.width*(audio.duration||waveformData.duration));};
    surface.onpointerdown=e=>{surface.setPointerCapture(e.pointerId);scrub(e);};
    surface.onpointermove=e=>{if(surface.hasPointerCapture(e.pointerId)){scrub(e);}};
    surface.onpointerup=e=>{scrub(e);surface.releasePointerCapture(e.pointerId);};
  }
  drawPlayheads();
}
// While a layer switch is in flight the audio element's clock is meaningless; the saved position stands in.
function localTime(){return switching?pendingTime:audio.currentTime;}
function drawPlayheads(){
  const duration=audio.duration||tracks[current].duration||0;
  const time=window.castlePlayer?.active()?window.castlePlayer.time():localTime();
  $('preview-time').textContent=`${fmt(time)} / ${fmt(duration)}`;
  $('preview-seek').value=duration?time/duration*1000:0;
  document.querySelectorAll('.playhead').forEach(p=>p.style.left=`${duration?Math.min(100,time/duration*100):0}%`);
}
new ResizeObserver(()=>drawWaveforms()).observe($('preview-canvas'));
window.addEventListener('radio-track',()=>{
  switchEpoch++;switching=false;pendingTime=0;lightKey='';
  // load() sets the new audio URL after this event; refresh on the next microtask.
  queueMicrotask(()=>{syncLayer();loadWaveforms();});
});
for(const event of ['play','pause','ended','loadedmetadata']){audio.addEventListener(event,()=>{syncLayer();drawPlayheads();});}
audio.addEventListener('timeupdate',drawPlayheads);
$('stop').addEventListener('click',()=>{switchEpoch++;switching=false;pendingTime=0;});
$('blackout').addEventListener('click',()=>{switchEpoch++;switching=false;pendingTime=0;});

const paletteColors={violet:[.66,.15,1,.05],green:[.25,1,.5,.05]};
const styleBases={'Haunted ballroom':'seance','Electric storm':'chill'};
function makeScene(t){
  if(!t.cues){const original=sceneData.find(s=>s.file===t.file);if(original){return original;}}
  const baseColor=paletteColors[$('palette').value]||[1,.5,.08,.03];
  const base=styleBases[$('style').value]||'candle';
  const intensity=Number($('intensity').value)/100;
  return {id:t.key||t.file,name:t.title,dur:t.duration*1000,loop:false,volume:1,blurb:'',file:t.file,bytes:0,yaml:'',
    base:{towerL:base,towerR:base,door:'ember'},levels:{towerL:.14,towerR:.14,door:.12},
    cues:(t.cues||[]).map(c=>({t:c[0]*1000,bus:'LED',op:'strike',zone:({left:'towerL',right:'towerR',door:'door'})[c[1]],intensity:c[2]*intensity,color:baseColor,decay:c[3],ms:300}))};
}
function lightKeyFor(t,soft){
  return [t.id,t.cues?.length,sceneData.length,$('style').value,$('palette').value,$('intensity').value,soft,window.radioRig?.revision||0].join('|');
}
// Rebuild the cue state when the song or preview settings change (or the clock
// jumps backwards), then step the 16 ms cue timeline up to the current clock.
function rebuildLightState(t,key,soft,clock){
  if(key!==lightKey||clock<lightTick-16){
    lightKey=key;lightScene=makeScene(t);lightState=V.createState(lightScene,0);
    V.rebuildLightsAt(lightState,lightScene,0);lightState.soft=soft;lightTick=-16;
  }
  while(lightTick+16<=clock){lightTick+=16;V.fireCues(lightState,lightTick,()=>{});V.decayFlashes(lightState);}
}
function applyRigLayouts(){
  if(!window.radioRig){return;}
  const layouts=window.radioRig.layouts();
  lightState.layout=layouts;
  for(const z of V.ZONE_ORDER){lightState.rgbw[z]=V.zoneRgbw(window.radioRig.rig,z);}
  heroStage.setLayouts(layouts);detailStage.setLayouts(layouts);
}
function zoneScale(isPaused){
  if(blacked||Number($('brightness').value)===0||(stopped&&$('ambient').value==='dark')){return 0;}
  if(stopped){return .3;}
  if(isPaused){return $('paused').value==='dim'?.18:.4;}
  return 1;
}
function scaleZones(out,isPaused,reduced){
  const scale=zoneScale(isPaused);
  for(const zone of ['towerL','door','towerR']){
    out[zone].pix=out[zone].pix.map(c=>c.map(v=>v*scale));out[zone].avg=out[zone].avg.map(v=>v*scale);
    if(reduced&&!stopped&&!blacked){out[zone].avg=[.12,.1,.05].map(v=>v*scale*params.bright);out[zone].pix=out[zone].pix.map(()=>out[zone].avg);}
  }
}
function drawStages(out,seconds,flash){
  const wash=flash.flash*params.bright;
  if(!$('play').hidden){heroStage.draw(out,seconds,wash,flash.color);}
  if(!$('play').hidden||!$('import').hidden){detailStage.draw(out,seconds,wash,flash.color);}
}
function previewClockLabel(remote){
  if(!remote){return layerNames[window.radioLayer];}
  return window.castleLink?.capabilities().position?'Castle clock':'Castle · estimated';
}
function lightStateText(remote,seconds){
  if(blacked){return 'Blackout';}
  return `${previewClockLabel(remote)} · ${fmt(seconds)}`;
}
function drawCastle(now){
  requestAnimationFrame(drawCastle);if(now-lastFrame<32){return;}lastFrame=now;
  const t=tracks[current],soft=$('soften').checked;
  const remote=window.castlePlayer?.active();
  const isPaused=remote?!window.castlePlayer.playing():audio.paused;
  const clock=(remote?window.castlePlayer.time():localTime())*1000;
  rebuildLightState(t,lightKeyFor(t,soft),soft,clock);
  Object.assign(params,{bright:Number($('brightness').value)/100,soft});
  applyRigLayouts();
  const out=V.renderZones(lightState,clock/1000,params);
  if(window.radioRig&&t.key){window.radioRig.apply(out,clock/1000);}
  else if($('route-preview-status')){$('route-preview-status').textContent='Built-in song: original authored light cues. Audio assignments apply to imported songs.';}
  let flash=V.dominantFlash(lightState);
  const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
  scaleZones(out,isPaused,reduced);
  if(window.radioRig){window.radioRig.drawPixels(out);}
  if(isPaused||blacked||reduced||t.key){flash={flash:0,color:[1,1,1,0]};}
  drawStages(out,clock/1000,flash);
  $('light-state').textContent=lightStateText(remote,clock/1000);
}

function rememberHidden(){try{localStorage.setItem('castle-radio-hidden',JSON.stringify(tracks.filter(t=>!t.key&&t.deleted).map(t=>t.file)));}catch{}}
async function deleteSong(id){
  const t=tracks[id];if(!t||t.deleted){return;}
  try{
    if(t.key){await request(`/radio/library/${t.key}`,{method:'DELETE'});}
    t.deleted=true;imported.delete(t.key);queue=queue.filter(i=>i!==id);history=history.filter(i=>i!==id);
    if(current===id){stop();const replacement=tracks.find(t=>!t.deleted);if(replacement){load(replacement.id);}}
    rememberHidden();lastRemoved=t;$('undo-bar').hidden=false;
    $('undo-bar').querySelector('span').textContent=`Removed “${t.title}” from this demo.`;
    lastLibrary='';await refresh();renderTracks();renderQueue();renderImports();
  }catch(e){toast(`Could not remove song: ${e.message}`);}
}
$('tracks').addEventListener('click',e=>{const b=e.target.closest('[data-delete-song]');if(b){deleteSong(Number(b.dataset.deleteSong));}});
$('undo-delete').onclick=async()=>{if(!lastRemoved){return;}const t=lastRemoved;try{if(t.key){await request(`/radio/restore/${t.key}`,{method:'POST',headers:{'X-Castle':'1'}});}t.deleted=false;if(!queue.includes(t.id)){queue.push(t.id);}rememberHidden();lastLibrary='';await refresh();renderTracks();renderQueue();$('undo-bar').hidden=true;lastRemoved=null;}catch(e){toast(`Could not restore song: ${e.message}`);}};
$('dismiss-undo').onclick=()=>{$('undo-bar').hidden=true;};
try{const hidden=JSON.parse(localStorage.getItem('castle-radio-hidden')||'[]');for(const t of tracks){if(!t.key&&hidden.includes(t.file)){t.deleted=true;}}}catch{}
queue=queue.filter(i=>!tracks[i].deleted);history=history.filter(i=>!tracks[i].deleted);
mountPreview();syncLayer();loadWaveforms();requestAnimationFrame(drawCastle);
