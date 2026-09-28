/* The lab's blind test: two shows of one song, names hidden and sides
 * shuffled, looping a short clip until you pick. Your picks stay in this
 * browser (localStorage) until you download them; show_lab.py --verdicts
 * reads that file. Runs after the page script and shares its globals. */
'use strict';
const VERDICTS='castle.lab.verdicts',CLIP_MS=15000;
function picks(){try{return JSON.parse(localStorage.getItem(VERDICTS)||'[]');}catch{return [];}}
function showOf(song,id){return id==='current'?song.baseline:song.candidates.find(c=>c.id===id).show;}
function nameOf(id){return id==='current'?'the current prepared show':(LABELS[id]||id);}
function pickTwo(ids){
  const a=ids[Math.floor(Math.random()*ids.length)];let b=a;
  while(b===a){b=ids[Math.floor(Math.random()*ids.length)];}
  return [a,b];
}
function blindRound(){
  const index=Math.floor(Math.random()*SONGS.length),song=SONGS[index];
  const [a,b]=pickTwo(['current',...song.candidates.map(c=>c.id)]);
  // Clips start where a passage starts, so a transition is usually inside.
  const lab=song.candidates.map(c=>c.show.lab).find(Boolean);
  const starts=lab?lab.sections.map(s=>s.t).filter(t=>t+CLIP_MS<=song.baseline.dur):[0];
  const start=starts[Math.floor(Math.random()*starts.length)]??0;
  blind={song:song.baseline.id,index,a,b,start,end:Math.min(song.baseline.dur,start+CLIP_MS)};
  if($('song').value!==String(index)){$('song').value=String(index);candidatesFor();loadSound();}
  sides=[makeSide(showOf(song,a)),makeSide(showOf(song,b))];duration=song.baseline.dur;
  $('title-0').textContent='A';$('title-1').textContent='B';$('info-0').textContent='';$('info-1').textContent='';
  $('note').textContent='';$('blind-result').textContent='';
  $('blind-what').textContent=`${song.baseline.name}, ${fmt(start)}–${fmt(blind.end)}, looping. Which side looks better with the music?`;
  seek(start);setPlaying(true);
}
function vote(verdict){
  if(!blind){return;}
  const all=picks();all.push({song:blind.song,a:blind.a,b:blind.b,start:blind.start,verdict,soft:params.soft,at:new Date().toISOString()});
  localStorage.setItem(VERDICTS,JSON.stringify(all));
  const said={a:'A',b:'B',same:'neither'}[verdict];
  $('blind-result').textContent=`You picked ${said}. A was ${nameOf(blind.a)}; B was ${nameOf(blind.b)}. ${all.length} picks saved in this browser.`;
  setTimeout(blindRound,2500);
}
function leaveBlind(){
  blind=null;$('blind').hidden=true;$('title-0').textContent='Current prepared show';setPlaying(false);load();
}
function savePicks(){
  const link=document.createElement('a');
  link.href=URL.createObjectURL(new Blob([JSON.stringify(picks(),null,1)],{type:'application/json'}));
  link.download='castle-lab-verdicts.json';link.click();URL.revokeObjectURL(link.href);
}
$('blind-start').onclick=()=>{$('blind').hidden=false;blindRound();};
$('blind-next').onclick=blindRound;$('blind-stop').onclick=leaveBlind;$('blind-save').onclick=savePicks;
document.querySelectorAll('[data-vote]').forEach(button=>{button.onclick=()=>vote(button.dataset.vote);});
