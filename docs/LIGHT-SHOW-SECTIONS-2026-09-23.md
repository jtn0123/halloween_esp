# Section-aware light shows, cue format v2, the porch's soften, and the blind test

September 23, 2026. Continues
[the beat-locked lab](LIGHT-SHOW-BEAT-CHOREOGRAPHY-2026-09-18.md). Software
only: no device contact, no song played anywhere but a browser, no candidate
promoted into `rich_show.prepare`. The prepared baselines are untouched, and
options 5–8 re-generate byte-identical (Monster Mash `c042545f` / `69cd14c2`
/ `ca9be2ca` / `a84c9fde`) after the refactor that made room for this work.

## What the review found

1. **The lab did not show the porch.** The castle's *Soften lightning*
   switch is on by default (`firmware/castle.yaml`, `RESTORE_DEFAULT_ON`);
   the lab rendered with it off. Soften scales every strike's peak by 0.55
   instead of 0.92 and slows every decay to `1 − (1 − d) × 0.35`. A beat
   flash tuned to be down to 12% by the next beat at 140 BPM is still about
   40% lit when it arrives — a steady beat becomes a wash on the porch.
2. **Looks changed every ~7 seconds** (one per 4-bar phrase, cycling six),
   and the second chorus never looked like the first.
3. **The drop's roll was a ~9 Hz white stutter** — inside the flash rate
   the soften switch exists for.
4. **Instrumentals had a fake singer.** Peaks are normalised per stem, so a
   near-silent vocal stem (Halloween Theme: level 0.0025 against the band's
   0.985) still yields 1,223 "sung" onsets of bleed. The stem's raw `level`
   was never consulted. (The production `rich_show.build` routes those
   onsets to the door too — see *Not done* below.)

## Option 9 · Sections

`sections_show.py`, built on `structure.py`, `looks.py` and `porch.py`:

| What you see | How |
| --- | --- |
| One look per kind of passage, held for the section; a chorus returns wearing the same look | Each 4-bar phrase gets a groove fingerprint (where in the bar each band hits, 16 slots per band), plus loudness and singing; phrases are grouped at the strictest similarity that leaves ≤ 4 recurring kinds (+2 heard once). Neighbours of one kind merge into a section, at most 4 phrases (~30 s). |
| Hot looks for loud kinds, calm for quiet; the song's main groove alternates calm ↔ hot | `looks.casting`: a kind at ≥ 62% of the loudest phrase is hot. Three new looks use effects nothing used before: *Watchers* (candle towers, eyes in the door), *Haunt* (spirits, eyes), *Heartbeat* (throb, blood). |
| Hits as hard as the band plays them | Each beat's onset strength against the surrounding 32 beats scales its strike 0.6–1.0. |
| Towers breathe with the band | Tower base level follows the band's envelope bar by bar, ±50%. |
| Into a louder section: build, swell, dark, slam | Two bars of rising tower level; a colour swell (attack 1.5 beats) in the next look's colour; half a beat of dark; one white slam. No white roll. |
| Into a different section: a hand-over | The last two beats become a left-right eighth-note run in the next look's colours — each tower still only once a beat. |
| The band stops dead: dark but for the singer, then a slam | Starts on ≥ 2 beats with almost no band onsets and the band envelope under 40% of typical, provided one of the four bars before was at full strength (a sparse intro is not a stop; a band that thins out for a bar first still is). Lasts, with hysteresis, while the band stays under half its loudness and under its usual onset count — so a singer's faint bleed into the other stems does not end it; the band coming back does. A stop in a section's last bar cancels that transition: silence reads as "quieter", so without this the a cappella call manufactured its own drop. The singer stays lit through a stop — the first cut refused sung notes there. |
| The singer owns the door; held notes glow | Option 8's rule; a sung note with ≥ 450 ms before the next swells in (60 ms) and glows for its length. Onsets are kept only where the vocal envelope is ≥ 15% of the band's there, and not at all when the vocal stem is near-silent. |
| Written for today's porch | `porch.for_todays_castle`: every strike that is not a strobe on its zone (≥ 333 ms since that zone's last strike) gets the intensity and decay that soften turns back into the design. Strobes are left for soften. |

