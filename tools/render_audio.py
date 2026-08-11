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
import analyze  # noqa: E402
import synth  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCENES = ROOT / "scenes" / "scenes.yaml"
OUT = ROOT / "audio"

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
        path = ROOT / track
        if not path.exists():
            raise SystemExit(f"scene {scene['id']}: no such audio_file {track}")
        x = analyze.load_audio(path, sr)
        gain = float(scene.get("track_gain", 1.0))
        synth._place(buf, x * gain, float(scene.get("track_at", 0.0)))
        for band, hits in analyze.analyze(
                x, sr, sensitivity=float(scene.get("sensitivity", 1.1))).items():
            markers.setdefault(band, []).extend(
                [int((t + scene.get("track_at", 0.0)) * 1000), v]
                for t, v in hits if t < dur - 0.1)

    for ev in scene.get("score") or []:
        name = ev["synth"]
        fn = synth.SYNTHS.get(name)
        if fn is None:
            raise SystemExit(f"scene {scene['id']}: unknown synth {name!r}")
        res = fn(rng, dur=ev["dur"]) if "dur" in ev else fn(rng)
        sig, marks = res if isinstance(res, tuple) else (res, [])
        # A synth may report bare times or (time, velocity) pairs.
        marks = [m if isinstance(m, tuple) else (m, 1.0) for m in marks]
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="render just this scene id")
    ap.add_argument("--wav", action="store_true", help="keep intermediate WAVs")
    args = ap.parse_args()

    doc = yaml.safe_load(SCENES.read_text())
    cfg = doc["hardware"]["audio"]
    OUT.mkdir(exist_ok=True)

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
