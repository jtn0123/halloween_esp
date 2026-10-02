"""A synthetic song for the lab's tests: onsets and peaks at a known tempo,
with grooves, loudness, stops and a singer placed where a test wants them.

No library track, audio or device is involved — this is the shape of an
analysis.json, built by hand.
"""

from __future__ import annotations

from typing import Any

BPM = 120
BEAT = 60000 // BPM
START = 1000  # ms: the first downbeat
PHRASE = 16 * BEAT  # four bars of 4/4
PEAKS = 640


def groove(kind: str, at: int, beat: int) -> dict[str, list[list[float]]]:
    """One beat of a groove. 'A': kick on one and three, snare on two and
    four. 'B': four on the floor with eighth-note hats — a different groove."""
    one = beat % 4 == 0
    t = at / 1000
    if kind == "A":
        if beat % 2 == 0:
            return {"onset_low": [[t, 1.0 if one else 0.7]]}
        return {"onset_mid": [[t, 0.8]]}
    return {
        "onset_low": [[t, 1.0 if one else 0.6]],
        "onset_high": [[t, 0.4], [(at + BEAT // 2) / 1000, 0.4]],
    }


def song(
    plan: str = "AABBAABBAA",
    loud: str = "B",
    stops: tuple[tuple[int, int], ...] = (),
    voice_level: float = 0.6,
    voice_from: int = 8130,
) -> tuple[dict[str, Any], int]:
    """(layers, duration_ms). Each letter of `plan` is one 4-bar phrase of
    that groove; phrases whose groove is in `loud` are loud. `stops` are
    (start, end) ms windows in which the band is silent."""
    duration = START + len(plan) * PHRASE + 1000
    bands: dict[str, list[list[float]]] = {
        "onset_low": [],
        "onset_mid": [],
        "onset_high": [],
    }
    for p, kind in enumerate(plan):
        for b in range(16):
            at = START + p * PHRASE + b * BEAT
            if any(a <= at < e for a, e in stops):
                continue
            for name, hits in groove(kind, at, b).items():
                bands[name] += [
                    h for h in hits if not any(a <= h[0] * 1000 < e for a, e in stops)
                ]
    peaks = []
    for i in range(PEAKS):
        t = i * duration / PEAKS
        p = max(0, min(len(plan) - 1, int((t - START) // PHRASE)))
        level = 0.9 if plan[p] in loud else 0.35
        peaks.append(0.02 if any(a <= t < e for a, e in stops) else level)
    band = {"onsets": bands, "peaks": peaks, "level": 1.0}
    sung = [[t / 1000, 0.8] for t in range(voice_from, duration - 1500, 730)]
    voice = {
        "onsets": {"onset_mid": sung},
        "peaks": [0.5] * PEAKS,
        "level": voice_level,
    }
    layers = {
        "backing": {"both": band, "left": band, "right": band},
        "vocals": {"both": voice},
    }
    return layers, duration


def source(duration: int) -> dict[str, Any]:
    return {"id": "radio_test", "name": "Test song", "dur": duration, "zones": {}}
