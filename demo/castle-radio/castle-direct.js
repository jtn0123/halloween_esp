/* Castle direct: the same control room, served by the castle itself.
   device_site.py inlines this ahead of every other script in the page it
   publishes to the SD card. Every route server.py answers on a computer is
   answered here from the firmware's own /api, so the page needs nothing but
   the castle: status settling, the command builders, the SD inventory and the
   generated-light streamer are ports of device_bridge.py and remote_library.py.
   Importing, separation and waveforms stay on the computer; those routes
   answer with a sentence that says so. */
/* global current, load */
(() => {
  const byId = id => document.getElementById(id);
  const parse = id => JSON.parse(byId(id)?.textContent || 'null');
  const scenes = parse('radio-scenes') || [];
  const library = parse('radio-library') || [];
  const nativeFetch = window.fetch.bind(window);
  const SETTLE_MS = 2500, STATUS_MS = 250, LISTING_MS = 4000, TIMEOUT_MS = 8000;
  const AUDIO = /\.(mp3|opus|wav)$/i;
  const LIGHT = /^(?:(?:towerL|towerR|door):)?(?:[0-9a-fA-F]{6}|white|off|show|bars|chase|ends)(?:@(?:[1-9]|[1-9]\d|100))?$/;
  const TONES = new Set(['test_sweep.mp3', 'test_1k.mp3', 'test_200.mp3', 'test_4k.mp3', 'test_silence.mp3']);
  const PLAIN = {stop: '/api/stop', blackout: '/api/blackout', 'show/start': '/api/show/start', 'show/stop': '/api/show/stop'};
  const COOLDOWNS = new Set([30, 60, 120]);
  const COMPUTER_ONLY = 'This runs in the control room on your computer, not on the castle itself.';
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const json = (payload, status = 200) => new Response(JSON.stringify(payload), {status, headers: {'Content-Type': 'application/json'}});
  const refuse = (message, status = 400) => json({error: message}, status);
  const isAudioName = name => !!name && !name.includes('/') && !name.includes('\\') && AUDIO.test(name);

  // ── the castle's own API, with a timeout the page can name ─────────────
  async function castle(path, method = 'GET') {
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), TIMEOUT_MS);
    let response;
    try { response = await nativeFetch(path, {method, signal: abort.signal, cache: 'no-store'}); }
    catch (error) {
      if (error.name === 'AbortError') {throw new Error('Castle not answering · request timed out');}
      throw new Error('Castle not answering · check the Wi-Fi link');
    } finally { clearTimeout(timer); }
    const text = await response.text();
    if (!response.ok) {throw new Error(text.trim() || `Castle answered ${response.status}`);}
    return text ? JSON.parse(text) : {};
  }
  let statusAt = 0, statusPromise = null;
  function status(fresh = false) {
    if (!fresh && statusPromise && performance.now() - statusAt < STATUS_MS) {return statusPromise;}
    statusAt = performance.now();
    statusPromise = castle('/api/status').catch(error => { statusPromise = null; throw error; });
    return statusPromise;
  }
  const listings = new Map();
  function listing(dir = '') {
    const cached = listings.get(dir);
    if (cached && performance.now() - cached.at < LISTING_MS) {return cached.rows;}
    const rows = castle(dir ? `/api/files?d=${encodeURIComponent(dir)}` : '/api/files').catch(error => { listings.delete(dir); throw error; });
    listings.set(dir, {at: performance.now(), rows});
    return rows;
  }
  const forget = () => { statusPromise = null; listings.clear(); };
  const versionAtLeast = (value, [major, minor]) => {
    const [a, b] = String(value || '').split('.').map(Number);
    return a > major || (a === major && (b || 0) >= minor);
  };

  // ── what a command asked for, until the castle reports it ──────────────
  const expected = {scene: null, track: null, until: 0};
  function expect(scene, track) { Object.assign(expected, {scene, track, until: performance.now() + SETTLE_MS}); statusPromise = null; }
  function settle(state) {
    if (expected.until <= performance.now()) {return state;}
    const sceneOk = expected.scene === null || state.scene === expected.scene;
    const trackOk = expected.track === null || state.track === expected.track;
    if (sceneOk && trackOk) { expected.until = 0; return state; }
    const next = {...state, settling: true};
    if (expected.scene !== null) {next.scene = expected.scene;}
    if (expected.track !== null) {next.track = expected.track;}
    if ('position_ms' in state) { next.playing = !!(expected.track || (expected.scene && expected.scene !== 'stop')); next.position_ms = 0; }
    return next;
  }
  function playback(state) {
    const playing = !!state.playing;
    return {position_s: playing ? Math.max(0, (state.position_ms || 0) / 1000) : 0, estimated: false, origin: 'castle', playing, scene: state.scene, track: state.track};
  }
  function capabilities(state) {
    return {scene: true, files: true, volume: true, pir: true, pause: false, seek: false,
      dynamic_lights: versionAtLeast(state.version, [5, 51]), position: 'position_ms' in state, track_end: versionAtLeast(state.version, [5, 52])};
  }

  // ── generated lights for a synced import, on the castle's clock ────────
  const show = {active: false, track: null, frames_sent: 0, frames_total: 0, error: null, token: 0};
  async function stopShow() { show.token++; show.active = false; show.track = null; }
  const now = () => performance.now() / 1000;
  async function awaitTrack(filename, token, first) {
    let state = await status(true);
    for (let tries = first ? 15 : 0; tries > 0 && state.track !== filename; tries--) {
      await sleep(200);
      if (token !== show.token) {return null;}
      state = await status(true);
    }
    return state.track === filename ? state : null;
  }
  async function align(filename, started, token, first = false) {
    let state = await awaitTrack(filename, token, first);
    if (!state) {return null;}
    if (!('position_ms' in state)) {return started;}
    // Firmware 5.55 holds position_ms at 0 until the speaker itself runs.
    const sounding = s => !!s.playing && (s.position_ms || 0) > 0;
    for (let tries = 10; tries > 0 && !sounding(state); tries--) {
      await sleep(200);
      if (token !== show.token) {return null;}
      state = await status(true);
      if (state.track !== filename) {return null;}
    }
    return sounding(state) ? now() - state.position_ms / 1000 : started;
  }
  async function runShow(filename, frames, duration) {
    const token = ++show.token;
    Object.assign(show, {active: true, track: filename, frames_sent: 0, frames_total: frames.length, error: null});
    try {
      let started = await align(filename, now() + 0.24, token, true);
      for (let index = 0; started !== null && index < frames.length; index++) {
        const [at, spec] = frames[index];
        await sleep(Math.max(0, (started + at - now()) * 1000));
        if (token !== show.token) {return;}
        await castle(`/api/light?c=${encodeURIComponent(spec)}`, 'POST');
        show.frames_sent = index + 1;
        if (index && index % 20 === 0) {started = await align(filename, started, token);}
      }
      if (started !== null) {await sleep(Math.max(0, (started + (duration || 0) - now()) * 1000));}
    } catch (error) { show.error = error.message; }
    finally {
      if (token === show.token) {
        show.active = false; show.track = null;
        castle('/api/light?c=off', 'POST').catch(() => {});
      }
    }
  }

  // ── the command builders: every castle URL is rebuilt from known tokens ─
  async function installedScene(name) {
    const installed = (await status()).scenes.split(',');
    const scene = installed.find(s => s === name);
    if (!scene) {throw new Error('This light show is not installed in the current firmware.');}
    return scene;
  }
  async function castleFile(name, missing) {
    const row = (await listing()).find(f => f.name === name && !f.dir);
    if (!row) {throw new Error(missing);}
    return String(row.name);
  }
  function percent(value, what = 'Volume') {
    const number = Number.parseInt(value, 10);
    if (!(number >= 0 && number <= 100)) {throw new Error(`${what} must be between 0 and 100.`);}
    return number;
  }
  function lightSpec(value) {
    if (!LIGHT.test(value)) {throw new Error('Choose a valid castle light test.');}
    return value;
  }
  async function scenePath(body) {
    const scene = await installedScene(String(body.scene || ''));
    return {path: `/api/scene?s=${encodeURIComponent(scene)}`, scene};
  }
  async function filePath(body) {
    const wanted = String(body.file || '');
    if (!isAudioName(wanted)) {throw new Error('Choose a playable castle audio file.');}
    const filename = await castleFile(wanted, 'That audio file is not on the castle.');
    return {path: `/api/play?f=${encodeURIComponent(filename)}`, filename};
  }
  async function tonePath(body) {
    if (!TONES.has(String(body.file || ''))) {throw new Error('Choose a diagnostic tone.');}
    const volume = percent(body.volume ?? 50);
    const filename = await castleFile(String(body.file), 'That diagnostic tone is not on the castle.');
    await stopShow();
    await castle(`/api/volume?v=${volume}`, 'POST');
    await sleep(300);
    return {path: `/api/play?f=${encodeURIComponent(filename)}`};
  }
  async function lightPath(body) { await stopShow(); return {path: `/api/light?c=${encodeURIComponent(lightSpec(String(body.value || '')))}`}; }
  async function volumePath(body) { return {path: `/api/volume?v=${percent(body.volume ?? 0)}`}; }
  async function pirPath(body) {
    const cooldown = Number.parseInt(body.cooldown ?? 60, 10);
    if (!COOLDOWNS.has(cooldown)) {throw new Error('Choose a supported motion cooldown.');}
    return {path: `/api/pir?armed=${body.armed ? 1 : 0}&cooldown=${cooldown}`};
  }
  const BUILDERS = {scene: scenePath, file: filePath, light: lightPath, tone: tonePath, volume: volumePath, pir: pirPath};
  async function command(body) {
    const action = String(body.action || '');
    let built;
    if (action in PLAIN) {built = {path: PLAIN[action]};}
    else if (action in BUILDERS) {built = await BUILDERS[action](body);}
    else {throw new Error('This control is not supported by the running firmware.');}
    if (action in PLAIN || action === 'scene') {await stopShow();}
    const imported = action === 'file' && body.key ? library.find(row => row.key === body.key) : null;
    if (imported) {
      if (!versionAtLeast((await status()).version, [5, 51])) {throw new Error('Generated imported lights require castle firmware 5.51 or newer.');}
      await castle('/api/light?c=show', 'POST');
      await sleep(300);
    }
    const result = await castle(built.path, 'POST');
    if (action === 'scene') {expect(built.scene, null);}
    else if (action === 'file') { expect('stop', built.filename); if (imported) {runShow(built.filename, imported.frames || [], imported.duration);} }
    else if (action === 'stop' || action === 'blackout' || action === 'show/stop') {expect('stop', '');}
    else {forget();}
    return result;
  }

  // ── what the card holds, as the sync panel understands it ──────────────
  async function inventory() {
    const [state, files, sceneFiles] = await Promise.all([status(), listing(), listing('scenes')]);
    const installed = new Set(state.scenes.split(','));
    const audio = new Map(files.filter(f => !f.dir).map(f => [f.name, f.size]));
    const sceneAudio = new Set(sceneFiles.filter(f => !f.dir).map(f => f.name));
    const tracks = {};
    for (const scene of scenes) {
      const ready = installed.has(scene.id) && sceneAudio.has(scene.file);
      tracks[scene.file] = {status: ready ? 'ready' : 'missing', audio: sceneAudio.has(scene.file), lights: installed.has(scene.id), can_sync: false};
    }
    const known = new Set();
    for (const row of library) {
      const present = audio.get(row.filename) === row.bytes;
      known.add(row.filename);
      tracks[row.key] = {status: present ? 'audio_only' : 'missing', audio: present, lights: installed.has(row.key), can_sync: false, filename: present ? row.filename : null, bytes: present ? row.bytes : null};
    }
    const other = [...audio].filter(([name]) => AUDIO.test(name) && !known.has(name)).map(([name, bytes]) => ({name, bytes})).sort((a, b) => a.name.localeCompare(b.name));
    return {tracks, jobs: {}, other_audio: other};
  }
  async function presentRows() {
    const audio = new Map((await listing()).filter(f => !f.dir).map(f => [f.name, f.size]));
    return library.filter(row => audio.get(row.filename) === row.bytes).map(({frames, ...row}) => row);
  }
  async function deleteAudio(name) {
    if (!isAudioName(name)) {throw new Error('Choose a castle audio file.');}
    const listed = await castleFile(name, 'That audio file is not on the castle.');
    const result = await castle(`/api/files/${encodeURIComponent(listed)}`, 'DELETE');
    forget();
    return result;
  }

  // ── the routes server.py used to answer ────────────────────────────────
  async function device() {
    try {
      const state = settle(await status());
      return json({connected: true, host: location.host, state, playback: playback(state), capabilities: capabilities(state),
        light_show: {active: show.active, track: show.track, frames_sent: show.frames_sent, frames_total: show.frames_total, error: show.error}});
    } catch (error) { return json({connected: false, host: location.host, error: error.message}, 502); }
  }
  async function answer(path, options) {
    const method = (options?.method || 'GET').toUpperCase();
    if (path === '/radio/device') {return device();}
    if (path === '/radio/device/command' && method === 'POST') {return json(await command(JSON.parse(options.body || '{}')));}
    if (path === '/radio/device/library') {return json(await inventory());}
    if (path.startsWith('/radio/device/audio/') && method === 'DELETE') {return json(await deleteAudio(decodeURIComponent(path.slice('/radio/device/audio/'.length))));}
    if (path === '/radio/library') {return json(await presentRows());}
    if (path === '/radio/jobs') {return json([]);}
    if (path === '/scenes.json') {return json(scenes);}
    if (path.startsWith('/radio/waveform/')) {return refuse('Waveforms are analyzed in the control room on your computer.', 404);}
    if (path.startsWith('/radio/device/sync')) {return refuse('Sync audio from the control room on your computer; this page plays what is already on the card.');}
    return refuse(COMPUTER_ONLY);
  }
  window.fetch = (input, options) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    const local = url.pathname.startsWith('/radio/') || url.pathname === '/scenes.json';
    if (url.origin !== location.origin || !local) {return nativeFetch(input, options);}
    return answer(url.pathname, options).catch(error => {
      const busy = /not answering|answered \d+/.test(error.message);
      return refuse(error.message, busy ? 502 : 400);
    });
  };

  // ── the page itself: this castle is the default output ─────────────────
  const target = byId('output-target');
  if (target) { target.options[1].textContent = `Porch castle · ${location.host}`; target.value = 'castle'; }
  // app.js leaves the audio element empty while the castle is the output;
  // choosing the browser loads the current song (at preload=none: no bytes
  // until Play).
  target?.addEventListener('change', () => { if (target.value === 'computer') {load(current);} });
  // The computer build prints the castle's address; here the address is our own.
  for (const dd of document.querySelectorAll('dd')) { if (/^\d{1,3}(\.\d{1,3}){3}$/.test(dd.textContent.trim())) {dd.textContent = location.host;} }
  const form = byId('import-form');
  if (form) {
    for (const control of form.querySelectorAll('input, button')) {control.disabled = true;}
    byId('import-message').textContent = COMPUTER_ONLY;
  }
  window.castleDirect = {scenes, library, show};
})();
