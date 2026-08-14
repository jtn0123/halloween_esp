# Castle roadmap — accepted improvements, in build order

Picked 2026-08-12. Each phase ships and verifies before the next starts.
Verification: the Playwright screenshot gauntlet + hue/saturation audit, and
for firmware, version bump → OTA → /api/status confirm.

## Phase 1 — Evaluation tooling (cue desk, browser only) — DONE 2026-08-12
- [x] #11 Section overlay: tint hush/verse/chorus tiers behind the waveform
- [x] #12 Onset dots — already existed (lane ticks + washes in waveform_view)
- [x] #14 A/B audition: one button swaps the engine vs the frozen pre-4K
      baseline (exports always use A)
- [x] #13 Per-band mute/solo in the band editor (audition only)
- [x] #15 Live intensity/tail knobs + "Copy as TS" (tweaks DO export)

## Phase 2 — Dynamics engine (track_lights.ts + gen_esphome.py +
##            gen_previewer.py, parity tests for every rule)
- [x] #7  Stereo panning → towerL/towerR from channel energy (2026-08-12:
      pan rides as an optional 3rd onset-tuple element; |pan| ≥ 0.25 decisive)
- [x] #3  Tempo-aware decay/ms from median onset spacing (factor 0.7–1.6,
      neutral < 8 hits so hand-written scenes are untouched)
- [x] #8  Accent = vel ≥ rolling-mean(8) + 0.25 and ≥ 0.55 → boost fires
      below the global bar
- [x] #9  Per-band section gating (2026-08-13: gates reconstructed from the
      exported set-cue notes, so YAML is the single section carrier)
- [x] #5  Silence handling: env < 0.04 held ≥ 2 s → near-black tier, grey
      strip on the waveform
- [x] #6  Anticipation: previous look dips to 45% for 450 ms before a chorus
      (never out of silence)
- [x] #10 attack_ms strike-shape field (2026-08-13: rise of peak*16/attack
      per frame in both renderers; mid band swells 90 ms, drums still slam;
      firmware v5.14). PHASE 2 COMPLETE.

## Phase 3 — Flavors, each behind an editor toggle (default off)
- [x] #1  Palette drift: triad lerp, one lap/min (2026-08-13)
- [x] #2  Chorus takeover: shared warm family in choruses (2026-08-13)
- [x] #4  Sustained swells: env plateaus bloom the towers, exported as
      explicit attack strikes (2026-08-13). PHASE 3 COMPLETE.

## Phase 4 — Device & show night (firmware, one OTA per feature)
- [x] #24 eInk status screen: scene, uptime, SD free, QR to web remote
      (shipped v5.11 2026-08-12; needs one eyeball check — if the text is
      upside down, flip ROT180 in firmware/castle_eink.h)
- [x] #19 Playlist/show mode (2026-08-13, v5.15): show: block, generated
      self-looping script, dark gaps between scenes, /api/show/start|stop,
      device-panel button. Verified advancing live.
- [x] #21 Phone remote at /remote — four giant buttons, embedded in
      flash so it survives a missing SD (v5.16)
- [x] #25 /api/blackout — GET+POST, bookmarkable, kills everything (v5.16)
- [x] #26 Boot self-test: R/G/B/W sweep per zone at plug-in; no tone
      (vigil's opening audio is the speaker test) (v5.16)
- [x] #29 Manifest check: every scene file stat()ed at boot; missing
      names in /api/status + remote status line (v5.16)
- [x] #27 Crash telemetry — already existed as /sd/logs/castle.log (one
      line per boot: version, reason, crash count). PHASE 4 COMPLETE.

## Phase 5 — Hardware-gated
- [ ] #20 Physical trigger (sensor TBD — see below). Firmware side: GPIO +
      debounce + HTTP trigger endpoint can be built before the sensor arrives.
- [ ] #30 Jewels dry-run mode. Do NOT flash castle_sd_jewels.yaml until the
      jewels are physically soldered to A0.

## #20 trigger hardware — DECIDED 2026-08-13: both, plus a button panel
User is ordering both motion (HLK-LD2410 mmWave preferred outdoors over PIR)
and buttons. Jewels are ordered too (#30 unblocks on soldering).

**The side-of-house button panel** (user's design):
- 2–3 arcade buttons, RGB-controlled so their meaning is software:
  green glow = start the show (LED goes DARK once running, so it doesn't
  compete with the castle); red ember = stop; third TBD (scare / next).
- Parts: translucent CLEAR arcade buttons (Adafruit 30mm #471 or 24mm mini
  #3489) + NeoPixel diffused 5mm through-hole LEDs (#1938) swapped into the
  LED holders — no true-RGB arcade button exists off the shelf; this is the
  standard Adafruit-documented retrofit. One WS2812 chain = all buttons.
- Wiring: 3 GPIO inputs (pullups, PIR-style debounce/cooldown) + one short
  NeoPixel chain through a spare 74AHCT125 gate (few-meter run wants the
  shifter + twisted pair). Firmware: map presses to show start / stop /
  blackout APIs that already exist; drive button colours from the same
  mirrored show state the eInk reads.
