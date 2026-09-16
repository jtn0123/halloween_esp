/* Standalone concept: all playback and preferences stay in this browser. */
const tracks = [
  ['This Is Halloween', 'The Citizens of Halloween', 'Haunted carnival', '09_the_citizens_of_halloween___this.mp3', '#a27143', '☾', 'song'],
  ["The Ballad of the Witches’ Road", 'The castle collection', 'Witchlight', '10_the_ballad_of_the_witches__road_.mp3', '#676a9b', '✧', 'song'],
  ['Vigil', 'Castle original · atmosphere', 'Candlelight', '01_vigil.mp3', '#8d713e', '♜', 'scene'],
  ['Séance', 'Castle original · atmosphere', 'Spirit glow', '03_seance.mp3', '#5d7d6c', '◈', 'scene'],
  ['Ballroom', 'Castle original · atmosphere', 'Haunted waltz', '04_ballroom.mp3', '#98696d', '❖', 'scene'],
  ['Storm', 'Castle original · atmosphere', 'Thunder & gold', '02_storm.mp3', '#627684', 'ϟ', 'scene'],
  ['Descent', 'Castle original · showpiece', 'Deep ember', '05_descent.mp3', '#926349', '⌄', 'scene'],
  ['Visitation', 'Castle original · atmosphere', 'Ghostly green', '06_visitation.mp3', '#607648', '◇', 'scene'],
  ['Approach', 'Castle original · trigger', 'Doorway glow', '07_approach.mp3', '#a2874e', '♙', 'scene'],
  ['Crypt', 'Castle original · atmosphere', 'Heartbeat', '08_crypt.mp3', '#70677a', '◉', 'scene'],
].map(([title,artist,style,file,color,symbol,kind],id)=>({id,title,artist,style,file,color,symbol,kind,duration:0}));
const $ = id => document.getElementById(id);
const audio = $('audio');
window.radioTheme=localStorage.getItem('castle-radio-theme')||'oled';
document.documentElement.dataset.theme=window.radioTheme;
const lightSoundCard=$('settings').querySelector('.settings-grid article');
lightSoundCard.querySelector('h2').insertAdjacentHTML('afterend',`<label>Theme<select id="theme"><option value="oled">OLED night · black & dark purple</option><option value="castle">Castle green</option></select></label>`);
$('theme').value=window.radioTheme;
$('theme').onchange=()=>{
  window.radioTheme=$('theme').value;
  document.documentElement.dataset.theme=window.radioTheme;
  localStorage.setItem('castle-radio-theme',window.radioTheme);
  window.dispatchEvent(new CustomEvent('radio-theme'));
  toast(window.radioTheme==='oled'?'OLED night theme on':'Castle green theme on');
};
window.radioLayer='combined';
window.radioAudioFormat=localStorage.getItem('castle-radio-audio-format')||'mp3';
window.radioAudioQuality=localStorage.getItem('castle-radio-audio-quality')||'standard';
document.cookie=`castle_audio_format=${window.radioAudioFormat}; SameSite=Strict; Path=/`;
document.cookie=`castle_audio_quality=${window.radioAudioQuality}; SameSite=Strict; Path=/`;
const codecSettings=document.createElement('div');
codecSettings.className='codec-settings';
codecSettings.innerHTML=`
  <div><div class="eyebrow">S3 PLAYBACK</div><h2>Audio format for new imports</h2>
  <p class="subtle">Choose how prepared songs are stored and streamed from the castle. Existing songs keep their current format until you reprocess them.</p></div>
  <div class="codec-options" role="radiogroup" aria-label="Audio playback format">
    <label class="codec-card recommended"><input aria-label="MP3" type="radio" name="audio-format" value="mp3"><span class="codec-name">MP3 <b>Recommended</b></span><strong>Balanced</strong><small>96–192 kbps · 44.1 kHz</small><p>Broad compatibility and good sound. Higher quality helps detailed music, but makes larger files.</p></label>
    <label class="codec-card"><input aria-label="Ogg Opus" type="radio" name="audio-format" value="opus"><span class="codec-name">Ogg Opus</span><strong>Smallest</strong><small>64–128 kbps · 48 kHz</small><p>Better quality at small sizes, with more S3 decoder work.</p></label>
    <label class="codec-card"><input aria-label="PCM WAV" type="radio" name="audio-format" value="wav"><span class="codec-name">PCM WAV</span><strong>Lightest decode</strong><small>16-bit stereo · 44.1 kHz</small><p>Uncompressed and about 10.1 MB per minute. Quality presets do not change WAV.</p></label>
  </div>
  <label class="codec-quality">Default quality for new imports
    <select id="audio-quality">
      <option value="standard">Standard · MP3 96 / Opus 64 kbps</option>
      <option value="high">High · MP3 160 / Opus 96 kbps</option>
      <option value="max">Maximum · MP3 192 / Opus 128 kbps</option>
    </select>
  </label>
  <p id="codec-status" class="notice"></p>`;