`porch.lead(cues, ms)` shifts a show earlier for when the lights are measured
to trail the speaker. Since v5.72 the castle needs no lead for its own delay:
a card show's cues fire on the samples the speaker has actually played
(`firmware/castle_heard.h`), and `/api/status` reports what the old clock's
error was (`sync_lead_ms`, `sync_drift_ms`). What is left for a lead is
the physics after the amplifier — sound travelling from the speaker to the
listener, about 3 ms a metre — so it stays 0.

### Structure found in the six songs

Four-stem analyses and the drum-based grid (below); sections in order, a
letter per kind of passage.

| Song | Sections | Look changes | Stops (s) | Drops |
| --- | --- | --- | --- | --- |
| Thriller | A B A C A A A B A D E F | 11 in 201 s | 36–41, 186–190 | 1 |
| Halloween Theme | A B C D C D D D C E F (no singer — correct) | 10 in 182 s | — | 0 |
| Day-o | A B C D E D E D F | 8 in 118 s | 30–37, the a cappella call | 2 |
| I Put a Spell on You | A B C D E F E E E | 8 in 121 s | — | 2 |
| Oogie Boogie | A B C D B B B B E F | 9 in 186 s | 11–13, 130–132, 173–176 | 2 |
| Monster Mash | A B C C C D C C D C D C E F — D is each stop-time moment | 13 in 186 s | 77, 118, 145 | 3 |

A look now holds 14–20 s on average, against ~7 s before. The table is
what the analysis *says*; only Day-o's call and Monster Mash's stop-time
have been checked against the song, by timestamp, and none of it by ear.
Halloween Theme is in 5/4, which the 4/4 grid cannot represent; its bar
lines will be wrong.

### Four stems and a drum-based grid

The Radio's split (`tools/stems.py`) now asks demucs for drums, bass and
other as well as vocals, in the same single pass, and writes `backing` as
their sum — correlation 0.998 with the old two-stem backing on Thriller, so
nothing downstream that reads `backing` moves. Old two-stem caches stay
valid; every consumer reads only keys it knew.

