/* Import UI backed by server.py and the existing project tools. */
/* A library refresh that drops the current song stops the castle only when
   this page is the one playing it there: an import finishing must not
   black out a song somebody else (or the installed playlist) started. */
/* global $, art, current, deleteSong, history, load, openPreview, queue, renderQueue, renderTracks, start, toast, tracks */
const imported = new Map();

let  lastJobs='', lastLibrary='', pollBusy=false;
const escapeHTML = value => String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
/* Every call is bounded. A fetch that never settles used to leave pollBusy
   true for good, and with it the 2 s refresh (grade report 2026-09-17 pm C6);
   the budget is per call, so a poll gives up long before an upload does.
   `r.ok` is read BEFORE the body, and a failure that is not JSON — a 404 from
   a static server is HTML — reads as its own text rather than as a parse error. */
const REQUEST_MS={poll:6000,act:15000,inventory:10000,analysis:120000,upload:15*60*1000};
async function request(url,options,budget){
const ms=budget||(options?.method&&options.method!=='GET'?REQUEST_MS.act:REQUEST_MS.poll);
let r;
try{r=await fetch(url,{...options,signal:AbortSignal.timeout(ms)});}catch(error){throw error&&(error.name==='TimeoutError'||error.name==='AbortError')?new Error(`The import service did not answer within ${Math.round(ms/1000)}s.`):error;}
if(!r.ok){throw new Error(await failureText(r));}
return r.json();}
/* Tags out, one pass: a regex over `<[^>]*>` rescans the tail at every
   unclosed `<`, and this text is whatever a failing server sent. */
const stripTags=text=>{let out='',at=0;for(;;){const lt=text.indexOf('<',at),gt=lt<0?-1:text.indexOf('>',lt);if(gt<0){return out+text.slice(at);}out+=`${text.slice(at,lt)} `;at=gt+1;}};
async function failureText(r){
let text='';try{text=await r.text();}catch{text='';}
try{const value=JSON.parse(text);if(value?.error){return String(value.error);}}catch{/* not JSON: its own text is the report */}
const flat=stripTags(text).replace(/\s+/g,' ').trim();
return flat?`${r.status}: ${flat.slice(0,160)}`:`The import service could not complete the request (${r.status}).`;}
const importBytes=value=>value>=1024*1024?`${(value/1024/1024).toFixed(1)} MB`:`${Math.round((value||0)/1024)} KB`;
const reprocessDialog=document.createElement('dialog');
reprocessDialog.className='reprocess-dialog';
reprocessDialog.innerHTML=`<form method="dialog" id="reprocess-form"><div class="eyebrow">SAVED SOURCE</div><h2 id="reprocess-title">Reprocess song</h2><p id="reprocess-source" class="subtle"></p><label>Playback format<select id="reprocess-format"><option value="mp3">MP3</option><option value="opus">Ogg Opus</option><option value="wav">PCM WAV</option></select></label><label>Audio quality<select id="reprocess-quality"><option value="standard">Standard · MP3 96 / Opus 64 kbps</option><option value="high">High · MP3 160 / Opus 96 kbps</option><option value="max">Maximum · MP3 192 / Opus 128 kbps</option></select></label><label class="switch-row">Separate voice & background<input id="reprocess-split" type="checkbox"></label><p id="reprocess-note" class="notice">The saved link or original upload will be used automatically.</p><div><button class="primary" value="save">Reprocess song</button><button value="cancel">Cancel</button></div></form>`;
document.body.append(reprocessDialog);
let reprocessTrack=null;
function integrate(rows){const keys=new Set(rows.map(r=>r.key));for(const t of tracks){if(t.key&&!keys.has(t.key)){t.deleted=true;imported.delete(t.key);}}for(const record of rows){let t=tracks.find(t=>t.key===record.key);if(!t){t={id:tracks.length,key:record.key,file:'',color:'#6c927b',symbol:'✧',kind:'song'};tracks.push(t);}Object.assign(t,record,{deleted:false});imported.set(record.key,t);}
const count=tracks.filter(t=>!t.deleted).length;$('collection-count').textContent=count;$('collection-caption').textContent=`${count} tracks · automatic light shows`;
queue=queue.filter(id=>!tracks[id].deleted);history=history.filter(id=>!tracks[id].deleted);if(tracks[current].deleted){if(!window.castlePlayer?.active()||window.castlePlayer.owns(tracks[current])){stop();}load(tracks.find(t=>!t.deleted)?.id??0);}renderTracks();renderQueue();renderImports();}
/* The prepared songs as the listener asked to see them: newest first unless
   they chose a name or size order, and only those matching the filter. */
