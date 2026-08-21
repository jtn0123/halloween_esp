#!/usr/bin/env python3
"""Render each scene in scenes.yaml to a single pre-mixed audio file.

The MAX98357A plays one stream, and mixing on a single-core ESP32-S2 is not
worth fighting. So every scene becomes one file: all the layering, ducking and
reverb happens here, where CPU is free.

    tools/render_audio.py            # render everything to audio/
    tools/render_audio.py --wav      # keep the intermediate WAVs too
    tools/render_audio.py --only storm

Output is mono at the bitrate set in scenes.yaml, sized to fit the flash left
over after the ESPHome image.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
import zlib
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).parent))
import analyze
import build_paths as bp
import synth

ROOT = Path(__file__).resolve().parent.parent
# Both redirectable (build_paths.py): a sandboxed studio renders beside its
# own scenes file, never into the repo's audio/.
SCENES = bp.SCENES
OUT = bp.AUDIO

TARGET_PEAK = 0.89   # leaves headroom for the MP3 encoder's overshoot


def write_wav(path: Path, x: np.ndarray, sr: int) -> None:
    pcm = (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def render_scene(scene: dict, cfg: dict) -> tuple[np.ndarray, dict[str, list]]:
    """Mix one scene's score into a single buffer.

    Also returns beat markers, keyed by synth name: the ACTUAL musical event
    times AND loudness reported by the synths — both heartbeat thumps, each
    whispered word, organ chord onsets, waltz downbeats. Each marker is
    [ms, velocity]. The cue generators turn these into light pulses, so light
    stays locked to sound even when a synth jitters its own timing, and a
    scene can give each sound its own colour and decay.
    """
    sr = cfg["sample_rate"]
    dur = scene["duration_ms"] / 1000.0
    buf = np.zeros(int(dur * sr))
    markers: dict[str, list] = {}

    # Deterministic per scene, so re-rendering is reproducible and a diff in
    # the output means a real change in the score. NOT Python's hash() — that
    # is salted per process, which would re-randomise every render.
    rng = np.random.default_rng(zlib.crc32(scene["id"].encode()))

    # A scene can be built on an imported file instead of (or as well as) the
    # synths. Nobody tells us where an outside track's beats are, so we detect
    # them — and the detected onsets are reported under the same marker names
    # the synths use, so `pulse:` streams work identically either way.
    track = scene.get("audio_file")
    if track:
        path = bp.track_source(track)
        if not path.exists():
            raise SystemExit(f"scene {scene['id']}: no such audio_file {track}")
        x = analyze.load_audio(path, sr)
        gain = float(scene.get("track_gain", 1.0))
        synth._place(buf, x * gain, float(scene.get("track_at", 0.0)))
        for band, hits in analyze.analyze_full(
                # A scalar, or a per-band map ({low: 0.8, mid: 1.1, high: 1.6})
                # as the clip editor writes it. Coercing to float here would
                # have thrown on the map and, worse, silently ignored it if it
                # had not — the render must detect the same onsets the editor
                # showed, or the tuning was for nothing.
                x, sr, sensitivity=scene.get("sensitivity", 1.1),
                # Onsets gain a pan third element; markers grow with them,
                # and the pulse expansions route decisive pans by tower.
                stereo=analyze.load_stereo(path, sr)).items():
            markers.setdefault(band, []).extend(
                [int((h[0] + scene.get("track_at", 0.0)) * 1000), *h[1:]]
                for h in hits if h[0] < dur - 0.1)

    for ev in scene.get("score") or []:
        name = ev["synth"]
        fn = synth.SYNTHS.get(name)
        if fn is None:
            raise SystemExit(f"scene {scene['id']}: unknown synth {name!r}")
        res = fn(rng, dur=ev["dur"]) if "dur" in ev else fn(rng)
        sig, raw_marks = res if isinstance(res, tuple) else (res, [])
        # A synth may report bare times or (time, velocity) pairs.
        marks: list[tuple[float, float]] = [
            m if isinstance(m, tuple) else (m, 1.0) for m in raw_marks]
        if "take" in ev:                      # trim a long piece to fit
            sig = sig[: int(ev["take"] * sr)]
            fade = min(len(sig), int(0.4 * sr))
            if fade:
                sig[-fade:] *= np.linspace(1.0, 0.0, fade)
            marks = [(m, v) for m, v in marks if m < ev["take"]]
        synth._place(buf, sig * float(ev.get("gain", 1.0)), ev["t"])
        markers.setdefault(name, []).extend(
            [int((ev["t"] + m) * 1000), round(v, 3)] for m, v in marks
            if ev["t"] + m < dur - 0.1)

    # Imported tracks arrive already produced — adding the stone hall on top
    # of someone else's reverb just makes mud. Scenes can override either way.
    wet = float(scene.get("reverb", 0.0 if track else 0.42))
    buf = synth.apply_reverb(buf, wet=wet, rng=rng)

    if scene.get("loop"):
        # Crossfade the tail into the head so the loop point is inaudible.
        xf = min(int(0.6 * sr), len(buf) // 4)
        if xf > 0:
            head = buf[:xf].copy()
            ramp = np.linspace(0.0, 1.0, xf)
            buf[-xf:] = buf[-xf:] * (1.0 - ramp) + head * ramp
    else:
        fade = min(int(0.25 * sr), len(buf))
        buf[-fade:] *= np.linspace(1.0, 0.0, fade)

    buf = synth.limit(buf)

    # Normalise every scene to the same peak. Files should use the full 16-bit
    # range — quiet material stored quietly just sits closer to the DAC noise
    # floor for no benefit. Relative loudness between scenes is a playback
    # concern, set per scene by `volume` in scenes.yaml.
    peak = float(np.max(np.abs(buf)))
    if peak > 1e-6:
        buf *= TARGET_PEAK / peak
    return buf, {k: sorted(v) for k, v in markers.items()}


def encode_mp3(wav: Path, mp3: Path, bitrate: int) -> None:
    subprocess.run(
        ["lame", "--quiet", "-m", "m", "-b", str(bitrate), "--resample", "44.1",
         str(wav), str(mp3)],
        check=True,
    )


def render_chirp(cfg: dict) -> None:
    """audio/00_chirp.mp3 — the firmware's no-SD-card fallback beep.

    castle_sd.yaml embeds it (a card can be absent; silence would read as
    a dead speaker), but until now it only existed as a hand-made file on
    one machine — gitignored, so a fresh clone or CI could not validate
    the SD build at all. Synthesised here like everything else in audio/:
    two rising sine blips, deterministic, ~0.4 s.
    """
    sr = cfg["sample_rate"]
    t = np.arange(int(0.4 * sr)) / sr
    x = np.zeros_like(t)
    for at, f in ((0.02, 880.0), (0.20, 1320.0)):
        seg = (t >= at) & (t < at + 0.12)
        ts = t[seg] - at
        x[seg] += np.sin(2 * np.pi * f * ts) * np.exp(-ts * 28) * 0.7
    wav = OUT / "00_chirp.wav"
    write_wav(wav, x * TARGET_PEAK, sr)
    encode_mp3(wav, OUT / "00_chirp.mp3", cfg["bitrate"])
    wav.unlink()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="render just this scene id")
    ap.add_argument("--wav", action="store_true", help="keep intermediate WAVs")
    args = ap.parse_args()

    doc = yaml.safe_load(SCENES.read_text())
    cfg = doc["hardware"]["audio"]
    OUT.mkdir(parents=True, exist_ok=True)
    if not args.only:
        render_chirp(cfg)

    scenes = doc["scenes"]
    if args.only:
        scenes = [s for s in scenes if s["id"] == args.only]
        if not scenes:
            raise SystemExit(f"no scene with id {args.only!r}")

    total = 0
    all_markers: dict[str, dict[str, list]] = {}
    print(f"{'scene':<12} {'length':>8} {'mp3':>9}   file")
    print("-" * 52)
    for i, scene in enumerate(doc["scenes"], start=1):
        if args.only and scene["id"] != args.only:
            continue
        buf, markers = render_scene(scene, cfg)
        if markers:
            all_markers[scene["id"]] = markers
        stem = f"{i:02d}_{scene['id']}"
        wav = OUT / f"{stem}.wav"
        mp3 = OUT / f"{stem}.mp3"
        write_wav(wav, buf, cfg["sample_rate"])
        encode_mp3(wav, mp3, cfg["bitrate"])
        if not args.wav:
            wav.unlink()
        size = mp3.stat().st_size
        total += size
        print(f"{scene['id']:<12} {len(buf)/cfg['sample_rate']:>7.1f}s "
              f"{size/1024:>8.0f}K   {mp3.name}")

    print("-" * 52)
    print(f"{'total':<12} {'':>8} {total/1024:>8.0f}K")
    if not args.only:  # partial renders must not clobber other scenes' markers
        (OUT / "markers.json").write_text(json.dumps(all_markers, indent=0))
        n = sum(len(m) for v in all_markers.values() for m in v.values())
        print(f"beat markers: {n} across {len(all_markers)} scenes -> audio/markers.json")
    # 3.87 MB single-app partition minus ~0.97 MB of firmware (measured,
    # PROJECT_NOTES §12.2).
    budget = 2.9 * 1024 * 1024
    pct = total / budget * 100
    verdict = "fits" if total < budget else "OVER BUDGET"
    print(f"\nAudio budget ~2.9 MB (single-app partition): {pct:.0f}% used — {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
