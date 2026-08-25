# Open issue — a scene's audio starts rough

**Status:** open, narrowed by ear to the *scene start*, not the file path and
not the lights. **Opened:** 2026-08-22 · **Firmware:** v5.40 · **Board:** the
new porch Feather (10.27.27.247), CanaKit 5 A, shrouded 4 Ω speakers.

## The symptom, in the operator's words

> weird slow audio when I play some of the scenes … when the song starts it
> is bad — can we add a buffer or something so it doesn't slam it?

Test tones and a plainly played song are clean (HARDWARE_FINDINGS §8 — the
5 V rail was the earlier, separate fault). A **scene** start is not.

## What was compared (each a 15–30 s listen, same song, same board)

| Test | What ran | Heard |
|---|---|---|
| A | `/api/scene?s=the_citizens…` — full scene start | "weird buffered start", every time |
| B | the song restarted with `/api/play` while the scene's lights kept animating | much better; "a hair" of static at the start |
| C | the song with `/api/play`, lights parked off | same as B |
| D | the scene's own rendered file (`audio/card/09_…`, pushed to the root as `scene09_copy.mp3`) with `/api/play`, lights off | **not yet reported** — run this first; it decides content vs firmware |

So the lights are not it (B ≈ C), and IDLE→PLAYING is not it (C starts from
idle too). What A does that B does not: `run_scene` → the scene script's
opening lambda, its `set_volume`, `sfx` → the **cue timeline** (Citizens:
200 pulse hits + cues as chunked scripts). And A plays a *different file* —
the rendered scene track, not the imported song. D separates those two.

## Evidence from the firmware

- Logs at INFO are silent through every start: no "took a long time", no
  decoder complaints, no eInk refresh (rate-limited) — underruns are not
  logged at this level (`i2s_audio_speaker_standard.cpp` pads silence quietly).
- ESPHome's read/decode tasks run at **priority 1 — the main loop's** —
  (`speaker_media_player.cpp` `ANNOUNCEMENT_PIPELINE_TASK_PRIORITY`); the
  speaker task is 19. A busy main loop (16 ms pixel render, cue scripts)
  time-slices against the decoder exactly when it must get ahead.
- `audio_pipeline.cpp` starts the decoder after `INITIAL_BUFFER_MS = 1000`
  of *file*; the speaker drains as soon as PCM exists — no start cushion.
- v5.40 moved the pipeline task stacks from PSRAM to internal RAM
  (`castle_audio.yaml`): **no audible change**, heap while playing 62 → 34 KB.
  Revert or keep knowingly.

## Next, in order

1. **Hear D.** If D is rough → the rendered file (check `tools/render_audio.py`
   TARGET_PEAK / the score's first second / LAME settings). If D is clean →
   the scene start itself.
2. If it is the scene start: strip the cue timeline — a scene with the song
   and no cues (copy Citizens in `scenes.yaml`, empty `cues`/`pulse`) — and
   listen. Then a scene with cues but `sfx` delayed 1 s. One variable at a time.
3. The lever YAML cannot reach: the pipeline tasks' priority. That means an
   `external_components` override of ESPHome's `speaker/media_player` with
   one constant changed (priority 1 → 3), and the LOC cap to think about.
4. Revert `task_stack_in_psram` to `true` if nothing above needs the speed.

The pixels were parked off by the test script (`/api/light?c=off`); hand
them back with `/api/light?c=show`. `scene09_copy.mp3` stays on the card
root for test D; delete it after.