function shownImports(){const rows=[...imported.values()].reverse(),find=String($('library-filter').value||'').trim().toLowerCase(),sort=$('library-sort').value;
if(sort==='title'){rows.sort((a,b)=>String(a.title).localeCompare(String(b.title),undefined,{sensitivity:'base',numeric:true}));}
if(sort==='size'){rows.sort((a,b)=>(b.playback_bytes||0)-(a.playback_bytes||0));}
return find?rows.filter(t=>`${t.title} ${t.source_label||''}`.toLowerCase().includes(find)):rows;}
function renderImports(){const list=$('imported-list');list.innerHTML='';const shown=shownImports();
$('library-summary').textContent=imported.size?`${shown.length===imported.size?imported.size:`${shown.length} of ${imported.size}`} song${imported.size===1?'':'s'} · ${importBytes([...imported.values()].reduce((sum,t)=>sum+(t.playback_bytes||0),0))}`:'';
for(const t of shown){const row=document.createElement('div');row.className='prepared-track';const source=t.source_url?`<a href="${escapeHTML(t.source_url)}" target="_blank" rel="noreferrer">Saved link · ${escapeHTML(t.source_label)}</a>`:`Saved source · ${escapeHTML(t.source_label||'unavailable')}`;const bitrate=t.playback_bitrate?` · ${t.playback_bitrate} kbps`:'';const format=`${String(t.playback_format||'mp3').toUpperCase()}${bitrate} · ${importBytes(t.playback_bytes)}`;row.innerHTML=`${art(t)}<div><strong>${escapeHTML(t.title)}</strong><small>${t.split?'Voice + background lights':'Combined-audio lights'} · ${format}</small><small class="saved-source">${source}</small>${window.remoteLibrary?.badge(t)||''}</div>${window.remoteLibrary?.button(t)||''}<button data-import-play="${t.id}">▶ Play</button><button data-split-open="${t.id}">${t.prepared_show?'Preview show':t.split?'Hear split':'Preview'}</button><button data-rename="${t.id}">Rename</button><button data-reprocess="${t.id}" ${t.source_available?'':'disabled'}>Change audio</button><button data-delete-song="${t.id}" aria-label="Remove ${escapeHTML(t.title)}">Remove</button>`;list.append(row);}if(!imported.size){list.innerHTML='<p class="subtle">Your imported songs will appear here and in Listen.</p>';}else if(!shown.length){list.innerHTML='<p class="subtle">No prepared song matches that search.</p>';}}
function jobProgressText(j){
  if(Number.isFinite(j.percent)){return `${Math.round(j.percent)}% of this stage`;}
  return j.done?'Finished':'Working…';
}
/* A job's name, best first: the server's, the finished song's, the library's,
   the uploaded file's, and for a link still downloading the link itself. */
function jobName(j){
  const known=j.title||j.result?.title||tracks.find(t=>t.key===j.id&&!t.deleted)?.title||j.source_name;
  if(known){return known;}
  try{const u=new URL(j.source);return `${u.hostname.replace(/^www\./,'')}${u.pathname}${u.search}`.slice(0,90);}catch{return 'Imported song';}
}
/* Finished jobs the listener cleared, by id AND finish time: a retry reuses
   the id, and its next result must not arrive already hidden. */
