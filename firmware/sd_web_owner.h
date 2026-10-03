#pragma once
// The owner's page (v5.75, docs/PRODUCTION-TODO.md §1.3): everything a
// castle's owner can do without the studio, Castle Radio or a computer —
// run the show, pick a scene, set the level, black it out, read what is
// wrong, and change every setting the castle has.
//
// IN FLASH, NOT ON THE CARD, for the reason the phone remote is
// (sd_web_remote.h): it is the page you need when the card is the problem.
// Served at /owner always, and at / when the card has no site of its own —
// which is every castle with no card, a blank card, or one whose page has
// not been published. It replaced the v5.74 fallback page, which had a stop
// button, a file list and the two v5.74 settings, and nothing else.
//
// WHAT IT SHOWS, in plain words because the reader is not the person who
// built it: no card or a card with no show on it; the last restart — and a
// warning when that was a crash, a watchdog or a brownout, the three that
// mean "something is wrong" rather than "someone switched it on"; the
// castle's own clock in the owner's zone; quiet hours when they hold the
// speaker; and no motion control on a castle with no sensor (g_pir_fitted).
//
// "REPORT A PROBLEM" is page script and no firmware route: it fetches
// status, health, events and the boot log, adds the time, version and
// board, and hands the browser one text file. Nothing new on the castle to
// get wrong, and it works on any castle whose page loads.
//
// Every control is one request to an API the desk and Castle Radio already
// use; the castle key, when one is set, rides along as X-Castle-Key from
// this browser's localStorage, as it did on the v5.74 page.

#include <esp_http_server.h>

