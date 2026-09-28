"""What KIND of voice each sung note is, for option 12 (Ensemble): spoken or
sung, a lead alone or a choir behind it, a held note with vibrato, and the
tone — bright and belted or dark and breathy.

Everything is measured, offline, from the separated vocal stem and the pitch
track voice_pitch.py already made of it; the analysis is cached as JSON in
the lab's own directory, never beside the library's files. No device, no
audio playback.

- Spoken or sung is decided per LINE (notes with less than LINE_GAP between
  them), a few seconds of it at a time — Monster Mash talks its verses and
  sings its chorus without ever pausing for breath: a sung line sits on notes — each voiced stretch holds one pitch, and
  those pitches land on the song's own semitone grid — where speech glides
  through every pitch in between. Measured on Thriller's dialogue against its
  singing: 0.58-0.69 of voiced frames on a held pitch against 0.83-0.98, and
  0.29-0.46 of a semitone off the grid against 0.08-0.21.
- A choir is ROUGH: stacked voices are less periodic than one, so YIN's best
  match is worse while the stem is loud. Day-O's solo lines read 0.02-0.10,
  the chorus answering them 0.24-0.41 and the final chorus 0.55. Rough is
  judged against the song's own median — a gravelly lead is rough all song —
  and a stem spread wide in stereo (backing voices usually are) needs less.
- Vibrato is a held note's pitch swinging 4-8 times a second by at least a
  third of a semitone: the autocorrelation of the detrended pitch.
- Tone is the stem's spectral centroid at the note's start, against the
  song's median: +1 an octave brighter than usual, -1 an octave darker.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from voice_pitch import HOP, RATE, WINDOW, Pitch, cmnd_blocks

HOP_MS = HOP * 1000 // RATE  # 10 ms, the pitch track's own
LOUD = 0.25  # of the stem's loudest frame: the frames a feature is read from
LINE_GAP = 1500  # ms of silence between two lines (spin_show.PHRASE_GAP)
CHUNK_MS = 4000  # a longer line is judged a few seconds at a time
HELD_MS = 450  # sections_show.HELD_MS: a note with this much room is held
FLAT, OFF_GRID = 0.75, 0.25  # a line under FLAT and over OFF_GRID is spoken
GLIDING = 0.65  # ...and one under this is spoken wherever its pitches land
ROUGH, ROUGH_RATIO, WIDE_RATIO = 0.24, 1.8, 1.6
VIBRATO_HZ = (4.0, 8.0)
VIBRATO_DEPTH = 0.33  # semitones, peak to peak


@dataclass(frozen=True)
class VoiceTrack:
    """Per HOP_MS frame of the vocal stem: YIN's aperiodicity and the
    spectral centroid (Hz) where the stem is loud, None elsewhere, and the
    side/mid ratio of its stereo image."""

    rough: tuple[float | None, ...]
    bright: tuple[float | None, ...]
    width: tuple[float, ...]

    def window(
        self, series: Sequence[float | None], start: int, end: int
    ) -> list[float]:
        a, b = max(0, start // HOP_MS), max(0, end // HOP_MS)
        return [v for v in series[a : max(a + 1, b)] if v is not None]


@dataclass(frozen=True)
class Kind:
    spoken: bool = False
    choir: bool = False
    vibrato: bool = False
    tone: float = 0.0  # -1 dark .. +1 bright


def decode_stereo(path: Path) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2",
         "-ar", str(RATE), "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).astype(np.float64)


def measure(stereo: np.ndarray) -> VoiceTrack:
    """The per-frame features of a (samples, 2) vocal stem at RATE."""
    mid = stereo.mean(axis=1)
    side = (stereo[:, 0] - stereo[:, 1]) / 2
    rough: list[float] = []
    lo = int(RATE / 1000.0)
    for cmnd, _energy, _floor in cmnd_blocks(mid):
        rough += [float(row[lo:].min()) for row in cmnd]
    n = len(rough)
    frames = np.lib.stride_tricks.sliding_window_view(mid, WINDOW)[::HOP][:n]
    sides = np.lib.stride_tricks.sliding_window_view(side, WINDOW)[::HOP][:n]
    rms = np.sqrt((frames**2).mean(axis=1))
    loud = rms >= LOUD * (rms.max() if rms.size else 0.0)
    spectrum = np.abs(np.fft.rfft(frames * np.hanning(WINDOW), axis=1))
    freqs = np.fft.rfftfreq(WINDOW, 1 / RATE)
    centroid = (spectrum * freqs).sum(axis=1) / np.maximum(spectrum.sum(axis=1), 1e-12)
    width = np.sqrt((sides**2).mean(axis=1)) / np.maximum(rms, 1e-9)
    return VoiceTrack(
        tuple(round(r, 3) if ok else None for r, ok in zip(rough, loud, strict=True)),
        tuple(round(float(c)) if ok else None for c, ok in zip(centroid, loud, strict=True)),
        tuple(round(float(w), 3) for w in width),
    )  # fmt: skip


def track(vocals: Path, cache: Path) -> VoiceTrack:
    """The stem's features, from `cache` when it is newer than the stem."""
    if cache.is_file() and cache.stat().st_mtime >= vocals.stat().st_mtime:
        doc = json.loads(cache.read_text())
        return VoiceTrack(
            tuple(doc["rough"]), tuple(doc["bright"]), tuple(doc["width"])
        )
    out = measure(decode_stereo(vocals))
    cache.write_text(
        json.dumps({"rough": out.rough, "bright": out.bright, "width": out.width})
    )
    return out