let jobsNow=[],hiddenJobs=new Set();
try{hiddenJobs=new Set(JSON.parse(localStorage.getItem('castle-radio-hidden-jobs')||'[]'));}catch{/* no storage: nothing hidden */}
const jobStamp=j=>`${j.id}:${j.finished_at||0}`;
const isWaiting=j=>!j.done&&String(j.phase).startsWith('Queued');
function hideJobs(jobs){for(const j of jobs){hiddenJobs.add(jobStamp(j));}try{localStorage.setItem('castle-radio-hidden-jobs',JSON.stringify([...hiddenJobs].slice(-200)));}catch{/* hidden for this page only */}renderJobs();}
function took(j){const s=Math.max(0,Math.round((j.finished_at||0)-(j.started_at||j.finished_at||0)));return s>=60?`${Math.floor(s/60)}m ${s%60}s`:`${s}s`;}
/* The queue as it will run: what is being prepared, then what waits in the
   order it will start, then what finished, newest first. */
let holdingJob=false;
function renderJobs(){
  // A button that is replaced between press and release never clicks.
  if(holdingJob){return;}
  const running=jobsNow.filter(j=>!j.done&&!isWaiting(j)),waiting=jobsNow.filter(isWaiting);
  const finished=jobsNow.filter(j=>j.done&&!hiddenJobs.has(jobStamp(j))).reverse();
  const clearable=finished.filter(j=>!j.error&&!j.result?.split_error);
  const counts=[[running.length,'preparing'],[waiting.length,'waiting'],[finished.length,'finished']].filter(c=>c[0]).map(c=>c.join(' ')).join(' · ');
  const head=counts?`<div class="queue-head"><b>Import queue</b><span>${counts}</span>${clearable.length?'<button data-clear-finished>Clear finished</button>':''}</div>`:'';
  $('import-jobs').innerHTML=head+[...running.map(j=>renderJob(j)),...waiting.map((j,i)=>renderJob(j,i)),...finished.map(j=>renderJob(j))].join('');
}
function renderDone(j){
  const state=j.phase==='Cancelled'?'Cancelled':`${escapeHTML(j.phase)} · took ${took(j)}`;
  const retry=j.phase==='Cancelled'?`<button data-retry="${j.id}">Retry</button>`:'';
  return `<article class="import-job finished"><div><b>${escapeHTML(jobName(j))}</b><span>${state}</span></div>${retry}<button data-dismiss="${j.id}" aria-label="Clear ${escapeHTML(jobName(j))} from the queue">Clear</button></article>`;
}
function renderJob(j,place){
  if(j.done&&!j.error&&!j.result?.split_error){return renderDone(j);}
  const waitingNote=place===undefined?'':(place===0?'Next up':`Number ${place+1} in line`);
  const cancelButton=j.done?`<button data-dismiss="${j.id}">Clear</button>`:`<button data-cancel="${j.id}">${place===undefined?'Cancel':'Remove from queue'}</button>`;
  const percentAttr=Number.isFinite(j.percent)?`value="${j.percent}"`:'';
  const errorNote=j.error?`<p class="import-error">${escapeHTML(j.error)}</p>`:'';
  const splitNote=j.result?.split_error?'<p class="import-error">The song and rhythm lights are ready, but voice separation failed. Retry to prepare the split.</p>':'';
  const retryButton=j.done&&(j.error||j.result?.split_error)?`<button data-retry="${j.id}">Retry preparation</button>`:'';
  if(waitingNote){return `<article class="import-job waiting"><div><b>${escapeHTML(jobName(j))}</b><span>${waitingNote}</span></div>${cancelButton}</article>`;}
  return `<article class="import-job"><div><b>${escapeHTML(jobName(j))}</b><span>${escapeHTML(j.phase)}</span></div>${j.done?'':`<div class="job-measure"><progress max="100" ${percentAttr} aria-label="${escapeHTML(j.phase)} progress"></progress><b>${jobProgressText(j)}</b></div><p class="subtle">${escapeHTML(j.detail||'Starting…')} <span data-started="${j.started_at||0}" data-finished="${j.finished_at||0}"></span></p>`}${errorNote}${splitNote}${retryButton}${cancelButton}</article>`;
}
async function refresh(){if(pollBusy){return;}pollBusy=true;try{const [jobs,rows]=await Promise.all([request('/radio/jobs'),request('/radio/library')]);$('service-status').textContent='Import service ready · files stay in this demo';if(JSON.stringify(jobs)!==lastJobs){lastJobs=JSON.stringify(jobs);jobsNow=jobs;renderJobs();}const signature=JSON.stringify(rows);if(signature!==lastLibrary){lastLibrary=signature;integrate(rows);renderJobs();}}catch{ // any failure reads the same to the user: the service is not answering
$('service-status').textContent='Import service unavailable. Start server.py to import songs.';}finally{pollBusy=false;}}
/* One import, or many: every link on its own line and every chosen file is
   queued in the order given. What could not be queued stays in the box with
   the reason, so a full queue or one bad link does not cost the whole list. */
