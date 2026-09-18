"""The Python reference renderer's two halves: mix the score, shape the tail.

Split from render_audio.py at the 500-line cap along the seam render_scene_py
already had: everything here works on the buffer — laying each score event
into it and then finishing the whole mix — while render_audio.py stays the
DRIVER (scenes.yaml, the imported song, the stamps, LAME, the size report).

This is the parity reference, not the production path: `make audio` renders
through castle-core's `scene_render` bin (tools/core_bins.py), and
tests/test_scene_render_rust.py holds the two byte-equal. So the arithmetic
here moves only when the Rust moves with it, in the same commit.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import synth

#: Every scene normalised to the same peak — see the comment at the foot of
#: shape_tail for why, and import_convert's limiter for the other half.
TARGET_PEAK = 0.89


def mix_score(
    scene: Mapping[str, Any],
    buf: np.ndarray,
    rng: np.random.Generator,
    sr: int,
    dur: float,
    markers: dict[str, list],
) -> None:
    """Lay every event of the scene's `score:` into `buf`, collecting each
    synth's reported marker times into `markers` as it goes.

    A marker past the end of the scene is dropped (the 0.1 s margin keeps a
    hit that only just lands from being a light cue with no sound behind it),
    and an unknown synth is a hard stop — a typo'd name would otherwise be a
    silently missing layer.
    """
    for ev in scene.get("score") or []:
        name = ev["synth"]
        fn = synth.SYNTHS.get(name)
        if fn is None:
            raise SystemExit(f"scene {scene['id']}: unknown synth {name!r}")
        res = fn(rng, dur=ev["dur"]) if "dur" in ev else fn(rng)
        sig, raw_marks = res if isinstance(res, tuple) else (res, [])
        # A synth may report bare times or (time, velocity) pairs.
        marks: list[tuple[float, float]] = [
            m if isinstance(m, tuple) else (m, 1.0) for m in raw_marks
        ]
        if "take" in ev:  # trim a long piece to fit
            sig = sig[: int(ev["take"] * sr)]
            fade = min(len(sig), int(0.4 * sr))
            if fade:
                sig[-fade:] *= np.linspace(1.0, 0.0, fade)
            marks = [(m, v) for m, v in marks if m < ev["take"]]
        synth._place(buf, sig * float(ev.get("gain", 1.0)), ev["t"])
        markers.setdefault(name, []).extend(
            [int((ev["t"] + m) * 1000), round(v, 3)]
            for m, v in marks
            if ev["t"] + m < dur - 0.1
        )


def shape_tail(
    scene: Mapping[str, Any],
    buf: np.ndarray,
    rng: np.random.Generator,
    sr: int,
    has_track: bool,
) -> np.ndarray:
    """Reverb, ending, ceiling, level — the mix's last four steps, in order."""
    # Imported tracks arrive already produced — adding the stone hall on top
    # of someone else's reverb just makes mud. Scenes can override either way.
    wet = float(scene.get("reverb", 0.0 if has_track else 0.42))
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
    return buf
