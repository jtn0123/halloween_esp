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
- [ ] #10 attack_ms strike-shape field (lows slam, pads bloom)

## Phase 3 — Flavors, each behind an editor toggle (default off)
- [ ] #1  Palette drift: triad hue rotates slowly over the song
- [ ] #2  Chorus takeover: unified palette during chorus sections
- [ ] #4  Sustained-note detection → slow zone-wide swells

## Phase 4 — Device & show night (firmware, one OTA per feature)
- [x] #24 eInk status screen: scene, uptime, SD free, QR to web remote
      (shipped v5.11 2026-08-12; needs one eyeball check — if the text is
      upside down, flip ROT180 in firmware/castle_eink.h)
- [ ] #19 Playlist/show mode: ordered scenes, crossfade, ambient gaps
- [ ] #21 Phone "big buttons" page: Ambient / Scare / Song / Off
- [ ] #25 /api/blackout panic endpoint
- [ ] #26 Boot self-test sweep (zones, colours, test tone)
- [ ] #29 SD manifest check: verify every audio_file on boot, surface missing
- [ ] #27 Crash telemetry: append health + reset reason to CSV on SD each boot

## Phase 5 — Hardware-gated
- [ ] #20 Physical trigger (sensor TBD — see below). Firmware side: GPIO +
      debounce + HTTP trigger endpoint can be built before the sensor arrives.
- [ ] #30 Jewels dry-run mode. Do NOT flash castle_sd_jewels.yaml until the
      jewels are physically soldered to A0.

## #20 sensor decision (pending purchase)
Recommendation: combo — wired button for control, motion for automation.
- Wired big-dome arcade button + 2-core cable to a GPIO (internal pullup).
  Foolproof, zero latency, works with cold hands in the dark. ~$8.
- Motion: PIR (HC-SR501, ~$3) is fine under a porch roof but false-triggers in
  sun/wind/heat; mmWave presence (HLK-LD2410, ~$5, UART) is far more reliable
  outdoors. For a walkway "beam", VL53L1X ToF works to ~3 m.
- Firmware treats both as the same trigger event with a cooldown, so either
  can be added whenever it arrives.