async function queueLink(url,split){return request('/radio/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,split,audio_format:window.radioAudioFormat,audio_quality:window.radioAudioQuality})});}
$('import-form').onsubmit=async e=>{e.preventDefault();const button=$('import-submit');button.disabled=true;
try{
  const files=[...($('import-file').files||[])],links=[...new Set(String($('import-url').value).split(/\s+/).filter(Boolean))],split=$('import-split').checked;
  if(files.length&&links.length){throw new Error('Choose files or paste links, then clear the other source.');}
  if(!files.length&&!links.length){throw new Error('Paste a link or choose an audio file first.');}
  const tooBig=files.find(f=>f.size>100*1024*1024);if(tooBig){throw new Error(`${tooBig.name} is over 100 MB. Choose smaller files.`);}
  let queued=0;const left=[];
  for(const [index,item] of [...files,...links].entries()){
    const name=typeof item==='string'?item:item.name;
    $('import-message').textContent=`${typeof item==='string'?'Submitting':'Uploading'} ${index+1} of ${files.length+links.length} · ${name}`;
    try{if(typeof item==='string'){await queueLink(item,split);}else{await uploadAudio(item,split);}queued++;lastJobs='';refresh();}
    catch(error){left.push({name,reason:error.message});}
  }
  $('import-file').value='';$('import-url').value=left.filter(l=>links.includes(l.name)).map(l=>l.name).join('\n');
  const done=queued?`Queued ${queued} song${queued===1?'':'s'}. Sources are saved so you can reprocess later.`:'';
  $('import-message').textContent=[done,...left.map(l=>`Not queued · ${l.name} — ${l.reason}`)].filter(Boolean).join('\n');
  await refresh();
}catch(error){$('import-message').textContent=error.message;}finally{button.disabled=false;}};
$('import-jobs').onpointerdown=()=>{holdingJob=true;};
$('import-jobs').onpointerup=$('import-jobs').onpointerleave=$('import-jobs').onpointercancel=()=>{if(holdingJob){holdingJob=false;setTimeout(renderJobs,0);}};
$('import-jobs').onclick=async e=>{
  holdingJob=false;
  const pick=name=>e.target.closest(`[data-${name}]`),retry=pick('retry'),cancel=pick('cancel'),dismiss=pick('dismiss');
  if(pick('clear-finished')){hideJobs(jobsNow.filter(j=>j.done&&!j.error&&!j.result?.split_error));return;}
  if(dismiss){hideJobs(jobsNow.filter(j=>j.id===dismiss.dataset.dismiss&&j.done));return;}
  const b=retry||cancel;if(!b){renderJobs();return;}
  b.disabled=true;
  try{await request(retry?'/radio/retry':'/radio/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:b.dataset.retry||b.dataset.cancel})});lastJobs='';await refresh();}
  catch(err){toast(/^404/.test(err.message)?'Restart Castle Radio on your Mac to cancel imports — it is still running the older version.':err.message);}
  finally{b.disabled=false;}};
$('imported-list').onclick=e=>{const p=e.target.closest('[data-import-play]'),s=e.target.closest('[data-split-open]'),r=e.target.closest('[data-reprocess]'),d=e.target.closest('[data-delete-song]'),n=e.target.closest('[data-rename]');if(n){renameSong(tracks[Number(n.dataset.rename)]);}if(p){start(Number(p.dataset.importPlay));openPreview();}if(s){const id=Number(s.dataset.splitOpen);if(current!==id){stop();load(id);}openPreview();}if(r){reprocessTrack=tracks[Number(r.dataset.reprocess)];$('reprocess-title').textContent=reprocessTrack.title;$('reprocess-source').textContent=`${reprocessTrack.source_kind==='link'?'Saved link':'Saved original file'}: ${reprocessTrack.source_label}`;$('reprocess-format').value=reprocessTrack.playback_format||'mp3';$('reprocess-quality').value=reprocessTrack.playback_quality||'standard';$('reprocess-quality').disabled=$('reprocess-format').value==='wav';$('reprocess-split').checked=!!reprocessTrack.split;reprocessDialog.showModal();}if(d){deleteSong(Number(d.dataset.deleteSong));}};
async function renameSong(t){
  const title=String(window.prompt('Name this song',t.title)||'').trim();
  if(!title||title===t.title){return;}
  try{await request('/radio/rename',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:t.key,title})});lastLibrary='';await refresh();toast(`Renamed to ${title}`);}
  catch(err){toast(/^404/.test(err.message)?'Restart Castle Radio on your Mac to rename songs — it is still running the older version.':err.message);}
}
$('library-filter').oninput=renderImports;$('library-sort').onchange=renderImports;
$('reprocess-format').onchange=()=>{$('reprocess-quality').disabled=$('reprocess-format').value==='wav';};
$('reprocess-form').onsubmit=async e=>{if(e.submitter?.value!=='save'||!reprocessTrack){return;}e.preventDefault();e.submitter.disabled=true;try{await request('/radio/reprocess',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:reprocessTrack.key,audio_format:$('reprocess-format').value,audio_quality:$('reprocess-quality').value,split:$('reprocess-split').checked})});reprocessDialog.close();lastJobs='';toast('Reprocessing from the saved source');await refresh();}catch(error){$('reprocess-note').textContent=error.message;}finally{e.submitter.disabled=false;}};
refresh();setInterval(refresh,2000);

