/* The lab's flags: tap 🚩 the moment something looks wrong (or right), tag
 * it, and it goes to the lab server's notes.jsonl with the song time, both
 * shows and the section — `show_lab.py --notes` reads them back for the next
 * pass. Opened from disk there is no server to take it, so the flag stays in
 * this browser instead, and says so. Flags are ticks on the timeline, the
 * phone's and the Mac's alike. Runs after the page script and shares its
 * globals. */
'use strict';
const LOCAL_NOTES='castle.lab.notes',GOOD=new Set(['love']);
let notes=[],flagAt=null,wasPlaying=false;
function localNotes(){try{return JSON.parse(localStorage.getItem(LOCAL_NOTES)||'[]');}catch{return [];}}
async function loadNotes(){
  let shared=[];
  try{const reply=await fetch('notes.jsonl',{cache:'no-store'});
    if(reply.ok){shared=(await reply.text()).split('\n').filter(Boolean).map(l=>JSON.parse(l));}}catch{/* opened from disk */}
  notes=[...shared,...localNotes()];if(!playing){draw();}
}
function songKey(){return SONGS[+$('song').value].baseline.id;}
function drawFlags(g,w,h){
  const key=songKey();
  for(const n of notes){
    if(n.song!==key){continue;}
    const x=n.t/duration*w;g.fillStyle=n.tags.some(t=>GOOD.has(t))?'#5ee08a':'#ff5a4a';
    g.beginPath();g.moveTo(x-5,0);g.lineTo(x+5,0);g.lineTo(x,8);g.fill();g.fillRect(x-.5,0,1,h);
  }
}
function pressed(box){return [...box.querySelectorAll('[aria-pressed="true"]')];}
function openFlag(){
  flagAt=clock;wasPlaying=playing;setPlaying(false);
  const view=$('sides').dataset.view,about=view==='0'?'current':view==='1'?'candidate':'both';
  $('flag-when').textContent='Flag '+fmt(flagAt);
  $('flag-about').querySelectorAll('button').forEach(b=>{
    if(b.dataset.about!=='both'){b.textContent=$(b.dataset.about==='current'?'title-0':'title-1').textContent;}
    b.setAttribute('aria-pressed',String(b.dataset.about===about));});
  $('flag-tags').querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed','false'));
  $('flag-text').value='';$('flag-status').textContent='';$('flag-sheet').hidden=false;sheet(false);
}
function closeFlag(){$('flag-sheet').hidden=true;flagAt=null;if(wasPlaying){setPlaying(true);}}
async function saveFlag(){
  const s=sectionAt(flagAt),song=SONGS[+$('song').value].baseline;
  const note={t:Math.round(flagAt),song:song.id,name:song.name,
    current:blind?blind.a:'current',candidate:blind?blind.b:$('candidate').value,
    about:pressed($('flag-about'))[0]?.dataset.about||'both',tags:pressed($('flag-tags')).map(b=>b.dataset.tag),
    text:$('flag-text').value.trim(),section:s?[s.label,s.look,s.pattern].filter(Boolean).join(' · '):'',
    firmware:$('firmware').value,soften:params.soft};
  if(!note.tags.length&&!note.text){$('flag-status').textContent='Pick a tag or type a few words.';return;}
  try{
    const reply=await fetch('notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(note)});
    if(!reply.ok){throw new Error('HTTP '+reply.status);}
    notes.push(await reply.json());closeFlag();
  }catch{
    localStorage.setItem(LOCAL_NOTES,JSON.stringify([...localNotes(),note]));notes.push(note);
    $('flag-status').textContent='The lab server did not take it, so it is kept in this browser only.';
    setTimeout(closeFlag,1800);
  }
  draw();
}
$('flag').onclick=()=>{if($('flag-sheet').hidden){openFlag();}else{closeFlag();}};
$('flag-cancel').onclick=closeFlag;$('flag-save').onclick=saveFlag;
$('flag-text').onkeydown=e=>{if(e.key==='Enter'){saveFlag();}};
$('flag-about').onclick=e=>{const b=e.target.closest('button');if(b){$('flag-about').querySelectorAll('button').forEach(o=>o.setAttribute('aria-pressed',String(o===b)));}};
$('flag-tags').onclick=e=>{const b=e.target.closest('button');if(b){b.setAttribute('aria-pressed',String(b.getAttribute('aria-pressed')!=='true'));}};
loadNotes();