`beat_grid.analyse` takes the pulse from the drums (with the backing at
half weight — drums alone heard Day-o's calypso 3-3-2 as 92 BPM) and
chooses the "one" by the backbeat, a heavier kick, bass and chord onsets
on the downbeat, and section changes. With no kit it falls back to the old
low-band method. Tempo is unchanged on all six songs; the "one":

| Song | Old "one" lands on new beat | Decided by | Margin |
| --- | --- | --- | --- |
| Monster Mash | 1 — agree | kick | 1.0 |
| Day-o | 1 — agree | kick | 0.18 (weak) |
| I Put a Spell on You | 1 (52 of 61 bars) | chords | 1.0 |
| Thriller | **2** — the old grid sat on the backbeat | chords | 0.35 |
| Oogie Boogie | 4 | sections | 0.17 (weak) |
| Halloween Theme | 2 | bass | 0.04 (no answer: 5/4) |

`structure.rhythm_bands` listens to kick, snare, hat, bass and keys when a
song has them, so the groove fingerprints are the kit's own. Every lab
candidate (options 5–9) changes with the grid, as it should; the prepared
baselines do not.

## Option 10 · Sections, new firmware

Firmware v5.71 (built and host-tested; **flashed to the castle on
September 23** after this was written — boot only, no v2 show has run on it
yet; the version table
in `firmware/pending/README.md` says so) reads cue format v2: a second,
additive light layer per zone, `look` records that set a zone's overlay,
palette and centre and lock its chase to a rate, directional masks
(left/right/top/bottom halves) and a soften that only touches a zone struck
again within 333 ms. The layout is in `tools/cue_file.py`; the firmware,
desk, Radio preview and Rust overlay copies are kept frame-exact
(`docs/PARITY.md`). A file that uses none of it is still version 1 and
byte-identical.

`sections_v2.py` takes option 9's plan unchanged (`sections_show.draft`)
and lands it the v2 way:

- the singer on layer 1, added to the band instead of replacing it — so
  every sung note is kept (option 9's placer had to refuse the ones that
  would cut a band hit short);
- per section, the towers' chase locked to the tempo: one turn a bar from
  the downbeat in hot looks, two for the final chorus, a meteor dripping
  every two bars in calm ones; a stop stills it and the band's return
  restarts it on the beat;
- hand-overs run across the door's left and right halves with the towers;
  a build's swell climbs the door, bottom half then top;
- no soften compensation.

**A v5.70 castle refuses a v2 file whole and plays the song dark.** So
option 10 is a view of the next firmware, not of the porch.

**The v5.71 soften changes old shows too.** With the switch on, a lone
strike on v5.71 is no longer dimmed — isolated lightning in the authored
scenes peaks at 0.92 instead of 0.55 on the card (full instead of 0.42 in
the desk). Flash trains are softened exactly as before. That is the point
of the change, but it is a visible change to every show, and a reason to
look at a few scenes on the desk before flashing.

## Option 11 · Spin

Justin's verdict on 9 and 10: the beat is much better, and the whole castle
landing together is the thing to keep — but the singer's lights, though they
plainly follow the voice, are boring: every light is one solid block. Option
11 (`spin_show.py`) keeps option 10's plan and unison, and puts the light in
motion between the unison hits. It needs cue format v2 *with arcs*: flash
modes 8–15 (`arc0`…`arc7`), an eighth of a turn each, measured on each
zone's `walk` from 12 o'clock, clockwise (full inside ±1/12 of a turn,
fading to 10% at ±1/4; a Jewel's centre pixel at 30%).

| What you see | How |
| --- | --- |
| The whole castle still hits together on every bar's "one" | Downbeat strikes, white slams and held-note swells are left whole. |
| The towers turn | Every other band hit lands as an arc where the towers' tempo-locked chase is at that instant (`Heads` replays the firmware's overlay clock from the look records), so the hits walk round with the chase. |
| The singer circles the door | Each sung note is an arc on layer 1. Its position follows the melody: `voice_pitch.py` runs YIN on the vocal stem (16 kHz, 10 ms hop, 75–1000 Hz), and the arc steps clockwise when the tune climbs, back when it falls (a whole tone a step, at most three), and one on when a note repeats or has no pitch. A line that pauses ≥ 1.5 s starts again at the top. Notes ≥ 4 semitones over the singer's median burn 35% whiter. |
| A held note spins | A sung note with room to glow sets the door's chase turning from its own arc, at a turn per note length (0.6–2.5 turns/s), and hands the overlay back at 85% of its length. |
| A section change races across the castle | Four eighth notes: left tower's door-side edge, the door's left, the door's right, the right tower's near edge — and back the other way at the next change (door halves on a build's swell, as option 10). |
| A build spins up | Over the six beats before the dark, every zone's chase doubles its rate every two beats. After the white slam two lights burst apart round the door and meet at the bottom, white. |

The pitch track is cached beside the lab's own output
(`comparison/<song>.pitch.json`), never beside the stems. A song with no
vocal stem still walks, one step a note.

The arcs cost the v5.71 image **+160 B of flash and no RAM** (1,247,888 B,
68.0% of the OTA slot); the version stays 5.71 because no castle has run
it. Every copy computes the gate in integers (a turn is 3072 steps) and
matches bit for bit: 208 arc rows of the mode × pixel × zone table and
1,484 random arc gates, C against TypeScript and Rust. On a ring the arc
is measured from pixel 0; on a stick or grid it follows the strip's walk
order, not the drawing.

**How much moves** (a zone counts as *aimed* when its light's brightness-
weighted angle is clearly off-centre; *turns* is the angle it travels,
50 ms frames, soften off):

| Song | Door aimed, 10 → 11 | Door turns | Towers aimed |
| --- | --- | --- | --- |
| Monster Mash | 1% → 86% | 1 → 81 | 4% → 42–54% |
| Oogie Boogie | 0% → 72% | 0 → 52 | 11% → 47% |
| Day-o | 1% → 90% | 1 → 39 | 5% → 27–33% |
| I Put a Spell on You | 0% → 82% | 0 → 33 | 1–3% → 44–49% |
| Thriller | 1% → 51% | 1 → 25 | 12–15% → 36–54% |
| Halloween Theme (no singer) | 7% → 11% | 12 → 6 | 9–15% → 29–55% |

It measures movement, not taste. Fast trains — the burst after a slam, a
quick run of sung notes — are still softened by v5.71 when the switch is on.

## Evidence, and what it does not show

- **Firmware reader:** all twelve option-9 and option-10 card files, run
  through the real v2 `castle_cues.h` (`tests/cxx/cues_check.cpp`), match
  the decoded records within 0.0001 (`sections-firmware-validation.json`).
  Option 9's are version 1 and a v5.70 reader reads them; option 10's are
  version 2 and a v5.70 reader refuses them.
- **The lab's v5.70 is exact:** option 9, option 7 and Day-o rendered by
  the pre-v2 renderer and by the new one with `softAll` differ by 0 over
  30,594 frames. On v5.71 the same option 9 differs by up to 0.84 — its
  compensation is for today's soften, which is why each show is judged on
  its own firmware.
- **Simulation:** `simulate_show.mjs` on both options, every pixel finite.
- **Beats that come back down before the next hit, soften ON**
  (`compare_lights.mjs`, Monster Mash, each show on its own firmware):
  current prepared show 9.4%, option 7 16.5%, option 9 49.5%, option 10
  80.4%. Soften off: 41.3% / 76.2% / 92.3% / 96.6%. That measures
  separation, not taste.
- Tests: `test_sections_show.py` (27) and `test_sections_v2.py` (7) on a
  synthetic song (`lab_song.py`); the thinning-then-stop, stop-before-a-
  louder-section and singer-in-a-stop cases each fail on the code as first
  written. `test_beat_grid.py` (11) covers the drum path, and
  `test_rich_preview.test.mjs` the lab's v5.70 switch.
- Option 11: all six spin files pass the real v2 reader (version 2, every
  arc used); `simulate_show.mjs` runs every cue of all six, every pixel
  finite — after a held note at the very end of Day-o was found handing the
  door back 394 ms after the song. `test_spin_show.py` (9) covers the walk
  with a synthetic melody (up, down, repeated, no stem), the overlay clock,
  races, spin-ups and that fix; `test_voice_pitch.py` (7) runs YIN on
  synthetic tones (110–880 Hz within 0.5%, noise and silence unvoiced).

## The lab

`make show-lab`, then <http://127.0.0.1:8894/show-lab.html>.
`make show-lab-phone` serves the same page to the home network (it prints
the address) for review on a phone, either way up. The two castles fill
the screen, each named on its own sky, between a slim song / candidate bar
and a pinned transport (play, seek, Section, Drop, ⚙). Tap a castle to see
it alone with its LEDs drawn at twice the size — the view for judging a
spin — and tap again for both. Every other setting is in the sheet behind
⚙; a blind test's votes sit above the castles. 🚩 flags the moment on
screen: which show, a tag (love, boring, too busy, off the beat, colour,
dim, bright) and a few optional words. The lab server
(`demo/castle-radio/lab_server.py`, which both make targets now run in place
of `http.server`) appends it to `comparison/notes.jsonl`, and every open page
marks it on the timeline. `show_lab.py --notes` prints the flags song by song
for the next pass.

- **Soften lightning** (on, like the castle) now applies to both sides.
- **Castle firmware**: each show on the castle it is written for (v1
  shows on v5.70, options 10 and 11 on v5.71), or one castle for both —
  v5.71 for both, the porch since the flash, is the default. On v5.70 a v2
  show is dark.
- Six songs instead of two: a song with stems but no prepared show gets a
  baseline the lab prepares for itself with the Radio's own
  `rich_show.build`, written only to the comparison directory.
- The timeline letters each phrase with its kind of passage and blacks out
  the detected stops. "Look changes" now counts base-effect changes, not
  the towers' level updates.
- **Blind test**: two shows of a random song, names hidden, sides shuffled,
  a 15-second clip from a section start, looping until you pick A, B or
  same. The names are revealed after each pick. Picks stay in the browser
  until *Download my picks*; `show_lab.py --verdicts FILE` ranks the shows.

## Not done / follow-ups

- The production `rich_show.build` has the same fake-singer problem for
  instrumental imports. Fixing it changes prepared output, so it waits for
  a go-ahead.
- Time signatures other than 4/4 (Halloween Theme is 5/4).
- The offset between speaker and lights is measured by the castle itself
  since v5.72, and removed rather than compensated: cues follow the samples
  the speaker has played (`firmware/castle_heard.h`), and `/api/status`
  `sync_lead_ms` / `sync_drift_ms` say how far the old stopwatch was off on
  the last show heard. A filming kit that measured it from a phone video was
  written and dropped the same day — it needed someone on the porch, and
  the castle already had the number.
