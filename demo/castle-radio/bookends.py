"""How a show begins and ends, for option 12 (Ensemble).

A candle intro: when the band takes a while to come in (Day-O's a-cappella
call and answer, 38 s of it; Spell's 6 s lead-in), the castle does not play
the first section's look at a band that is not there. It starts as three
candles, barely lit, and wakes — the candles climbing — with only the
singer's lights over them, until the band's entrance brings the look in.

A finale that matches how the song ends, read off the band's envelope:

- cold   the band is still loud a moment before it stops dead: one white
         slam on the last hit, two lights burst round the door, and the
         castle goes BLACK on the next beat;
- fade   the band dies away over several seconds: the castle fades with
         it, bar by bar, the chase slowing, until one candle is left in
         the door and then nothing;
- ring   the last chord is struck and left to ring: one long hit in the
         look's colour that dies as the chord does, the castle sinking to
         embers with a candle in the door.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from looks import WHITE, ZONES, Cue, Look, level_cue, look_cues, strike
from spin_show import arc

INTRO_MS = 1500  # a band that comes in later than this gets a candle intro
MAX_INTRO = 45000  # ...but never more than this of candles
BAND_HOLD = 1500  # ms the band must hold its level to have come in
CANDLE = (0.04, 0.35)  # the intro's first and last level
LOUD = 0.12  # of the song's loudest: the band is playing
COLD_HOLD = 0.45  # still this loud (of the bars before) at the stop: a cold end
FADE_DROP = 0.6  # the last 1.5 s this quiet against 6-3 s before: a fade
LAST_MS = 20  # the last cue lands a frame before the song's end


@dataclass(frozen=True)
class Ending:
    kind: str  # "cold", "fade" or "ring"
    at: int  # ms: the finale starts (the last hit; where the fade begins)
    end: int  # ms: the band's last sound


def band_in(env: Sequence[float], duration: int, starts: Sequence[int]) -> int:
    """When the band really comes in — its envelope holding half of the
    song's typical loudness (the 75th percentile, so one huge final chorus
    cannot make everything before it an intro) for BAND_HOLD — moved back
    to the bar it lands in (`starts`: every bar's "one"). 0 when it plays
    from the start."""
    n = len(env)
    if n == 0 or duration <= 0:
        return 0
    typical = sorted(env)[int(0.75 * (n - 1))]
    hold = max(1, BAND_HOLD * n // duration)
    at = next(
        (i * duration // n for i in range(n - hold + 1)
         if min(env[i : i + hold]) >= 0.5 * typical),
        0,
    )  # fmt: skip
    before = [s for s in starts if s <= at + 500]
    return min(MAX_INTRO, max(before, default=0))


def intro(first: int, look: Look, door_overlay: str) -> list[Cue]:
    """Three candles waking until the band's entrance at `first` ms, every
    overlay off until then; on the entrance the look arrives and the door
    gets its own overlay back (the towers' comes with the section's look
    record, which the caller adds)."""
    if first < INTRO_MS:
        return []
    steps = max(2, min(8, first // 800))
    out: list[Cue] = [{"t": 0, "bus": "LED", "op": "look", "targets": list(ZONES),
                       "overlay": "none"}]  # fmt: skip
    for k in range(steps):
        t = round(k * (first - 200) / steps)
        level = round(CANDLE[0] + (CANDLE[1] - CANDLE[0]) * k / (steps - 1), 2)
        out += [level_cue(t, z, "candle", level) for z in ZONES]
    out += look_cues(first, look)
    out.append({"t": first, "bus": "LED", "op": "look", "targets": ["door"],
                "overlay": door_overlay})  # fmt: skip
    return out


def _mean(env: Sequence[float], duration: int, a: int, b: int) -> float:
    n = len(env)
    i, j = max(0, a * n // duration), max(0, min(n, b * n // duration))
    part = env[i:j] if j > i else env[max(0, i - 1) : i + 1]
    return sum(part) / len(part) if part else 0.0


def ending(env: Sequence[float], duration: int, last_hit: int) -> Ending:
    """Read how the band stops. `last_hit` is the last strong band onset."""
    n = len(env)
    top = max(env) if env else 0.0
    if n == 0 or top <= 0:
        return Ending("ring", last_hit, duration)
    last = max((i for i, v in enumerate(env) if v >= LOUD * top), default=n - 1)
    end = min(duration, (last + 1) * duration // n)
    before = _mean(env, duration, end - 6000, end - 3000)
    tail = _mean(env, duration, end - 1500, end)
    at_stop = env[last]
    if before > 0 and at_stop >= COLD_HOLD * before:
        return Ending("cold", min(last_hit, end), end)
    if before > 0 and tail <= FADE_DROP * before:
        start = end - 1500
        while (
            start > end - 20000
            and _mean(env, duration, start - 1000, start) < before * 0.9
        ):
            start -= 1000
        return Ending("fade", max(0, start), end)
    return Ending("ring", min(last_hit, end), end)


def finale(e: Ending, look: Look, period: int, duration: int,
           env: Sequence[float]) -> list[Cue]:  # fmt: skip
    """The finale's cues; everything the show had from `e.at` on is dropped."""
    stop = duration - LAST_MS
    out: list[Cue] = []
    if e.kind == "cold":
        out.append(strike(e.at, ZONES, WHITE, 1.0, period * 1.5))
        for n in range(1, 5):
            t = round(e.at + n * period / 4)
            for layer, k in ((0, n), (1, 8 - n)):
                out.append(
                    {**strike(t, ["door"], WHITE, 0.9, 120, arc(k)), "layer": layer}
                )
        dark = min(stop, e.at + period)
        out += [level_cue(dark, z, "off", 0.0) for z in ZONES]
        out.append(_still(dark))
    elif e.kind == "fade":
        top = _mean(env, duration, e.at - 1000, e.at) or 1.0
        t, rate = e.at, 1.0
        while t < min(e.end, stop):
            share = max(0.0, min(1.0, _mean(env, duration, t, t + 4 * period) / top))
            out += [level_cue(t, z, look.towers, round(look.tower_level * share, 2))
                    for z in ZONES[:2]]  # fmt: skip
            out.append(level_cue(t, "door", "candle", round(0.35 * share + 0.08, 2)))
            rate /= 2
            out.append({"t": t, "bus": "LED", "op": "look", "targets": list(ZONES[:2]),
                        "rate": round(rate, 3)})  # fmt: skip
            t += 4 * period
        out += [level_cue(min(e.end, stop), z, "off", 0.0) for z in ZONES[:2]]
        out.append(level_cue(min(e.end + 1500, stop), "door", "off", 0.0))
    else:
        ring = max(1200, min(4000, e.end - e.at))
        out.append(strike(e.at, ZONES, look.a, 1.0, ring))
        out += [level_cue(e.at + 1, z, look.towers, round(look.tower_level * 0.3, 2))
                for z in ZONES[:2]]  # fmt: skip
        out.append(level_cue(e.at + 1, "door", "candle", 0.25))
        out.append(_still(e.at + 1, rate=0.1))
        out += [level_cue(min(e.end + 1500, stop), z, "off", 0.0) for z in ZONES]
    return [c for c in out if c["t"] <= stop]


def _still(t: int, rate: float = 0.0) -> Cue:
    """Every overlay stopped (or slowed to `rate` turns a second)."""
    if rate:
        return {
            "t": t,
            "bus": "LED",
            "op": "look",
            "targets": list(ZONES),
            "rate": rate,
        }
    return {
        "t": t,
        "bus": "LED",
        "op": "look",
        "targets": list(ZONES),
        "overlay": "none",
    }
