"""The scene block an import prints — wired to what the analyser found.

Split from import_track.py at the 500-line cap along the seam that was
already there: nothing here touches ffmpeg, yt-dlp or the manifest. Given a
track id, its duration and the detected onsets per band, it writes the YAML
to paste under `scenes:` — with decays solved from how busy each band is.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

FRAME = 0.016  # the light engine's tick, matching the firmware

#: One band's detected hits, each [time_s, velocity] (a third element is a
#: re-pin marker). Sequence because the analyser hands over tuples, the
#: markers file lists — the maths only ever reads.
Hits = Sequence[Sequence[float]]


def fit_to_density(hits: Hits, fallback: float) -> tuple[float, float]:
    """Choose a decay and an intensity scale that suit how busy a band is.

    The built-in scenes' decay constants were tuned against Crypt's 48 bpm
    heartbeat — gaps of 1.25 s. Reuse them on a track that hits every 0.24 s
    and the flash is still at ~29% when the next one lands: the zone saturates
    and reads as a continuous smear rather than as pulses. Which is precisely
    the way an imported track stops "making sense".

    So the decay is solved from the material: fall to ~10% by the time the
    next hit is due. And dense bands get their intensity pulled down, because
    a lot of overlapping pulses sum to a floor that never returns to dark.

    Returns (decay_per_frame, intensity_scale).
    """
    if len(hits) < 3:
        return fallback, 1.0

    gaps = sorted(hits[i + 1][0] - hits[i][0] for i in range(len(hits) - 1))
    median_gap = gaps[len(gaps) // 2]
    if median_gap <= 0:
        return fallback, 1.0

    # d^frames = 0.1  ->  d = 0.1 ** (1/frames)
    frames = max(1.0, median_gap / FRAME)
    decay = 0.1 ** (1.0 / frames)
    # Floor at 0.78: faster than that is a single-frame blink nobody sees.
    # Ceiling at the scene's own value, so sparse material keeps its bloom.
    decay = max(0.78, min(fallback, decay))

    # Below ~0.5 s between hits, back the level off so they stay distinct.
    scale = 1.0 if median_gap >= 0.5 else max(0.45, median_gap / 0.5)
    return round(decay, 3), round(scale, 2)


def scene_block(
    tid: str, dur: float, marks: Mapping[str, Hits], ext: str = "mp3"
) -> str:
    """A ready-to-paste scene, wired to whatever the analyser actually found."""
    zones = {"onset_low": "door", "onset_mid": "towerL", "onset_high": "towerR"}
    colors = {
        "onset_low": "[1.0, 0.12, 0.02, 0.0]",
        "onset_mid": "[0.66, 0.10, 1.0, 0.05]",
        "onset_high": "[0.30, 1.0, 0.55, 0.0]",
    }
    decays = {"onset_low": 0.86, "onset_mid": 0.92, "onset_high": 0.94}
    lines = [
        f"  - id: {tid}",
        f"    name: {tid.replace('_', ' ').title()}",
        "    kind: custom",
        "    volume: 0.7",
        f"    duration_ms: {int(dur * 1000)}",
        "    loop: true",
        "    blurb: >",
        f"      Imported track {tid}. Light cues are onset-detected from the",
        "      audio itself, so they follow whatever the track actually does.",
        f"    audio_file: tracks/{tid}.{ext}",
        "    base: {towerL: chill, towerR: chill, door: ember}",
        "    levels: {towerL: 0.4, towerR: 0.4, door: 0.5}",
        "    pulse:",
    ]
    for band, hits in marks.items():
        if not hits:
            continue
        base = band.replace("level_", "onset_")
        z = zones.get(base, "door")
        col = colors.get(base, "[1,1,1,1]")

        if band.startswith("level_"):
            # An envelope wants to GLIDE, not pulse. Its samples arrive at a
            # steady 6 Hz, so a decay that empties between them would chop a
            # smooth swell into a stutter. 0.90 leaves roughly half the level
            # standing when the next sample lands, which reads as breathing.
            lines.append(
                f"      - {{synth: {band}, zone: {z}, intensity: 0.5, "
                f"decay: 0.90, color: {col}}}"
                f"   # {len(hits)} level samples — no beat here, so the zone "
                f"follows loudness instead"
            )
            continue

        decay, scale = fit_to_density(hits, decays.get(band, 0.9))
        intensity = round(0.55 * scale, 3)
        rate = len(hits) / dur * 60 if dur else 0
        note = f"{len(hits)} onsets, {rate:.0f}/min"
        if scale < 1.0:
            note += " — dense, so eased back to stay distinct"
        lines.append(
            f"      - {{synth: {band}, zone: {z}, intensity: {intensity}, "
            f"decay: {decay}, color: {col}}}"
            f"   # {note}"
        )
    lines.append("    cues: []")
    return "\n".join(lines)
