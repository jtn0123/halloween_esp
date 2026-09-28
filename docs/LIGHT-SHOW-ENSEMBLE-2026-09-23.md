# Option 12 · Ensemble — per-drum lights, colour from harmony, kinds of voice, bookends

September 23, 2026. Continues
[sections, spin and the blind test](LIGHT-SHOW-SECTIONS-2026-09-23.md).
Software only: no device contact, no song played anywhere but a browser, no
candidate promoted into `rich_show.prepare`. Written for firmware v5.71+
(cue format v2 with arcs), which the castle has run since 2026-09-23.

Option 12 is option 11 (Spin) with four things added. All four are read
from the stems the splitter already makes, and none of them needs anything
new on the castle. It is the lab's default candidate now.

## What it adds

**Per-drum lights** (`drum_kit.py`). The drum stem's three onset bands
become three instruments:

- The kick thumps the towers' bottom halves in the look's first colour.
- The snare cracks an arc of both towers where the tempo-locked chase is,
  in the answering colour.
- A kick and snare together (within 30 ms) light the whole tower.
- Hi-hats are faint sparkles on the ornament layer, alternating left and
  right, at most one every 160 ms.

The bar's "one" is still the unison hit. The kit only takes over where it is
really playing (a kick or snare a bar), and never inside a transition, a
stop or a drop. A song whose drum stem is under a fifth of the band's level
has no kit and keeps option 11's pattern.

**Colour from harmony** (`harmony.py`). The chord of each bar is read from
the bass and "other" stems: a chroma matched against the 24 triads, with
the bass weighted for the root. That chord is judged against the song's
key, found with the Krumhansl-Kessler profiles. A key and its relative key
share every note, so the tie is broken by dominant→tonic resolutions, then
by which chord opens more phrases. A bar's mood then tints the band's hits:

| Mood | Chord | Light |
| --- | --- | --- |
| home | the key's own | the look's colours |
| away | another major chord of the key | the two colours swapped |
| shadow | another minor chord of the key | blended toward deep blue; chase palette "moonlight" |
| strange | outside the key | blended toward sickly green; chase palette "toxic" |

A single passing bar between two bars of one mood takes their mood.

**Kinds of voice** (`voice_kinds.py`). These are read from the vocal stem
and its pitch track:

- **Spoken lines** are judged four seconds at a time. Speech glides between
  pitches, where singing holds each note on the song's semitone grid. For
  a spoken line the door goes to "eyes" and flickers pale with the words,
  instead of walking a melody.
