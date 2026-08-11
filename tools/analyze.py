#!/usr/bin/env python3
"""Turn an arbitrary audio file into light cues.

The synths in synth.py report their own event times, so scenes built from
them get light that is locked to the sound by construction. A track you drop
in from outside has no such luxury — nobody tells us where its beats are. So
we listen for them.

The method is spectral flux onset detection, run separately in three bands:

    low   (< 200 Hz)     kick drums, heartbeats, organ pedal   -> the door
    mid   (200-2000 Hz)  voices, piano, most melodic material   -> the towers
    high  (> 2000 Hz)    cymbals, sibilance, bells, sparkle     -> accents

Splitting by band matters more than it might sound. A single onset track
gives every zone the same pulse and the castle blinks as one lamp; banded
onsets give the bass its own zone and let the towers answer the melody,
which is the difference between "the lights are flashing" and "the castle is
listening to the music".

Markers come back in exactly the shape synth.py produces — {name: [(t, vel)]}
— so custom tracks feed the same `pulse:` streams as everything else, and no
other part of the pipeline needs to know the difference.

    tools/analyze.py tracks/whatever.mp3          # print what it found
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy import signal

SR = 44100
HOP = 512                 # ~11.6 ms between analysis frames
WIN = 2048

# (name, low_hz, high_hz, min_gap_s) — min_gap is a refractory period, the
# shortest believable spacing between two separate hits in that band. Bass
# notes are further apart than hi-hats, and without this a single kick
# smeared across a few frames reads as a burst of four.
BANDS = [
    ("onset_low",  20,    200,   0.16),
    ("onset_mid",  200,   2000,  0.11),
    ("onset_high", 2000,  16000, 0.09),
]


def load_audio(path: Path, sr: int = SR) -> np.ndarray:
    """Decode anything ffmpeg understands to mono float32."""
    out = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-i", str(path),
         "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(out, dtype="<f4").astype(np.float64)


def _flux(mag: np.ndarray) -> np.ndarray:
    """Positive spectral flux: how much energy APPEARED since last frame.

    Only rises count. Energy dying away is a note ending, which is not a
    thing to flash a light at.
    """
    d = np.diff(mag, axis=1, prepend=mag[:, :1])
    return np.maximum(d, 0.0).sum(axis=0)


def _pick_peaks(env: np.ndarray, times: np.ndarray, min_gap: float,
                sensitivity: float) -> list[tuple[float, float]]:
    """Adaptive-threshold peak picking.

    A fixed threshold fails on any real music: a quiet intro and a loud
    chorus need different bars. The threshold here is a running median plus a
    margin, so it tracks the material.
    """
    if env.max() <= 0:
        return []
    env = env / env.max()

    # Running median over ~0.5 s, as the local "normal" level.
    k = max(3, int(0.5 / (HOP / SR)) | 1)
    local = signal.medfilt(env, kernel_size=k)
    thresh = local + sensitivity * (env.std() + 1e-9)

    hits = []
    last = -1e9
    for i in range(1, len(env) - 1):
        if env[i] < thresh[i]:
            continue
        if not (env[i] >= env[i - 1] and env[i] >= env[i + 1]):
            continue                       # local maximum only
        t = float(times[i])
        if t - last < min_gap:
            # Too close to the previous hit: keep whichever is stronger.
            if hits and env[i] > hits[-1][1]:
                hits[-1] = (t, float(env[i]))
                last = t
            continue
        hits.append((max(0.0, t), float(env[i])))
        last = t

    if not hits:
        return []
    # Rescale velocities so the loudest hit in the band is 1.0 — the pulse
    # config sets absolute intensity, this only carries relative dynamics.
    peak = max(v for _, v in hits)
    return [(t, round(min(1.0, v / peak), 3)) for t, v in hits]


def analyze(x: np.ndarray, sr: int = SR, sensitivity: float = 1.1,
            bands=BANDS) -> dict[str, list[tuple[float, float]]]:
    """Band-split onset detection. Returns {band_name: [(seconds, velocity)]}."""
    if len(x) < WIN * 2:
        return {}
    # Pad the front with silence so a hit at t=0 has something to rise from.
    # Without it the first frame diffs against itself, and a track that opens
    # on its downbeat — which is most loops — loses that downbeat.
    pad = WIN
    x = np.concatenate([np.zeros(pad), x])
    f, t, Z = signal.stft(x, fs=sr, nperseg=WIN, noverlap=WIN - HOP)
    t = t - pad / sr
    mag = np.abs(Z)
    # Log-compress: onsets are perceived roughly logarithmically, and without
    # this a loud section's flux dwarfs everything else in the file.
    mag = np.log1p(mag * 100.0)

    out = {}
    for name, lo, hi, gap in bands:
        sel = (f >= lo) & (f < hi)
        if not sel.any():
            continue
        env = _flux(mag[sel])
        # Light smoothing kills frame-level jitter without moving the peaks.
        env = np.convolve(env, np.hanning(5) / np.hanning(5).sum(), mode="same")
        hits = _pick_peaks(env, t, gap, sensitivity)
        if hits:
            out[name] = hits
    return out


def analyze_file(path: Path, **kw) -> dict[str, list[tuple[float, float]]]:
    return analyze(load_audio(path), **kw)


# How often the envelope is sampled. 6 Hz is a compromise: fast enough that a
# swell reads as movement rather than as steps, slow enough that a 30 s scene
# does not turn into two thousand cues the firmware has to step through.
ENV_HZ = 6.0
# Below this many onsets per band, the material has no beat worth following.
BEATLESS = 8


def envelope(x: np.ndarray, sr: int = SR, hz: float = ENV_HZ,
             bands=BANDS) -> dict[str, list[tuple[float, float]]]:
    """Band loudness over time, for audio with no beat to detect.

    Onset detection answers "when did something start". Drones, pads, wind and
    sustained organ never start — they just *are*, and the detector correctly
    finds nothing, which leaves the lights frozen on a static base.

    This answers the other question: "how loud is this band right now". Fed
    through the same pulse machinery, the zone tracks the sound's shape and
    the castle breathes with it instead of ignoring it.

    Returns the same {name: [(seconds, level)]} shape as `analyze`, under
    `level_*` names so a scene can ask for either behaviour, or both.
    """
    if len(x) < WIN * 2:
        return {}
    f, t, Z = signal.stft(x, fs=sr, nperseg=WIN, noverlap=WIN - HOP)
    mag = np.abs(Z)
    step = max(1, int(round((sr / HOP) / hz)))

    out: dict[str, list[tuple[float, float]]] = {}
    for name, lo, hi, _gap in bands:
        sel = (f >= lo) & (f < hi)
        if not sel.any():
            continue
        # RMS across the band, then a light smooth so the level glides rather
        # than steps between samples.
        env = np.sqrt((mag[sel] ** 2).mean(axis=0))
        if env.max() <= 0:
            continue
        env = np.convolve(env, np.hanning(9) / np.hanning(9).sum(), mode="same")

        # Perceptual-ish compression, then scale to the band's own range: what
        # matters is the shape of this material, not its absolute level.
        env = np.log1p(env * 50.0)
        lo_v, hi_v = float(env.min()), float(env.max())
        if hi_v - lo_v < 1e-9:
            continue                      # dead flat: nothing to follow
        env = (env - lo_v) / (hi_v - lo_v)

        pts = [(float(t[i]), round(float(env[i]), 3))
               for i in range(0, len(env), step)]
        # Drop the near-silent tail of the list; a zero-level pulse is a cue
        # that costs firmware space and does nothing.
        pts = [(tt, v) for tt, v in pts if v > 0.02]
        if pts:
            out[name.replace("onset_", "level_")] = pts
    return out


def analyze_full(x: np.ndarray, sr: int = SR, sensitivity: float = 1.1,
                 ) -> dict[str, list[tuple[float, float]]]:
    """Onsets, plus a level envelope for any band that has no beat.

    This is what the importer uses. A track can be beaty in the bass and
    beatless up top — a kick under a synth pad — and each band gets whichever
    treatment actually suits it rather than one verdict for the whole file.
    """
    onsets = analyze(x, sr, sensitivity=sensitivity)
    thin = [name for name, _lo, _hi, _g in BANDS
            if len(onsets.get(name, [])) < BEATLESS]
    if not thin:
        return onsets
    levels = envelope(x, sr, bands=[b for b in BANDS if b[0] in thin])
    return {**onsets, **levels}


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(f"usage: {sys.argv[0]} <audio-file> [sensitivity]")
    p = Path(sys.argv[1])
    sens = float(sys.argv[2]) if len(sys.argv) > 2 else 1.1
    x = load_audio(p)
    dur = len(x) / SR
    marks = analyze(x, sensitivity=sens)
    print(f"{p.name}  {dur:.1f}s  peak {np.abs(x).max():.2f}")
    print("-" * 52)
    for name, hits in marks.items():
        rate = len(hits) / dur * 60
        print(f"  {name:<11} {len(hits):>4} onsets  ({rate:>5.0f}/min)  "
              f"first: {', '.join(f'{t:.2f}s' for t, _ in hits[:5])}")
    if not marks:
        print("  no onsets found — try a lower sensitivity, e.g. 0.6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