namespace castle_web {

inline const char kOwnerPage[] = R"HTML(<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Castle</title>
<style>body{font:16px system-ui;background:#14101c;color:#e8e0f0;margin:1.5rem auto;max-width:42rem;padding:0 1rem}
h1{font-size:1.3rem}h2{font-size:1.05rem;margin:1.5rem 0 .4rem;color:#cbbfe0}
button,select,input{font:inherit;color:inherit;background:#221d33;border:1px solid #4a3f75;border-radius:8px;padding:.4rem .7rem;margin:.15rem}
button{background:#3a2a55;border:0;cursor:pointer}button:hover{background:#503a75}
.warn,.note{padding:.55rem .8rem;border-radius:6px;margin:.4rem 0}.warn{background:#4a1d1d;border-left:4px solid #e05050}
.note{background:#22203a;border-left:4px solid #7a6aa5}small,#facts{color:#9a8fb0}ul{padding:0}li{margin:.3rem 0;list-style:none}
label{display:block;margin:.5rem 0}a{color:#b9a6e8}</style>
<h1>🏰 Castle <small id=v></small></h1>
<div id=alerts></div><p id=facts></p>
<h2>Show</h2><div id=scenes></div>
<button onclick="api('/api/show/start')">▶ Start the evening show</button>
<button onclick="api('/api/show/stop')">■ Stop the show</button>
<button onclick="api('/api/stop')">Stop this scene</button>
<button onclick="api('/api/blackout')">Blackout</button>
<label>Volume <input id=vol type=range min=0 max=100 onchange="api('/api/volume?v='+vol.value)"> <span id=volv></span></label>
<h2>Songs on the card</h2><ul id=files></ul>
<h2>Settings</h2>
<label><input type=checkbox id=bp onchange="set('boot_play='+(bp.checked?1:0))"> Start the show when the castle is switched on</label>
<label>Loudest the castle may ever be: <input id=vm type=number min=1 max=100 style="width:4.5em">% <button onclick="set('vol_max='+vm.value)">Save</button></label>
<label><input type=checkbox id=qon> Quiet hours — no sound from <input id=qf type=time value=22:00> to <input id=qt type=time value=07:00>
<button onclick="set('quiet='+(qon.checked?qf.value+'-'+qt.value:'off'))">Save</button><br><small>The lights keep running; only the speaker goes quiet.</small></label>
<label>Time zone <select id=tzs onchange="if(tzs.value)tzc.value=tzs.value"></select>
<input id=tzc size=22 placeholder="or a POSIX TZ string"> <button onclick="set('tz='+encodeURIComponent(tzc.value))">Save</button></label>
<div id=pir></div>
<p>Castle key <input id=k type=password size=14>
<button onclick="localStorage.castleKey=k.value;say('This browser will send that key.')">Use</button>
<button onclick="api('/api/key?new='+encodeURIComponent(k.value)).then(r=>{if(r&&r.ok){localStorage.castleKey=k.value;say('Key set.')}})">Set</button>
<button onclick="api('/api/key?clear=1').then(r=>{if(r&&r.ok){localStorage.removeItem('castleKey');say('Key cleared.')}})">Clear</button></p>
<button onclick="confirm('Erase Wi-Fi, the key and every setting, and restart?')&&api('/api/factory-reset?confirm=yes')">Factory reset</button>
<p id=msg role=status></p>
<h2>Something wrong?</h2>
<button id=rep onclick="report()">Report a problem</button>
<small>Saves one text file with everything the castle knows about itself. Send it to whoever looks after your castle.</small>
<p><a href=/remote>Phone remote</a> · <a href=/>Castle page</a></p>
<script>
const Z=[['UTC','UTC0'],['US Pacific','PST8PDT,M3.2.0,M11.1.0'],['US Mountain','MST7MDT,M3.2.0,M11.1.0'],
['Arizona','MST7'],['US Central','CST6CDT,M3.2.0,M11.1.0'],['US Eastern','EST5EDT,M3.2.0,M11.1.0'],
['Alaska','AKST9AKDT,M3.2.0,M11.1.0'],['Hawaii','HST10'],['UK','GMT0BST,M3.5.0/1,M10.5.0'],
['Central Europe','CET-1CEST,M3.5.0,M10.5.0/3'],['Eastern Europe','EET-2EEST,M3.5.0/3,M10.5.0/4'],
['India','IST-5:30'],['Japan','JST-9'],['Australia East','AEST-10AEDT,M10.1.0,M4.1.0/3'],
['New Zealand','NZST-12NZDT,M9.5.0,M4.1.0/3']];
const W={'power-on':'it was switched on','software':'it restarted itself (an update or a setting)',
'external':'its reset button was pressed','usb':'it was restarted over USB','deep-sleep':'it woke from sleep',
'PANIC':'it CRASHED and restarted','cpu-lockup':'it locked up and restarted',
'int-watchdog':'it FROZE and the watchdog restarted it','task-watchdog':'it FROZE and the watchdog restarted it',
'watchdog':'it FROZE and the watchdog restarted it',
'BROWNOUT':'the power dipped too low (a brownout) — check the power supply and its cable',
'power-glitch':'the power glitched — check the power supply and its cable',
'sdio':'its host restarted it','jtag':'a debugger restarted it','efuse':'it restarted after a chip fault',
'unknown':'it restarted, and the chip did not say why'};
let S={},scn='';
tzs.innerHTML='<option value="">Other…</option>'+Z.map(z=>`<option value="${z[1]}">${z[0]}</option>`).join('');
const esc=t=>String(t).replace(/[&<>"']/g,c=>'&#'+c.charCodeAt(0)+';');
const say=t=>msg.textContent=t;
const api=(u,m)=>fetch(u,{method:m||'POST',headers:localStorage.castleKey?{'X-Castle-Key':localStorage.castleKey}:{}})
 .then(async r=>{say(r.ok?'':r.status==401?'This castle has a key — enter it in Settings':'The castle said: '+await r.text());setTimeout(sync,400);return r})
 .catch(()=>say('The castle is not answering.'));
const set=q=>api('/api/settings?'+q).then(r=>{if(r&&r.ok)say('Saved.')});
const box=(c,t)=>`<div class=${c}>${t}</div>`;
function sync(){Promise.all([fetch('/api/status').then(r=>r.json()),fetch('/api/health').then(r=>r.json())]).then(([s,h])=>{
 S=s;v.textContent='v'+s.version+' · '+s.fw_variant+(s.locked?' · 🔒':'');
 let a='';
 if(!s.sd_mounted)a+=box('warn','<b>No SD card</b> — the show is on the card. Switch the castle off, push the card in until it clicks, and switch it back on. If the card is in, the castle cannot read it.');
 else if(s.missing.split(',').includes('show.man'))a+=box('warn','<b>The SD card has no show on it.</b> Every scene will glow its built-in look, with no sound.');
 else if(s.missing)a+=box('note','Missing from the card: '+esc(s.missing));
 if(h.was_crash)a+=box('warn','<b>Last restart: '+esc(W[h.last_reset]||h.last_reset)+'.</b> If this keeps happening, use “Report a problem” below.');
 if(s.quiet_now)a+=box('note','🌙 Quiet hours ('+esc(s.quiet)+'): the speaker is off. The lights carry on.');
 alerts.innerHTML=a;
 facts.textContent='Last restart: '+(W[h.last_reset]||h.last_reset)+' · switched on '+h.boots+' times, '+h.crashes+' crashes · castle clock: '
  +(s.local?s.local+(s.tz?'':' UTC'):'not set yet (it needs the internet to know the time)')+' · Wi-Fi '+s.rssi+' dBm';
 const ids=s.scenes.split(',').filter(x=>x&&x!='stop');
 if(ids.join()!=scn){scn=ids.join();scenes.innerHTML=ids.map(i=>`<button onclick="api('/api/scene?s=${encodeURIComponent(i)}')">${esc(i)}</button>`).join('')}
 vol.max=s.vol_max;if(document.activeElement!=vol)vol.value=s.volume;volv.textContent=s.volume+'%'+(s.vol_max<100?' (limit '+s.vol_max+'%)':'');
 bp.checked=s.boot_play;if(document.activeElement!=vm)vm.value=s.vol_max;
 if(document.activeElement!=qf&&document.activeElement!=qt){qon.checked=!!s.quiet;if(s.quiet){qf.value=s.quiet.slice(0,5);qt.value=s.quiet.slice(6)}}
 if(document.activeElement!=tzc&&document.activeElement!=tzs){tzc.value=s.tz;tzs.value=Z.some(z=>z[1]==s.tz)?s.tz:''}
 pir.innerHTML=s.pir.fitted?`<label><input type=checkbox ${s.pir.armed?'checked':''} onchange="api('/api/pir?armed='+(this.checked?1:0))"> Start a scene when someone walks up (motion sensor)</label>`
  :'<p><small>Motion sensor: not fitted on this castle.</small></p>';
}).catch(()=>say('The castle is not answering.'))}
function songs(){fetch('/api/files').then(r=>r.ok?r.json():Promise.reject()).then(fs=>{
 const m=fs.filter(f=>f.name&&!f.dir&&/\.(mp3|wav)$/i.test(f.name));
 files.innerHTML=m.length?m.map(f=>`<li><button onclick="api('/api/play?f=${encodeURIComponent(f.name)}')">▶</button> ${esc(f.name)} <small>${(f.size/1024)|0} KB</small></li>`).join('')
  :'<li><small>No songs on the card yet — add some with the Castle app.</small></li>'})
 .catch(()=>files.innerHTML='<li><small>No card to read.</small></li>')}
async function report(){
 const g=u=>fetch(u).then(async r=>(r.ok?'':'HTTP '+r.status+': ')+await r.text()).catch(()=>'(no answer)');
 const [st,he,ev,bl]=await Promise.all(['/api/status','/api/health','/api/events','/api/bootlog'].map(g));
 let j={};try{j=JSON.parse(st)}catch(e){}
 const now=new Date(),t='Castle problem report\nmade '+now.toISOString()+' ('+now+')\nversion '+j.version+' · board '+j.board+' · build '+j.fw_variant
  +'\npage '+location.href+'\n\n== /api/status ==\n'+st+'\n\n== /api/health ==\n'+he+'\n\n== /api/events ==\n'+ev+'\n\n== /api/bootlog ==\n'+bl+'\n';
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([t],{type:'text/plain'}));
 a.download='castle-report-'+now.toISOString().slice(0,19).replace(/:/g,'-')+'.txt';document.body.append(a);a.click();a.remove();say('Report saved.')}
sync();songs();setInterval(sync,5000);
</script>)HTML";

inline esp_err_t send_owner_page(httpd_req_t *req) {
  httpd_resp_set_type(req, "text/html; charset=utf-8");
  return httpd_resp_send(req, kOwnerPage, HTTPD_RESP_USE_STRLEN);
}

// ── GET /owner — the owner's page, whatever the card holds ──────────────
inline esp_err_t h_owner(httpd_req_t *req) {
  set_csp(req);   // E4 — sd_web_site.h; the same policy as every page
  return send_owner_page(req);
}

// ── GET / — the card's own site when it has one, else the owner's page ──
inline esp_err_t h_root(httpd_req_t *req) {
  set_csp(req);
  if (castle_sd::g_mounted) {
    // Prefer the pre-compressed desk: ~3x fewer bytes over the radio, and
    // every browser this decade sends Accept-Encoding: gzip. sd_sync pushes
    // both forms. The .gz wins when both exist — a newer plain index.html
    // is ignored until the gzipped copy is replaced too (see README).
    // MISSING falls through to the next candidate; anything else is this
    // request's whole answer, torn or not (A8) — a desk page that died
    // half way must not be followed by a second, smaller desk page.
    Sent sent = send_sd_file(req, "/sd/site/index.html.gz", "gzip",
                             "text/html; charset=utf-8");
    if (sent == Sent::MISSING) sent = send_sd_file(req, "/sd/site/index.html");
    if (sent == Sent::TORN) return ESP_FAIL;
    if (sent == Sent::WHOLE) return ESP_OK;
  }
  return send_owner_page(req);
}

}  // namespace castle_web