$('settings').insertBefore(codecSettings,$('settings').querySelector('.settings-grid'));
const codecNames={mp3:'MP3',opus:'Ogg Opus',wav:'PCM WAV'};
function showCodecStatus(){
  const label=codecNames[window.radioAudioFormat];
  const quality=$('audio-quality');
  quality.disabled=window.radioAudioFormat==='wav';
  $('codec-status').textContent=window.radioAudioFormat==='wav'
    ? 'PCM WAV is always uncompressed 16-bit stereo; the quality preset does not apply.'
    : `${label} with ${window.radioAudioQuality} quality is selected for new imports. Each prepared song can be reprocessed later from its saved source.`;
}
document.querySelectorAll('[name="audio-format"]').forEach(input=>{
  input.checked=input.value===window.radioAudioFormat;
  input.onchange=()=>{
    if(!input.checked){return;}
    window.radioAudioFormat=input.value;
    localStorage.setItem('castle-radio-audio-format',input.value);
    document.cookie=`castle_audio_format=${input.value}; SameSite=Strict; Path=/`;
    showCodecStatus();
    toast(`${codecNames[input.value]} selected for new imports`);
  };
});
$('audio-quality').value=window.radioAudioQuality;
$('audio-quality').onchange=()=>{
  window.radioAudioQuality=$('audio-quality').value;
  localStorage.setItem('castle-radio-audio-quality',window.radioAudioQuality);
  document.cookie=`castle_audio_quality=${window.radioAudioQuality}; SameSite=Strict; Path=/`;
  showCodecStatus();
  toast(`${$('audio-quality').selectedOptions[0].textContent.split(' · ')[0]} quality selected`);
};
showCodecStatus();
let current=0, queue=tracks.slice(1).map(t=>t.id), history=[], shuffle=false, repeat=false, filter='all', blacked=false, stopped=true, toastTimer, unshuffled=null;
const fmt = s => `${Math.floor((s||0)/60)}:${String(Math.floor((s||0)%60)).padStart(2,'0')}`;
const safe = value => String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const art = t => `<div class="cover" style="--c:${t.color}">${t.symbol}</div>`;
function toast(message){$('toast').textContent=message;$('toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.remove('visible'),3200);}
function mix(ids){const out=[...ids];const dice=crypto.getRandomValues(new Uint32Array(Math.max(1,out.length)));for(let i=out.length-1;i>0;i--){const j=dice[i]%(i+1);[out[i],out[j]]=[out[j],out[i]];}return out;}
function renderTracks(){const q=$('search').value.toLocaleLowerCase(); const list=tracks.filter(t=>!t.deleted&&(filter==='all'||t.kind===filter)&&`${t.title} ${t.artist}`.toLocaleLowerCase().includes(q));$('tracks').innerHTML=list.map(t=>`<div class="track ${t.id===current?'active':''}"><span>${t.id===current&&!audio.paused?'♫':String(t.id+1).padStart(2,'0')}</span><button class="song-pick" data-song="${t.id}" aria-label="Play ${safe(t.title)}">${art(t)}<div><strong>${safe(t.title)}</strong><small>${safe(t.artist)}</small>${window.remoteLibrary?.badge(t)||''}</div></button><span class="style-tag">✧ ${safe(t.style)}</span><span>${t.duration?fmt(t.duration):'—'}</span><div class="row-actions">${window.remoteLibrary?.button(t)||''}<button class="add" data-add="${t.id}" aria-label="Add ${safe(t.title)} to queue">+</button><button class="remove-song" data-delete-song="${t.id}" aria-label="Remove ${safe(t.title)}">×</button></div></div>`).join('')||'<p class="subtle">No matching tracks. Try another title.</p>';}
function renderQueue(){$('queue-count').textContent=`${queue.length} tracks`;$('queue-description').textContent=shuffle?'Shuffle is on · a different kind of night':'Your collection, in order';$('queue').innerHTML=queue.map((id,i)=>{const t=tracks[id];return `<div class="queue-item">${art(t)}<div class="queue-name"><strong>${safe(t.title)}</strong><small>${safe(t.style)}</small></div><button data-up="${i}" aria-label="Move ${safe(t.title)} up next" ${i===0?'disabled':''}>↑</button><button data-remove="${i}" aria-label="Remove ${safe(t.title)} from queue">×</button></div>`;}).join('')||'<p>End of the queue. Add another track with +.</p>';}
function playerDetail(){
  if(blacked){return 'Blackout · lights and audio off';}
  if(stopped){return 'Ready to play · auto lights';}
  if(audio.paused){return 'Paused · position saved';}
  const layer=window.radioLayer==='combined'?'full song':window.radioLayer;
  return `Playing ${layer} · this computer`;
}
function updatePlayer(){const t=tracks[current];$('toggle').disabled=!!t.deleted;$('hero-play').disabled=!!t.deleted;$('current-title').textContent=t.title;$('current-art').style.setProperty('--c',t.color);$('current-art').textContent=t.symbol;$('current-detail').textContent=playerDetail();$('toggle').textContent=audio.paused?'▶':'Ⅱ';$('toggle').setAttribute('aria-label',audio.paused?'Play':'Pause');$('hero-play').textContent=audio.paused?'▶ Play the night':'Ⅱ Pause the night';$('shuffle').setAttribute('aria-pressed',String(shuffle));$('repeat').setAttribute('aria-pressed',String(repeat));$('next').disabled=!queue.length&&!repeat;$('previous').disabled=!history.length&&audio.currentTime<3;renderTracks();window.castlePlayer?.paint();}
function load(id){current=id;window.radioLayer='combined';window.dispatchEvent(new CustomEvent('radio-track')); audio.src=tracks[id].url||`media/${tracks[id].file}`;audio.load();$('seek').value=0;$('elapsed').textContent='0:00';$('duration').textContent=fmt(tracks[id].duration);updatePlayer();}
async function play(){if(window.castlePlayer?.active()){return window.castlePlayer.play();}blacked=false;stopped=false;const source=audio.src,id=current;try{await audio.play();}catch(e){if(audio.src!==source||current!==id||e.name==='AbortError'){return;}stopped=true;toast('Audio could not start. Press Play to retry.');}if(audio.src===source&&current===id){updatePlayer();}}
function toggle(){if(window.castlePlayer?.active()){return window.castlePlayer.toggle();}if(audio.paused){play();}else {audio.pause();}}
function start(id){history.push(current);queue=tracks.slice(id+1).filter(t=>!t.deleted).map(t=>t.id);if(shuffle){queue=mix(tracks.filter(t=>!t.deleted&&t.id!==id).map(t=>t.id));}load(id);renderQueue();play();}
function next(){if(!queue.length){if(repeat){queue=shuffle?mix(tracks.filter(t=>!t.deleted).map(t=>t.id)):tracks.filter(t=>!t.deleted).map(t=>t.id);}else{stop();return;}}if(!queue.length){stop();return;}history.push(current);load(queue.shift());renderQueue();play();}
function stop(){if(window.castlePlayer?.active()){return window.castlePlayer.stop();}audio.pause();audio.currentTime=0;stopped=true;updatePlayer();}
$('toggle').onclick=toggle;$('hero-play').onclick=toggle;$('next').onclick=next;$('stop').onclick=stop;
$('previous').onclick=()=>{if(audio.currentTime>3){audio.currentTime=0;return;}if(history.length){queue.unshift(current);load(history.pop());renderQueue();play();}};
function applyShuffle(){
  shuffle=!shuffle;
  if(shuffle){
    unshuffled=queue.slice();
    queue=mix(queue);
  }else if(unshuffled){
    const live=new Set(queue);
    const kept=unshuffled.filter(id=>live.has(id));
    queue=kept.concat(queue.filter(id=>!unshuffled.includes(id)));
    unshuffled=null;
  }
  renderQueue();
  updatePlayer();
  toast(shuffle?'Shuffle on — up next is mixed':'Shuffle off — collection order restored');
}
$('shuffle').onclick=applyShuffle;
$('repeat').onclick=()=>{repeat=!repeat;updatePlayer();toast(repeat?'The collection will repeat':'Repeat off');};
$('blackout').onclick=()=>{stop();blacked=true;updatePlayer();toast('Preview blacked out. Press Play to start again.');};
$('volume').oninput=()=>{audio.volume=Number($('volume').value)/100;savePreferences();};
$('seek').oninput=()=>{if(Number.isFinite(audio.duration)){audio.currentTime=Number($('seek').value)/1000*audio.duration;}};
audio.addEventListener('timeupdate',()=>{$('elapsed').textContent=fmt(audio.currentTime);$('seek').value=audio.duration?audio.currentTime/audio.duration*1000:0;$('previous').disabled=!history.length&&audio.currentTime<3;});
audio.addEventListener('loadedmetadata',()=>{if(!Number.isFinite(audio.duration)){return;}tracks[current].duration=audio.duration;$('duration').textContent=fmt(audio.duration);renderTracks();});
audio.addEventListener('ended',()=>{if(!window.castlePlayer?.active()){next();}});audio.addEventListener('play',updatePlayer);audio.addEventListener('pause',updatePlayer);audio.addEventListener('error',()=>toast('This audio file is unavailable. Try another song.'));
$('tracks').onclick=e=>{const song=e.target.closest('[data-song]'),add=e.target.closest('[data-add]');if(song){start(Number(song.dataset.song));}if(add){queue.push(Number(add.dataset.add));renderQueue();updatePlayer();toast('Added to the end of your queue');}};
$('queue').onclick=e=>{const up=e.target.closest('[data-up]'),remove=e.target.closest('[data-remove]');if(up){const [id]=queue.splice(Number(up.dataset.up),1);queue.unshift(id);}if(remove){queue.splice(Number(remove.dataset.remove),1);}renderQueue();updatePlayer();};
$('search').oninput=renderTracks;
document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('selected',x===b));renderTracks();});
function page(name){if(!['play','import','create','settings','device'].includes(name)){name='play';}document.querySelectorAll('.page').forEach(p=>p.hidden=p.id!==name);document.querySelectorAll('[data-page]').forEach(b=>b.classList.toggle('selected',b.dataset.page===name));$('breadcrumb').textContent=({play:'LISTEN',import:'IMPORT MUSIC',create:'LIGHT STUDIO',settings:'RUN SETTINGS',device:'YOUR CASTLE'})[name];window.dispatchEvent(new CustomEvent('radio-page',{detail:name}));}
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>{location.hash=b.dataset.page;page(b.dataset.page);window.scrollTo(0,0);});
window.addEventListener('hashchange',()=>page(location.hash.slice(1)));
const preferenceIds=['style','intensity','palette','brightness','soften','ambient','paused','motion','interrupt','cooldown','volume'];
function savePreferences(){const values={};for(const id of preferenceIds){values[id]=$(id).type==='checkbox'?$(id).checked:$(id).value;}try{localStorage.setItem('castle-radio-demo-preferences',JSON.stringify(values));}catch{}}
try{const values=JSON.parse(localStorage.getItem('castle-radio-demo-preferences')||'{}');for(const id of preferenceIds){if(values[id]!==undefined){if($(id).type==='checkbox'){$(id).checked=values[id];}else {$(id).value=values[id];}}}}catch{}
for(const id of preferenceIds){$(id).addEventListener('input',()=>{savePreferences();$('intensity-value').textContent=$('intensity').value+'%';$('brightness-value').textContent=$('brightness').value+'%';});}
$('save-style').onclick=()=>{savePreferences();toast('Preview style saved on this computer');};
audio.volume=Number($('volume').value)/100;
$('intensity-value').textContent=$('intensity').value+'%';$('brightness-value').textContent=$('brightness').value+'%';
load(0);renderQueue();page(location.hash.slice(1));
// Only ten small metadata reads, all from the copied local demo media.
tracks.forEach(t=>{const probe=new Audio();probe.preload='metadata';probe.src=`media/${t.file}`;probe.onloadedmetadata=()=>{if(!Number.isFinite(probe.duration)){return;}t.duration=probe.duration;renderTracks();};});

$('queue-toggle').onclick=()=>{const panel=document.querySelector('.queue-panel');const open=panel.classList.toggle('mobile-open');$('queue-toggle').setAttribute('aria-expanded',String(open));$('queue-toggle').textContent=open?'☷ Hide up next':'☷ View up next';if(open){panel.scrollIntoView({behavior:'smooth',block:'start'});}};
function syncVolume(){ $('settings-volume').value=$('volume').value;$('settings-volume-value').textContent=$('volume').value+'%'; }
$('settings-volume').oninput=()=>{$('volume').value=$('settings-volume').value;audio.volume=Number($('volume').value)/100;savePreferences();syncVolume();};
$('volume').addEventListener('input',syncVolume);syncVolume();
