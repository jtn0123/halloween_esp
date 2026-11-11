# Stereo bench fixture — future v3.4 boards

`channel-identification.mp3` is a two-channel 44.1 kHz MP3. Regeneration also writes a WAV reference. It is nine seconds long and contains:

| Time | Expected revised v3.4 output |
|---|---|
| 0–2 seconds | LEFT only: 440 Hz tone |
| 2–3 seconds | Silence |
| 3–5 seconds | RIGHT only: 660 Hz tone |
| 5–6 seconds | Silence |
| 6–8 seconds | Same 440 Hz tone from both speakers |
| 8–9 seconds | Silence |

The file was encoded, decoded and checked for separated channel energy on the computer; see `fixture-check.json`. **It has not been played on an assembled PCB.** The tone amplitude is about -14 dBFS; start with low playback volume.

For eventual bench bring-up, use the correctly assembled new v3.4 board and its matching stereo overlay. Copy the MP3 to the microSD card at `tests/channel-identification.mp3`, and play it through the existing SD-file playback path (`play_sd` with that relative path; internally an announcement from `http://127.0.0.1:8080/sd/tests/channel-identification.mp3`). The WAV is for reference, not the MP3-configured announcement pipeline. Confirm left/right as above, then play a known mono MP3 and confirm equal output from both speakers. Record the assembled revision, fitted selectors, firmware hashes, speaker connections, rail voltages, observed channel separation and load/temperature behavior before accepting the prototype.

This is not a modification procedure for v3.3a. Its unchanged dual-mono hardware cannot satisfy a left-only/right-only stereo test. The printed board keeps its original configuration.

Recreate only this fixture using `python3 stereo-bench/generate.py` from the Integrated handoff directory (requires ffmpeg). No device is flashed and no card is changed by generation.