def _near(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(b - a) < 1.0


def _stretches(notes: Sequence[float | None]) -> list[np.ndarray]:
    """Voiced runs of 80 ms or more, split where the pitch jumps a semitone."""
    out, i = [], 0
    while i < len(notes):
        if notes[i] is None:
            i += 1
            continue
        j = i
        while j + 1 < len(notes) and _near(notes[j], notes[j + 1]):
            j += 1
        if j - i + 1 >= 8:
            out.append(np.array(notes[i : j + 1], dtype=float))
        i = j + 1
    return out


def tuning(pitch: Pitch) -> float:
    """The song's offset from A440's semitone grid, 0..1 of a semitone."""
    voiced = np.array([n for n in pitch.notes if n is not None], dtype=float)
    if not voiced.size:
        return 0.0
    turn = np.exp(2j * np.pi * (voiced % 1)).mean()
    return float((np.angle(turn) / (2 * np.pi)) % 1)


def spoken(pitch: Pitch, start: int, end: int, grid: float) -> bool:
    """Does the voice between `start` and `end` ms glide like speech?"""
    runs = _stretches(pitch.notes[start // pitch.hop_ms : end // pitch.hop_ms])
    total = sum(len(r) for r in runs)
    if len(runs) < 2 or total * pitch.hop_ms < 250:
        return False
    flat = sum(int((np.abs(r - np.median(r)) < 0.5).sum()) for r in runs) / total
    if flat < GLIDING:
        return True
    off = [
        abs((float(np.median(r)) - grid + 0.5) % 1 - 0.5) for r in runs if len(r) >= 12
    ]
    return flat < FLAT and bool(off) and float(np.mean(off)) > OFF_GRID


def vibrato(pitch: Pitch, start: int, end: int) -> bool:
    """A held note whose pitch swings 4-8 times a second."""
    seg = pitch.notes[start // pitch.hop_ms : end // pitch.hop_ms]
    voiced = [n for n in seg if n is not None]
    if len(seg) < 30 or len(voiced) < 0.7 * len(seg):
        return False
    x = np.array(voiced, dtype=float)
    k = 15  # 150 ms: slower than any vibrato, faster than a phrase's drift
    trend = np.convolve(np.pad(x, k // 2, mode="edge"), np.ones(k) / k, mode="valid")
    wobble = x - trend[: len(x)]
    if np.ptp(wobble) < VIBRATO_DEPTH:
        return False
    ac = np.correlate(wobble, wobble, mode="full")[len(wobble) - 1 :]
    ac = ac / max(ac[0], 1e-12)
    fast = int(1000 / pitch.hop_ms / VIBRATO_HZ[1])
    slow = int(1000 / pitch.hop_ms / VIBRATO_HZ[0])
    lag = fast + int(np.argmax(ac[fast : slow + 1]))
    return bool(ac[lag] >= 0.3 and ac[max(1, lag // 2)] < ac[lag])


def kinds(
    notes: Sequence[tuple[int, float, int]], pitch: Pitch | None,
    voice: VoiceTrack | None,
) -> list[Kind]:  # fmt: skip
    """A Kind per note of `notes` ((time, strength, room), sections_show)."""
    if pitch is None and voice is None:
        return [Kind() for _ in notes]
    lines: list[list[int]] = []
    for i, (at, _s, _room) in enumerate(notes):
        gap = not lines or at - notes[lines[-1][-1]][0] >= LINE_GAP
        if gap or at - notes[lines[-1][0]][0] >= CHUNK_MS:
            lines.append([])
        lines[-1].append(i)
    grid = tuning(pitch) if pitch else 0.0
    talk = [False] * len(notes)
    for line in lines:
        start, last = notes[line[0]][0], notes[line[-1]]
        if pitch and spoken(pitch, start, last[0] + min(last[2], 600), grid):
            for i in line:
                talk[i] = True
    rough_all = [r for r in (voice.rough if voice else ()) if r is not None]
    bright_all = [b for b in (voice.bright if voice else ()) if b is not None]
    rough_mid = float(np.median(rough_all)) if rough_all else 1.0
    bright_mid = float(np.median(bright_all)) if bright_all else 1.0
    width_mid = float(np.median(voice.width)) if voice and voice.width else 1.0
    out = []
    for i, (at, _s, room) in enumerate(notes):
        choir, tone = False, 0.0
        if voice is not None:
            span = (at - 500, at + max(500, min(room, 1500)))
            rough = voice.window(voice.rough, *span)
            wide = voice.window(list(voice.width), *span)
            if len(rough) >= 20:
                r = float(np.median(rough))
                w = float(np.median(wide)) / max(width_mid, 1e-6) if wide else 1.0
                ratio = r / max(rough_mid, 1e-6)
                choir = r >= ROUGH and (
                    ratio >= ROUGH_RATIO or (ratio >= 1.4 and w >= WIDE_RATIO)
                )
            bright = voice.window(voice.bright, at, at + 200)
            if bright:
                octave = np.log2(float(np.median(bright)) / max(bright_mid, 1.0))
                tone = round(max(-1.0, min(1.0, float(octave))), 2)
        wobble = (
            pitch is not None and room >= HELD_MS
            and vibrato(pitch, at + 60, at + round(room * 0.85))
        )  # fmt: skip
        out.append(Kind(talk[i], choir and not talk[i], wobble, tone))
    return out
