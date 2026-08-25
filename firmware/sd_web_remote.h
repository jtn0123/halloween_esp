#pragma once
// The phone remote (#21): four giant buttons for cold thumbs in the dark,
// and under them a speaker strip (v5.38): level 25-100, the sweep from
// the desk's speaker test, quiet. Same tones, same API — see WIRING §5.
//
// Embedded in flash, not on the card, on purpose: this is the page you need
// most exactly when things are going wrong, so it must survive a missing or
// unreadable SD. Reached at /remote — the QR on the eInk panel lands on the
// full desk, and the desk links here; or bookmark it directly.
//
// Every control is one POST to an API the desk already uses. No state lives
// here beyond the show button's label, refreshed from /api/status.

#include <esp_http_server.h>

// Included from the bottom of sd_web.h, after the action queue and reply
// helpers it uses are defined — no forward declarations needed.
namespace castle_web {

// The all-evening playlist (#19): every scene in order with an ambient gap,
// looping until told to stop. The script itself is generated into
// scenes.yaml; these just flip it.
inline esp_err_t h_show_start(httpd_req_t *req) {
  set_pending(SHOW, "1");
  return reply_json(req, "{\"queued\":true}");
}

inline esp_err_t h_show_stop(httpd_req_t *req) {
  set_pending(SHOW, "0");
  return reply_json(req, "{\"queued\":true}");
}

// #25: the panic switch. Playlist, scene, audio, pixels — everything off in
// one call. Registered for GET as well as POST so it works from a plain
// browser bookmark, because the night you need this is the night you are
// not going to type curl flags.
inline esp_err_t h_blackout(httpd_req_t *req) {
  set_pending(BLACKOUT, "");
  return reply_json(req, "{\"queued\":true}");
}


inline const char kRemotePage[] = R"HTML(<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Castle Remote</title>
<style>
html,body{height:100%;margin:0}
body{font:20px system-ui;background:#0d0a14;color:#e8e0f0;display:flex;flex-direction:column}
#grid{flex:1;display:grid;grid-template:1fr 1fr/1fr 1fr;gap:10px;padding:10px}
button{border:0;border-radius:18px;color:inherit;cursor:pointer;
 font-size:7vmin;font-weight:700;display:flex;flex-direction:column;
 align-items:center;justify-content:center;gap:.3em}
button:active{filter:brightness(1.5)}
#show{background:#1d4428}#show.on{background:#6b2020}
#ambient{background:#3a2a55}#scare{background:#553114}#black{background:#222}
#spk{display:flex;gap:8px;padding:0 10px 10px;align-items:center;color:#cbbfe0}
#spk button{flex:1;font-size:18px;padding:.6em 0;border-radius:12px;background:#2a2540}
#spk button[aria-pressed=true]{background:#4a3f75}
#st{color:#9a8fb0;text-align:center;padding:.4rem;font-size:14px}
small{font-size:.45em;font-weight:400;color:#cbbfe0}
</style>
<div id=grid>
<button id=show onclick="toggleShow()">🎃<span id=showTxt>…</span></button>
<button id=ambient onclick="api('/api/scene?s=vigil')">🕯<span>ambient</span><small>vigil</small></button>
<button id=scare onclick="api('/api/scene?s=approach')">👻<span>scare</span><small>approach</small></button>
<button id=black onclick="api('/api/blackout')">🔇<span>blackout</span><small>all off</small></button>
</div>
<div id=spk>🔊<button data-v=25 onclick="api('/api/volume?v=25')">25</button><button data-v=50 onclick="api('/api/volume?v=50')">50</button><button data-v=80 onclick="api('/api/volume?v=80')">80</button><button data-v=100 onclick="api('/api/volume?v=100')">100</button><button onclick="api('/api/play?f=test_sweep.mp3')" title="The speaker test's sweep at the level chosen">test</button><button onclick="api('/api/stop')">quiet</button></div>
<div id=st>…</div>
<script>
let showOn=false;
const api=u=>fetch(u,{method:'POST'}).then(()=>setTimeout(sync,700)).catch(sync);
function toggleShow(){api('/api/show/'+(showOn?'stop':'start'))}
function sync(){fetch('/api/status').then(r=>r.json()).then(s=>{
  showOn=!!s.show_on;
  showTxt.textContent=showOn?'stop the show':'start the show';
  document.getElementById('show').classList.toggle('on',showOn);
  document.querySelectorAll('#spk [data-v]').forEach(b=>b.setAttribute('aria-pressed',+b.dataset.v===s.volume));
  st.textContent='v'+s.version+' · '+(s.scene||'idle')+(s.track?' · '+s.track:'')+' · 🔊'+s.volume+'%'
    +(s.missing?' · ⚠ missing: '+s.missing:'');
}).catch(()=>st.textContent='castle not answering')}
sync();setInterval(sync,4000);
</script>)HTML";

inline esp_err_t h_remote(httpd_req_t *req) {
  set_csp(req);  // E4 — sd_web_site.h; same policy as the desk page
  httpd_resp_set_type(req, "text/html; charset=utf-8");
  return httpd_resp_send(req, kRemotePage, HTTPD_RESP_USE_STRLEN);
}

}  // namespace castle_web
