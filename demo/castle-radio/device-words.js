/* The words the castle link prints, kept apart from the link that polls.
   Nothing here touches the network or the page: every function is a pure
   reading of one castle answer, so the link file stays about the poll and
   these sentences can be asserted on their own. device_site.py inlines this
   ahead of device-link.js, which is the only caller. */
(() => {
  // A version is a string or it is nothing: anything else read as a reboot
  // on every poll and printed as "[object Object]" (found by the link fuzz).
  const textOf = value => (typeof value === 'string' ? value : '');
  const versionOf = s => textOf(s?.version);
  const uptimeOf = s => (Number.isFinite(s?.uptime_s) && s.uptime_s >= 0 ? s.uptime_s : null);
  // A castle that has just booted answers /api/status before its scene table
  // exists; "not installed" is the wrong word for a show that is still loading.
  const sceneCount = s => String(s?.scenes ?? '').split(',').filter(x => x && x !== 'stop').length;
  const booting = s => !!s && sceneCount(s) === 0;
  function friendly(message) {
    if (/timed out/i.test(message)) {return 'Castle not answering · request timed out';}
    if (/refused|unreachable|No route|Errno/i.test(message)) {return 'Castle unreachable at 10.27.27.81';}
    if (/Failed to fetch|NetworkError/i.test(message)) {return 'Control room server is not running';}
    return message;
  }
  function upFor(seconds) {
    if (seconds === null) {return null;}
    const whole = Math.floor(seconds);
    return `${Math.floor(whole / 3600)}:${String(Math.floor(whole / 60) % 60).padStart(2, '0')}:${String(whole % 60).padStart(2, '0')}`;
  }
  // Frames SENT is what this page posted; frames LANDED is what the castle
  // drew; frames COALESCED were overtaken by the next frame inside the same
  // 200 ms drain and never posted at all, which is why sent and total differ.
  // Firmware older than 5.59 counts none of it, so it keeps the old words
  // rather than being told a confident zero.
  function framesText(s) {
    if (!s || typeof s.frames_sent !== 'number') {return '';}
    let line = typeof s.frames_landed === 'number'
      ? `${s.frames_landed} landed of ${s.frames_sent} sent`
      : `${s.frames_sent} of ${s.frames_total} light frames sent`;
    if (s.frames_coalesced > 0) {line += ` (${s.frames_coalesced} coalesced)`;}
    return s.frames_evicted > 0
      ? `${line} (${s.frames_evicted} overwritten before the castle drew them)` : line;
  }
  // One muted line, in the order a fault is read: how slow, how bad it gets,
  // what never came back, how long the castle has been up, what it runs.
  function healthLine(h) {
    const ms = v => (v === null || v === undefined ? '—' : v + ' ms');
    const missed = h.failures ? h.missed_total + ' (' + h.failures + ' in a row)' : h.missed_total;
    return [`link ${ms(h.rtt_ms)}`, `worst ${ms(h.worst_ms)}`, `missed ${missed}`,
      `up ${h.uptime || '—'}`, `firmware ${h.version || '—'}`].join(' · ');
  }
  // Written out rather than nested, in the order the eye reads them: a chip
  // that says nothing is offline, and the idle word is the only difference
  // between the two (the chip is "online", the target is "ready").
  const chipStatus = (online, playing) => {
    if (!online) {return 'offline';}
    return playing ? 'playing' : 'online';
  };
  const targetStatus = (online, playing) => {
    if (!online) {return 'offline';}
    return playing ? 'playing' : 'ready';
  };
  const clockLabel = caps => (caps.position ? 'castle clock' : 'estimated clock');
  // The device panel's "ready" line. A castle still loading its scene table
  // has no count to give yet, so it says what it is doing instead of "0".
  const readyText = (online, s) => {
    if (!online) {return 'Connection needed';}
    if (booting(s)) {return 'Castle is starting up';}
    return `${sceneCount(s)} installed shows`;
  };
  // The small transport button: [glyph, the label a screen reader hears].
  // One function for both so the two can never disagree about what is
  // happening — they were a pair of nested ternaries that had to be read twice.
  const toggleWords = (playing, starting) => {
    if (starting) {return ['…', 'Starting on castle'];}
    return playing ? ['■', 'Stop castle'] : ['▶', 'Play on castle'];
  };
  const playLabel = (busy, playing, starting) => {
    if (busy) {return 'Sending\u2026';}
    if (starting) {return '\u2026 Starting on castle';}
    return playing ? '\u25a0 Stop castle' : '\u25b6 Play on castle';
  };
  const queueDescription = (caps, shuffle) => {
    if (!caps.track_end) {return 'Manual skip on castle \u00b7 automatic queue needs firmware 5.52';}
    return shuffle ? 'Shuffle is on \u00b7 the castle plays what comes next' : 'The castle plays your queue in order';
  };
  const splitStateText = (online, error, caps) => {
    if (!online) {return error || 'Castle unavailable';}
    if (caps.position) {return 'Following the castle\u2019s own clock \u00b7 seeking unavailable';}
    return 'Following castle \u00b7 estimated timing \u00b7 seeking unavailable';
  };
  window.castleWords = {textOf, versionOf, uptimeOf, sceneCount, booting, friendly, upFor, framesText, healthLine,
    chipStatus, targetStatus, clockLabel, readyText, toggleWords, playLabel, queueDescription,
    splitStateText};
})();
