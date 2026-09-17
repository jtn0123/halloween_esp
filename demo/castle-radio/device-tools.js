/* Hardware diagnostics adapted from the original castle cue desk. */
/* global $, toast */
(() => {
  const device = $('device');
  const grid = device.querySelector('.settings-grid');
  const capabilityNote = grid.querySelector('.notice');
  const bench = document.createElement('section');
  bench.className = 'hardware-bench';
  bench.innerHTML = `
    <div class="bench-heading">
      <div><div class="eyebrow">LIVE HARDWARE BENCH</div><h2>Find the fault, one signal at a time</h2></div>
      <span id="bench-connection" class="bench-chip">Checking castle…</span>
    </div>
    <p class="subtle">These controls operate the physical castle. A light test stops the current show so wiring can be judged without scene effects.</p>
    <div class="bench-grid">
      <article class="bench-card">
        <div class="bench-card-title"><span>01</span><div><h3>LED channels</h3><p>Test each data line and color channel.</p></div></div>
        <div class="bench-brightness" role="group" aria-label="LED test brightness">
          <small>Brightness</small><button data-bright="25">25%</button><button data-bright="50" aria-pressed="true">50%</button><button data-bright="75">75%</button><button data-bright="100">100%</button>
        </div>
        <div class="led-test-table">
          <div class="led-test-head"><span>Output</span><span>R</span><span>G</span><span>B</span><span>W</span><span>Off</span></div>
          ${[['towerL','Tower L'],['door','Door'],['towerR','Tower R'],['','All']].map(([zone,label])=>`<div class="led-test-row"><b>${label}</b><button class="swatch red" data-led="${zone}:ff0000" aria-label="${label} red"></button><button class="swatch green" data-led="${zone}:00ff00" aria-label="${label} green"></button><button class="swatch blue" data-led="${zone}:0000ff" aria-label="${label} blue"></button><button class="swatch white" data-led="${zone}:white" aria-label="${label} white">W</button><button data-led="${zone}:off">Off</button></div>`).join('')}
        </div>
        <div class="bench-patterns"><small>Strip diagnosis</small><button data-led="bars">RGB bars</button><button data-led="chase">Chase</button><button data-led="ends">Ends</button></div>
        <div class="bench-custom"><label>Custom color <input id="bench-color" type="color" value="#7c3aed"></label><button id="bench-color-send">Send color</button><button data-led="off">All lights off</button></div>
      </article>
      <article class="bench-card">
        <div class="bench-card-title"><span>02</span><div><h3>Speaker & audio</h3><p>Separate frequency, power, and noise faults.</p></div></div>
        <label class="tone-level">Test level <output id="tone-level-value">50%</output><input id="tone-level" type="range" min="10" max="100" value="50"></label>
        <div class="tone-list">
          <button data-tone="test_sweep.mp3"><b>Sweep</b><small>200 Hz → 10 kHz · missing frequencies</small></button>
          <button data-tone="test_1k.mp3"><b>1 kHz</b><small>Reference · distortion</small></button>
          <button data-tone="test_200.mp3"><b>200 Hz</b><small>Bass load · 5 V rail sag</small></button>
          <button data-tone="test_4k.mp3"><b>4 kHz</b><small>Data and wiring noise</small></button>
          <button data-tone="test_silence.mp3"><b>Silence</b><small>Ground hum and hiss</small></button>
        </div>
        <button id="bench-audio-stop" class="bench-stop">■ Stop speaker</button>
      </article>
    </div>
    <div class="bench-console"><span class="bench-lamp"></span><div><b id="bench-result">Bench ready</b><small id="bench-detail">Choose one test. The result and castle state will appear here.</small></div></div>
    <div class="bench-events"><button id="bench-events-load">Recent castle events</button><b id="bench-health-row" class="subtle"></b><pre id="bench-events-log" class="subtle">The castle keeps a short record of what it did. Ask for it when something looked wrong.</pre></div>`;
  grid.parentNode.insertBefore(bench, grid);

  let brightness = 50;
  const result = (title, detail, error=false) => {
    $('bench-result').textContent=title;
    $('bench-detail').textContent=detail;
    bench.classList.toggle('has-error',error);
  };
  async function command(body, label, note) {
    result(`Running ${label}…`,'Sending command to 10.27.27.81');
    const ok=await window.castleLink.command(body);
    // Still sending is not a failure: a second press while the first command
    // was in the air used to paint the console red and say the test failed.
    if(ok===window.castleLink.BUSY){
      result(`${label} is waiting`,'Still sending the previous command · press again in a moment');
      return;
    }
    if(!ok){
      result(`${label} failed`,window.castleLink.lastError()||'Castle command failed',true);
      return;
    }
    result(`${label} is running`,note||'Command accepted by the physical castle. Watch and listen at the device.');
    toast(`${label} sent to castle`);
  }
  bench.querySelectorAll('[data-bright]').forEach(button=>button.onclick=()=>{
    brightness=Number(button.dataset.bright);
    bench.querySelectorAll('[data-bright]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));
  });
  bench.querySelectorAll('[data-led]').forEach(button=>button.onclick=()=>{
    let spec=button.dataset.led.replace(/^:/,'');
    if(!spec.endsWith('off')){spec+=`@${brightness}`;}
    command({action:'light',value:spec},button.getAttribute('aria-label')||button.textContent.trim());
  });
  $('bench-color-send').onclick=()=>command({action:'light',value:`${$('bench-color').value.slice(1)}@${brightness}`},'custom color');
  $('tone-level').oninput=()=>{$('tone-level-value').textContent=`${$('tone-level').value}%`;};
  bench.querySelectorAll('[data-tone]').forEach(button=>button.onclick=()=>command({action:'tone',file:button.dataset.tone,volume:Number($('tone-level').value)},button.querySelector('b').textContent));
  $('bench-audio-stop').onclick=()=>command({action:'stop'},'speaker stop','The speaker is quiet. This does not end the installed playlist — use “Stop castle” in Your castle for that.');
  // The castle's own answer to /api/status, kept so the ring's uptime
  // stamps can be turned into wall-clock times (L2, v5.62).
  let status = null;
  // Unix ms of the instant this boot started, or null until SNTP has
  // answered. epoch is 0 until then and the ring only ever knows uptime, so
  // without both numbers there is nothing to convert with.
  function bootWallMs() {
    const epoch = Number(status?.epoch), up = Number(status?.uptime_s);
    if (!Number.isFinite(epoch) || epoch <= 0 || !Number.isFinite(up)) {return null;}
    return (epoch - up) * 1000;
  }
  // +mm:ss.s against the NEWEST entry, so the last thing the castle did reads
  // +00:00.0 and everything above it says how long before that it happened.
  // Once the castle has a clock, the real time is what the operator actually
  // remembers ("it went dark some time after nine") and it wins.
  function stamp(t, newest, base) {
    if (!Number.isFinite(Number(t))) {return ' ??:??.?';}
    if (base !== null) {return new Date(base + Number(t)).toTimeString().slice(0, 8);}
    const delta = (Number(t) - newest) / 1000, size = Math.abs(delta);
    const mm = String(Math.floor(size / 60)).padStart(2, '0');
    return `${delta < 0 ? '-' : '+'}${mm}:${(size % 60).toFixed(1).padStart(4, '0')}`;
  }
  // Only rows shaped like the firmware's are shown; one odd entry must not
  // turn a 63-line record into "not supported" (found by the events fuzz).
  function renderEvents(answer) {
    const rows = Array.isArray(answer) ? answer.filter(row => row && typeof row === 'object') : [];
    if (!rows.length) {
      $('bench-events-log').textContent = 'The castle has not recorded anything since it booted.';
      return;
    }
    const newest = Number(rows.findLast(row => Number.isFinite(Number(row.t)))?.t) || 0;
    const base = bootWallMs();
    $('bench-events-log').textContent = rows.map(row => `${stamp(row.t, newest, base)}  ${row.e}  ${row.a ?? ''}`.trimEnd()).join('\n');
  }
  // L7/L9: the row the runbook sends you to look at. heap_min_kb is the
  // LOW-WATER mark — heap_free_kb reads healthy again the moment the
  // allocation that failed is handed back, which is why "look at heap" has
  // never once caught the fault it was written for.
  function renderHealth(h) {
    if (!h || typeof h !== 'object') {$('bench-health-row').textContent = ''; return;}
    const kb = v => (Number.isFinite(Number(v)) ? `${v} KB` : '—');
    let sd = 'SD ready';
    if (status?.sd_mounted === false) {sd = 'SD unavailable';}
    else if (Number.isFinite(Number(status?.sd_free_kb))) {sd = `SD ${Math.round(Number(status.sd_free_kb) / 1024)} MB free`;}
    const parts = [`${h.boots ?? '—'} boots · ${h.crashes ?? '—'} crashes`,
      `last reset ${h.last_reset || '—'}`,
      `heap now ${kb(status?.heap_free_kb)} · lowest ${kb(h.heap_min_kb)}`, sd];
    if (h.sd_read_errors) {
      const last = h.sd_last_error ? ` · last ${h.sd_last_error}` : '';
      parts.push(`${h.sd_read_errors} card read errors${last}`);
    }
    if (Number.isFinite(Number(status?.rssi)) && Number(status?.rssi)) {parts.push(`signal ${status.rssi} dBm`);}
    $('bench-health-row').textContent = parts.join(' · ');
  }
  $('bench-events-load').onclick = async () => {
    $('bench-events-log').textContent = 'Asking the castle…';
    try { renderHealth(await (await fetch('/radio/device/health')).json()); }
    catch { $('bench-health-row').textContent = ''; }
    try { renderEvents(await window.castleLink.events()); }
    catch { $('bench-events-log').textContent = 'Recent castle events are not supported by this firmware.'; }
  };
  function capabilityText(caps) {
    if (caps.track_end) {
      return 'Installed scenes and imported songs run physical lights, the scrubber follows the castle’s own clock, and the queue moves on when a song ends. Pausing and seeking are not supported by the firmware.';
    }
    if (caps.dynamic_lights) {
      return 'Installed scenes and imported songs run physical lights. Firmware 5.52 adds the castle’s own clock and automatic queue advance.';
    }
    return 'Manual tests work now. Imported generated lights need castle firmware 5.51 or newer.';
  }
  // One poll for the whole page: the shared castle link feeds this bench.
  window.castleLink.subscribe(({connected,state,caps,health,lightShow,error,healthLine,framesText})=>{
    status = state;
    $('live-link-health').textContent=healthLine||'';
    if(!connected){
      $('bench-connection').textContent=error?`Castle unavailable · ${error}`:'Castle unavailable';
      $('bench-connection').classList.remove('online');
      return;
    }
    $('bench-connection').textContent=`Online · firmware ${health?.version||'—'}${caps.position?' · castle clock':''}`;
    $('bench-connection').classList.add('online');
    capabilityNote.textContent=capabilityText(caps);
    if(lightShow?.active) {result('Generated lights are live',`${framesText} with ${lightShow.track}`);}
  });
})();
