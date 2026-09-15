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
    <div class="bench-console"><span class="bench-lamp"></span><div><b id="bench-result">Bench ready</b><small id="bench-detail">Choose one test. The result and castle state will appear here.</small></div></div>`;
  grid.parentNode.insertBefore(bench, grid);

  let brightness = 50;
  const result = (title, detail, error=false) => {
    $('bench-result').textContent=title;
    $('bench-detail').textContent=detail;
    bench.classList.toggle('has-error',error);
  };
  async function command(body, label) {
    result(`Running ${label}…`,'Sending command to 10.27.27.81');
    try {
      const response=await fetch('/radio/device/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const data=await response.json();
      if(!response.ok||data.error){throw new Error(data.error||'Castle command failed');}
      result(`${label} is running`,'Command accepted by the physical castle. Watch and listen at the device.');
      toast(`${label} sent to castle`);
    } catch(error) {
      result(`${label} failed`,error.message,true);
      toast(error.message);
    }
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
  $('bench-audio-stop').onclick=()=>command({action:'stop'},'speaker stop');
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
  window.castleLink.subscribe(({connected,state,caps,lightShow,error})=>{
    if(!connected){
      $('bench-connection').textContent=error?`Castle unavailable · ${error}`:'Castle unavailable';
      $('bench-connection').classList.remove('online');
      return;
    }
    $('bench-connection').textContent=`Online · firmware ${state.version}${caps.position?' · castle clock':''}`;
    $('bench-connection').classList.add('online');
    capabilityNote.textContent=capabilityText(caps);
    if(lightShow?.active) {result('Generated lights are live',`${lightShow.frames_sent} of ${lightShow.frames_total} light frames sent with ${lightShow.track}`);}
  });
})();