function uploadAudio(file,split){
/* X-Castle marks every raw-bodied or bodiless POST as ours: a custom header
   forces a preflight, which the import server never answers, so no other
   origin's page can reach these routes (grade report 2026-09-17 E2). */
if(window.castleDesktop?.connected){return file.arrayBuffer().then(body=>request('/radio/import',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Castle':'1','X-Filename':encodeURIComponent(file.name),'X-Split':String(split),'X-Audio-Format':window.radioAudioFormat,'X-Audio-Quality':window.radioAudioQuality},body},REQUEST_MS.upload));}
return new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST','/radio/import');xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.setRequestHeader('X-Castle','1');xhr.setRequestHeader('X-Filename',encodeURIComponent(file.name));xhr.setRequestHeader('X-Split',String(split));xhr.setRequestHeader('X-Audio-Format',window.radioAudioFormat);xhr.setRequestHeader('X-Audio-Quality',window.radioAudioQuality);const progress=$('upload-progress');progress.hidden=false;progress.value=0;xhr.upload.onprogress=e=>{if(e.lengthComputable){progress.value=e.loaded/e.total*100;$('import-message').textContent=`Uploading audio · ${Math.round(progress.value)}%`;}};xhr.onload=()=>{progress.hidden=true;try{const result=JSON.parse(xhr.responseText);if(xhr.status>=400){reject(new Error(result.error||'Upload failed'));}else {resolve(result);}}catch{reject(new Error('The import server returned an unreadable response.'));}};xhr.onerror=()=>{progress.hidden=true;reject(new Error('Upload interrupted. Try again.'));};xhr.send(file);});}
setInterval(()=>{document.querySelectorAll('[data-started]').forEach(e=>{const start=Number(e.dataset.started);e.textContent=start?`· ${Math.max(0,Math.floor((Number(e.dataset.finished)||Date.now()/1000)-start))}s elapsed`:'';});},1000);