- **A choir** makes the stem rough (YIN's best match is worse), measured
  against the song's own median. A stereo spread lowers the bar. It adds a
  ring swell to the towers, and the hi-hats keep out of it.
- **Vibrato** on a held note (4–8 Hz, at least a third of a semitone)
  shimmers with a sparkle overlay instead of spinning.
- **Tone** is the spectral centroid at the note's start, against the median.
  A bright, belted note burns whiter; a dark one burns deeper and softer.

**Bookends** (`bookends.py`).

- **Candle intro.** When the band takes more than 1.5 s to come in, the
  castle starts as three candles that brighten until the band's entrance
  bar. The entrance is where the envelope holds half the song's typical
  (75th percentile) level for 1.5 s, and the candles stop at 45 s at most.
- **Finale.** It follows how the band stops:
  - **cold** (still loud at the stop): a white slam, a burst round the door,
    then black on the next beat;
  - **fade**: the castle dims with the band, bar by bar, with the chase
    slowing, down to one candle in the door;
  - **ring**: one long hit that dies with the chord, then embers.

## What it heard

Every analysis is cached in the lab's own directory
(`<key>.voice.json`, `<key>.harmony.json`), never beside the library.

| Song | Cues | Moods home/away/shadow/strange | Spoken | Choir | Vibrato | Intro | Ending |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Thriller | 1579 | 73/4/6/9 | 14 | 14 | 1 | – | cold |
| Halloween Theme | 1348 | 26/25/6/43 | 0 | 0 | 0 | – | ring |
| Day-O | 892 | 30/5/1/19 | 10 | 46 | 6 | yes | cold |
| I Put a Spell on You | 1240 | 25/3/15/28 | 50 | 53 | 3 | yes | cold |
| Oogie Boogie | 1310 | 16/4/27/34 | 228 | 55 | 1 | – | cold |
| Monster Mash | 2115 | 31/29/43/3 | 178 | 51 | 2 | – | fade |

Every song has a kit.

**Keys checked by ear against the chart:**

| Song | Key read | Verdict |
| --- | --- | --- |
| Halloween Theme | F♯ minor | right |
| Thriller | C♯ minor | right |
| Monster Mash | G | right (G G Em Em C C D D) |
| Day-O | F | right |
| Oogie Boogie | D♯/E♭ | not confirmed |

Monster Mash and Day-O read as their relative minors until the resolution
vote was added.

**Calibration of the voice measures:**

- *Spoken vs sung.* Thriller's dialogue holds a pitch on 0.58–0.69 of its
  voiced frames and sits 0.29–0.46 of a semitone off the grid. Its singing
  holds 0.83–0.98 and sits 0.08–0.21 off.
- *Choir roughness.* Day-O's solo lines read 0.02–0.10, the answering chorus
  0.24–0.41, and the final chorus 0.55.

**Known soft spots.**

- The Halloween Theme's 43 strange bars are its chromatic line, read
  honestly. The light may still be too green; that is a question for the
  blind test.
- Oogie Boogie's 228 spoken notes are mostly its growled half-spoken verses.
- Nothing here has been judged on the porch yet. Tap 🚩 in the lab to flag
  a moment (`show_lab.py --notes`).

## Tests

These run on synthetic songs, chords and tones, with no library track,
audio or device:

- `test_harmony.py`: chroma, triads, mood rules, the relative-key vote,
  bar smoothing and the cache.
- `test_voice_kinds.py`: glide vs grid, the half-held line, vibrato rate and
  depth, choir, tone, and measurement on a synthetic tone and noise.
- `test_drum_kit.py`: kit gating, band thresholds, free windows, one light
  per drum, and what the kit replaces.
- `test_bookends.py`: the entrance, the candles, and cold/fade/ring detection
  with each finale's shape.
- `test_ensemble_show.py`: the whole run through the real card encoder.

The whole package is covered at 88% (`make coverage-radio`, floor 67).

## Option 13 · Colour (September 24)

The feedback on option 12 was that it was sometimes "bland on the same
colours" — but it should not get crazy. The measurement behind it:

- **One family per song.** Day-O was 66% red and orange by hit brightness,
  Spell 65% green, and Monster Mash 49% violet and blue.
- **Answers the LEDs cannot tell apart.** Four looks answered a warm colour
  with a warm neighbour: Furnace orange/red, Heartbeat magenta/red,
  Watchers amber/orange. Blood moon answered red with white.
- **White washed out the colour.** It was 34% of Thriller's hit light and
  28% of the Halloween Theme's.

`colour_show.py` keeps option 12's sections, drums, harmony, voices and
bookends, and changes only colour:

1. **Answers from across the wheel.** Every look's two colours are at
   least 80° of hue apart:
   - Furnace: orange / deep blue
   - Blood moon: red / ice
   - Watchers: amber / green
   - Heartbeat: magenta / teal

   Each look also gets an accent colour from a family neither of its two
   colours is in. The other options keep their looks: the retuned table is
   applied through `draft(restyle=…)`.
2. **White kept for the big moments:** drops and stops (with two beats
   either side), the finale, and the first hit of every chorus. The chorus
   is the loudest kind of passage *that comes back*. An instrumental's
   one-off loud opening is not one. Every other white hit takes the look's
   first colour.
3. **The race hands the colour over.** Each zone the race passes keeps a
   glow of the new section's colour (ornament layer) until two beats into
   the section, and its chase changes palette as the light goes by.
   Hi-hat sparkles stay out of a glow, which they would otherwise cut.
4. **Returning sections vary.** A section's second and later visits lead
   with the answering colour, and the chorus's last visit answers in the
   accent.
5. **Pitch shading.** Each sung arc is paler and cooler the higher it sits
   in the song's own range, and deeper and a little dimmer the lower.
6. **A cap per family.** Past 45% of a song's coloured light, a family's
   hits in alternate phrases take their look's accent.

What it measured, as share of the hit light:

| Song | White | Warm | Magenta | Blue | Green |
| --- | --- | --- | --- | --- | --- |
| Thriller | 3.2% | 26% | 20% | 37% | 17% |
| Halloween Theme | 0.7% | 21% | 7% | 29% | 43% |
| Day-O | 3.7% | 40% | 25% | 15% | 19% |
| I Put a Spell on You | 2.4% | 27% | 11% | 17% | 45% |
| Oogie Boogie | 2.3% | 19% | 6% | 33% | 42% |
| Monster Mash | 1.2% | 24% | 3% | 44% | 29% |

- Option 12's files are byte-identical after the refactor.
- `test_colour_show.py` covers the looks, the chorus rule, the white budget,
  the handoff, the shading and the cap.
- It is the lab's default now; the page still offers 12 beside it.

## Option 14 · Palette (September 24)

The verdict on 13: the effects are in the right places, but the colours were
still lacking. Measuring what the eye sees, every frame through the page's own
renderer, showed why. A hit is gone in a fraction of a second, so the eye mostly
holds the base effect under it, and:

- Eight of the nine looks gave the door a fixed red or amber effect (blood,
  ember, eyes, candle), so the door was red 42–70% of every song.
- Blood moon's towers sat on "blood", the dimmest red there is, and it held
  the Halloween Theme for 91 s and Thriller for 89 s.
- Every tower's centre pixel was an ember, whatever the look.

`palette_show.py` keeps every hit of option 13, the same cues in the same
places, and changes only the resting light:

1. **Palette effects.** Every fixed red or amber base effect becomes one the
   palette colours (seance, mansion, wisp or throb), at levels that keep the
   hits on top. Spoken lines still turn the door to eyes, and the candle
   intros and finales are untouched.
2. **The door's own palette.** Each look has two door palettes that stand
   apart from its towers' own. Violet and blue count as the same, because
   the LEDs barely tell them apart. Each section takes whichever of its two
   the song has worn least so far. Every chase and shimmer on the door wears
   it too.
3. **Tower centres.** The centre pixels follow the tower's effect, and
   Heartbeat's towers throb violet: three of the four loud looks were red
   towers.
4. **Long sections turn.** In a section's second and fourth phrase the
   towers wear a second palette (Blood moon's go violet, Graveyard's blue).
   A chord's mood still takes over bar by bar.

Share of frames each zone spends in a colour (the top two families):

| Song | Door, option 13 | Door, option 14 | Left tower, option 14 |
| --- | --- | --- | --- |
| Thriller | red 42, green 24 | green 19, blue 18 | blue 40, red 19 |
| Halloween Theme | red 70, orange 8 | blue 37, violet 34 | red 24, orange 21 |
| Day-O | red 60, magenta 28 | magenta 33, red 31 | red 33, orange 30 |
| I Put a Spell on You | red 42, green 31 | red 25, green 23 | green 23, orange 17 |
| Oogie Boogie | red 50, green 31 | red 33, green 22 | green 22, blue 22 |
| Monster Mash | red 57, yellow 14 | red 37, blue 19 | green 24, violet 17 |

The reds left on Oogie Boogie's and Monster Mash's doors are their spoken
lines (the eyes). Day-O's warm towers are its candle intro and its Furnace
sections, both meant to be warm. `test_palette_show.py` covers the table,
the unchanged hits, the door's balance, the phrase turns and the moods.

## Option 15 · Spectrum (September 25)

The next verdict: "a few colors is lame". A strike carries its own 8-bit
RGBW colour, so the card can show any of about 16 million colours, but
options 12–14 only asked for the ten or so their nine looks name.
`spectrum_show.py` keeps option 14's structure and draws its colours from
the whole wheel:

- **One hue per kind of passage.** Each kind starts from a hue taken from
  the song's id, plus the golden angle (137.5°) for each kind, so neighbours
  never sit together. A returning passage turns 14° each time.
- **Quiet vs loud.** Quiet passages use neighbouring hues (40° apart). Loud
  ones use the opposite side of the wheel, split by 25°.
- **The singer** gets a third, paler hue.
- **Every coloured hit** turns up to 9° by its own moment.
- **The resting glow** can only be one of the firmware's four palettes
  (`castle_effects.h` `PALETTES`), so each section takes the nearest one.

Distinct hit colours, and how many of the wheel's twelve 30° wedges hold at
least 3% of the rendered light:

| Song | Original show | Option 14 | Option 15 |
| --- | --- | --- | --- |
| Thriller | 462 · 7 | 124 · 9 | 537 · 11 |
| Halloween Theme | 435 · 7 | 36 · 11 | 524 · 11 |
| I Put a Spell on You | 376 · 7 | 84 · 10 | 535 · 11 |
| Oogie Boogie | 388 · 6 | 125 · 9 | 528 · 10 |

No wedge holds more than 22% of any song's light in option 15.
`test_spectrum_show.py` covers the themes, the golden-angle spread, the
jitter, the nearest palette and the section glow.

The resting glow is now the limit. Letting it take any colour would need a
look record that carries two RGB colours in place of a palette number. That
is cue format v3 and a firmware change, held to parity in every copy
(docs/PARITY.md).

## Option 16 · Spectrum, any-colour glow (September 26, preview only)

The user asked to see that glow in the lab before anyone touches firmware.
Option 16 is option 15's card with every palette record removed. In their
place are `preview` records: `{op: "look", targets, glow: [[r,g,b],[r,g,b]]}`,
which only the lab draws (`spectrum_glow.py`; `show_lab._write` merges them
after the card is decoded). The towers glow in the section's first colour and
drift to its answer. The door does the reverse. A chord's mood leans the
towers halfway toward shadow blue or strange green, and the race hands each
tower the next section's glow. The page registers each pair with
`effects.ts` `previewPalette`, as an index past the four real palettes, which
are the parity contract and are not changed. No card and no castle can play
this. Wedges held (rendered frames): Thriller 9 (max 30%), Halloween Theme 10
(17%), Spell 9 (19%), Oogie Boogie 8 (23%). `test_spectrum_glow.py` covers it.

## A more realistic stage (September 26)

`web/src/stage.ts` now paints light the way it falls. `stage_light.ts` holds
the light model.

- **Additive light.** Every glow is drawn in its full-strength hue at an
  alpha of its level, with the "lighter" operator.
- **LEDs.** Each pixel is a coloured halo plus a small core that whitens
  with the square of its level. A resting glow keeps its hue, and a hit reads
  as a hot die.
- **The room behind each opening** is lit brightest around the jewel. The
  arch's inner edge and the sill catch a rim of the light.
- **The wash on the wall** is clipped to the castle, so the sky beside a tower
  stays dark. It falls off steeply and leans downward.
- **The door** throws a flattened pool onto the ground and tints the fog near it.
- **Stonework** is a multiply mask of courses and weathering, so the wall's
  texture appears only where light reaches it.
- **Bloom** grows with the square of the level: a resting glow barely blooms,
  and a hit flares past its own arch.

The desk and Castle Radio share the same Stage, so they get the same picture
once this worktree lands.

A hidden stage measures 0 wide, and the first stonework mask divided by that
scale and looped forever: the desk's "Pixels" view and the lab's one-castle
view froze the page. The browser suite caught it (`desk.spec.ts`, the stage
view toggle); `stoneMask` now returns a blank mask for a scale that is not a
positive number, and `drawStone` skips a hidden stage.

## The lab's page (September 26)

- Both castles and their LED strips clear the pinned timeline on a
  1440×900 window: the pair narrows with the window's height and stops at
  1000px wide. The strips are drawn at the size they are shown, so they are
  sharp on a Retina screen.
- The timeline writes a section's whole name or only its letter, never a
  cut word, and hovering shows the full name and its times. A phone's thin
  strip gets the letters.
- The picker groups 15 and 16 as "Recommended" above the earlier ideas.
  `[` and `]` step through the options, and `X` swaps back to the last one.
- The firmware choice names the castle's v5.73. v5.71's softening rule is
  still what it runs.
- `lab_server.py` answers `GET /notes.jsonl` with an empty list before the
  first flag, rather than a 404 on every page load.
- A phone with room to spare shows the LED rings under both castles instead
  of empty space.

## The LEDs up close (September 27, preview only)

`lab-leds.js` holds the maths and `show-lab-leds.js` draws it; both are lab
only. `test_lab_leds.test.mjs` is in `make test-radio`.

- **Boards.** The strip under each castle draws a Jewel 7 disc per tower and
  the Ring 12 on the door, with 5050 packages. A lit die keeps its hue and
  whitens only slightly at full drive.
- **Readout.** Under each board: the light, its brightness, its base effect,
  and an orange dot that flashes with each hit. The supply draw is in the
  corner.
- **Barcodes.** Under the timeline, one row per show and one lane per light:
  every colour it holds over the whole song. Each bucket shows its hue at no
  less than 35% brightness, so a dim glow still says which colour it is.
  Hovering gives the show, the light, the time and its real colour and
  level. Each show is played headless once, in slices between frames, and
  cached per show, firmware and Soften setting.
- **Hues and peak.** Each card's line adds how many of twelve 30° hue
  families the show holds for two seconds or more, and its peak draw.
  Thriller: today's show 7 hues and ≈0.3 A; option 16 has 12 hues and ≈1.2 A.
- **Real LEDs.** ESPHome gamma-corrects each write at 2.8 and a screen shows
  a value at about 2.2, so the toggle draws v^(2.8/2.2): a 30% resting glow
  emits about 22% and a full hit is unchanged.
- **Current estimate.** 20 mA a die through the gamma plus 1 mA a pixel idle.
  It reads a little high on a jewel's warm white, because the screen colour
  folds its white die into RGB. The LEDs' share of the supply is taken as
  1.4 A (a 3 A supply less the amplifiers' 1.6 A), and above it the figure
  turns orange.
- **Differences** rings each pixel where the two shows differ by more than
  0.2 on any channel.
- **Follow one pixel.** Click a pixel and both barcodes follow it alone.
  Click it again, or press Escape, to go back.
