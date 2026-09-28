"""What the porch does to a show, and how a show can plan for it.

The castle's "Soften lightning" switch is ON by default: today's firmware
scales EVERY strike's peak by 0.55 instead of 0.92 and slows EVERY decay to
`1 - (1 - d) * 0.35` (firmware/castle_pixels.h). It exists for strobes —
a zone flashing faster than about three times a second — but it also turns a
2-per-second beat into a wash: a beat flash tuned to be down to 12% by the
next beat is still near half-lit when it arrives.

`for_todays_castle` pre-compensates exactly the strikes that are not a
strobe: on each zone, a strike at least STROBE_MS after that zone's last
strike gets the intensity and decay that soften will turn back into what
the show meant. Anything faster — a roll, a stutter — is left for soften to
tame, as it should be. With soften OFF those compensated hits come out
brighter and shorter than designed; soften is on by default, so that is the
porch this plans for. (A rate-aware soften in the firmware makes this step
unnecessary: a v2 show is written for that firmware and skips it.)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

#: A zone struck again sooner than this is strobing: 3 flashes a second.
STROBE_MS = 333
SOFT_PEAK = 0.55 / 0.92  # soften's peak against the normal one
SOFT_SLOW = 0.35  # soften keeps this share of a decay's per-tick loss
Cue = dict[str, Any]


def slow_zones(cues: Sequence[Cue]) -> list[set[str]]:
    """For each cue (same order), the target zones on which it is NOT a strobe."""
    last: dict[str, int] = {}
    out: list[set[str]] = []
    for cue in cues:
        slow: set[str] = set()
        if cue["op"] == "strike":
            for zone in cue["targets"]:
                if cue["t"] - last.get(zone, -(1 << 30)) >= STROBE_MS:
                    slow.add(zone)
                last[zone] = cue["t"]
        out.append(slow)
    return out


def compensated(cue: Cue) -> Cue:
    """The strike that, softened, renders like `cue` unsoftened."""
    decay = 1.0 - (1.0 - cue["decay"]) / SOFT_SLOW
    return {
        **cue,
        "intensity": round(cue["intensity"] / SOFT_PEAK, 3),
        "decay": round(max(0.0, decay), 4),
    }


def for_todays_castle(cues: Sequence[Cue]) -> list[Cue]:
    """Compensate every non-strobe strike for soften. A strike that is a
    strobe on some of its zones and not on others is split in two records at
    the same instant, one compensated and one not."""
    ordered = sorted(cues, key=lambda c: c["t"])
    out: list[Cue] = []
    for cue, slow in zip(ordered, slow_zones(ordered), strict=True):
        if cue["op"] != "strike" or not slow:
            out.append(cue)
            continue
        fast = [z for z in cue["targets"] if z not in slow]
        out.append(
            compensated({**cue, "targets": [z for z in cue["targets"] if z in slow]})
        )
        if fast:
            out.append({**cue, "targets": fast})
    return out


def lead(cues: Sequence[Cue], ms: int) -> list[Cue]:
    """Every cue `ms` earlier (never before 0): for when the lights are
    measured to trail the speaker. Unmeasured, so the default is 0."""
    return [{**c, "t": max(0, c["t"] - ms)} for c in cues] if ms else list(cues)
