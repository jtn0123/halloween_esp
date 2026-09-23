# Beat-locked choreography candidates (options 5–7) and the show lab

September 18, 2026, evening. Continues
[the handoff](LIGHT-SHOW-AGENT-HANDOFF-2026-09-18.md). Software only: no
device contact, no audio playback, no promotion of any candidate. The
prepared baselines are byte-identical (`1b80e0f6` Monster Mash, `37e69d96`
Day-o, checked with `shasum` before and after every generation).

## Why options 1–4 still looked the same

All four kept the baseline's two structural problems and only re-coloured
or thinned the hits:

1. **No pulse.** The baseline strikes on every detected onset — about 24 a
   second in Monster Mash. Options 2–4 alternated towers on *onsets*, which
   arrive irregularly, so the alternation never reads as left-right-left.
2. **No sections.** Monster Mash sits in one look (`seance` at 0.7) from
   0:10 to 2:59. Nothing a viewer would call a change happens for minutes.

## What options 5–7 do instead

`beat_grid.py` finds the pulse from the onsets the importer already keeps:
tempo by autocorrelation, beats by dynamic programming over the onset
envelope (so the grid bends with a human drummer), each beat snapped onto
the real drum hit beside it, bar phase from where the low band lands, and
4-bar phrases ranked quiet / middle / loud by the loudness peaks. Measured:
Monster Mash 139.5 BPM, 415 of 434 beats on a detected onset; Day-o
122.4 BPM with a free-time intro it treats as one long quiet phrase.

`choreography.py` spends the same card format on structure:

| Option | What you see |
| --- | --- |
| 5 · Beat lock | Towers trade the beat left-right; the whole castle lands on every "one"; the look changes every 4 bars. |
| 6 · Sweeps | A sweep runs tower → door → tower inside each beat and turns round every bar; tower rings rotate (`chase` overlay); instrument fills stay as small sparkles. |
| 7 · Full show | Pattern chosen per phrase by loudness — breathe, heartbeat, ping-pong, sweep, stomp — and before the song lifts: a climbing roll, half a beat of darkness, a white slam. Ends on one long white hit sinking to embers. |

Shared by all three:

- **Six looks** (Graveyard, Furnace, Séance, Toxic, Blood moon, Mansion): a
  base effect, level and colour pair per phrase. Neighbouring phrases never
  share one. Base levels stay low so hits have somewhere to go.
- **Decay fitted to the beat**: each flash is tuned to be down to ~12% by
  the next beat, so beats stay separate at any tempo.
- **The door follows the singer** between pattern hits, in the look's colour.
- **Replacement-aware placement.** The card reader *replaces* a zone's
  flash; a weak vocal or fill hit 100 ms after a beat would cut the beat
  short. `Placer` refuses an ornament while a brighter flash is live, when a
  pattern hit is under 110 ms away, or inside a roll. The slam owns its
  downbeat — the next phrase's own "one" is withheld.

Rolls are six strikes at sixteenth-note spacing (about 9 a second at
140 BPM, for under half a second). The sky-wash flash stays off, as it is
for imported songs in the Radio page.

## Option 8 · Singer's door (the stems, split cleanly)

Options 5–7 already use the vocal/backing split — the beat grid comes from the
backing stem, the tower sparkles from its left and right channels, and the
door's between-beat hits from the vocal stem — but the door is *shared*: in
option 7 it takes 180 band hits in Monster Mash beside 365 of the 458 sung
onsets (Day-o: 94 beside 197 of 245), and a sung hit is refused whenever a
band hit is live. Option 8 (`duet`) is option 7 with one rule added: **while
there is singing** (a sung onset from 0.5 s before to 1 s after) **the door is
the singer's alone** — the band's pattern keeps to the two towers, and the
voice lights the whole ring, brighter and longer. In an instrumental passage
the door rejoins the band, so it never sits idle. Drops and the finale still
take the whole castle. Both new cue files pass `simulate_show.mjs` (18,973
frames, every pixel finite).

## The show lab

<http://127.0.0.1:8894/show-lab.html> — rebuilt by
`.venv/bin/python demo/castle-radio/show_lab.py`, served by the same static
server as before (comparison directory only, no device bridge).

Both sides are the Radio's own renderer and `cue-playback.js` stepping
**live at 16 ms for the whole song** — no pre-sampled 64 ms frames, no
20-second passages. Each side shows the castle scene and every real pixel
(left tower, door, right tower) on one clock. The timeline names each
phrase's look and pattern and marks drops with ⚡; click it to seek.
**Next drop ⚡** jumps three seconds before one and plays. Speed ½× and ¼×
are there for judging short hits. Options 1–4 remain selectable.

**Sound is optional** (added later the same evening, at Justin's request).
The page is silent until **Play the song** is ticked. Then it fetches the
song from `audio/` — a symlink `show_lab.py` makes in the comparison
directory, so the library is read and never written — and plays it in that
browser only; nothing reaches the castle. With sound on, the song's own
playhead is the clock both sides follow (as in the Radio page), seeking and
**Next drop ⚡** move the song too, and ½× / ¼× slow the song with the lights.
Open the page over http, not from disk, or the song cannot load.

## Evidence, and what it does not show

- All six new cue files: every cue executes, every pixel finite and in
  range (`simulate_show.mjs`, 113,838 frames), and the real C++ firmware
  reader trace matches the decoded records within 0.0001
  (`show-lab-firmware-validation.json`).
- Against both the baseline and option 4, the new candidates show about
  twice the brightness swing per fixture (p95−p5 0.65–0.75 vs 0.34–0.43 on
  the left tower) and 2–3× the mean left/right difference (0.12–0.21 vs
  0.05–0.08). That says they are *different*, not that they are *good* —
  that judgement is Justin's, by eye.
- `test_choreography.py`: 14 tests on a synthetic 120 BPM song — tempo,
  bar phase, phrase ranks, tower alternation, sweep direction (which caught
  a real bug: the reverse sweep began at the door), roll-dark-slam order,
  the slam surviving its downbeat, ornament refusal, determinism, and the
  lab never writing beside a baseline.
- Limits: 4/4 and 4-bar phrases are assumed; "loud" is relative to the
  song, so a flat-mastered song still gets all three ranks; bar phase is a
  guess from the low band and can sit a beat off; a song without stems has
  no path into `choreograph` yet. None of this is wired into
  `rich_show.prepare` — adopting one is step 4 of the handoff, unstarted.
