"""The soak's thresholds and the pass/fail verdict built from them.

Every limit is a field of `Limits`, documented in LIMIT_HELP (which is also
what `soak.py --help` prints), and every one is a command-line flag of the
same name: `--max-reboots 1`, `--heap-floor-kb 24`. docs/SOAK.md explains
why each default is what it is; the short version is that an unattended
castle on a porch for three nights should not reboot, crash, lose its card
or leak, and may lose its Wi-Fi only briefly and only a few times.

A check whose reading the firmware never reported is INFO ("not reported"),
not a pass and not a fail: an older image is not punished for a field it
does not have, and the verdict says plainly what it could not judge.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

#: The heap-trend check's name, which each of its three outcomes carries.
HEAP_TREND = "heap trend"

#: The flag help, one line each — the documentation of every default.
LIMIT_HELP = {
    "max_reboots": "reboots allowed during the run (uptime went back, or the "
    "boot counter rose) — a castle left alone should not restart",
    "max_crashes": "reboots whose reset reason is a panic, a watchdog or a "
    "brownout (castle_health.h was_crash)",
    "max_outages": "Wi-Fi outages allowed: stretches with no answer of at "
    "least --outage-min-s (a router reboot is one)",
    "max_outage_s": "the longest an outage may last before the castle is "
    "judged not to have come back unaided",
    "outage_min_s": "an unanswered stretch shorter than this is a blip "
    "(counted, never judged)",
    "max_sd_errors": "torn card reads (/api/health sd_read_errors) during the run",
    "max_unmounted": "polls that may find the card unmounted",
    "heap_floor_kb": "the lowest the heap's low-water mark (heap_min_kb) may "
    "reach; ~20 KB is the documented failure floor (docs/RUNBOOK.md)",
    "max_heap_fall_kb_h": "the steepest fall of heap_free_kb, in KB per hour "
    "over one boot, before it is called a leak",
    "heap_trend_min_h": "hours one boot must span before the heap trend is judged",
    "rssi_floor": "a signal below this many dBm is weak",
    "max_weak_rssi_pct": "the share of polls that may read a weak signal",
    "max_drift_ms": "the heard clock's worst wander from the speaker "
    "(sync_drift_ms, v5.72): lights this far off are visibly late",
    "max_light_evicted": "light frames the mailbox may drop; -1 reports "
    "without judging (a desk streaming lights drops some by design)",
    "max_failed_starts": "show starts (--drive) that did not reach playing",
}


@dataclass
class Limits:
    max_reboots: int = 0
    max_crashes: int = 0
    max_outages: int = 3
    max_outage_s: float = 300.0
    outage_min_s: float = 20.0
    max_sd_errors: int = 0
    max_unmounted: int = 0
    heap_floor_kb: int = 20
    max_heap_fall_kb_h: float = 2.0
    heap_trend_min_h: float = 6.0
    rssi_floor: int = -80
    max_weak_rssi_pct: float = 20.0
    max_drift_ms: int = 250
    max_light_evicted: int = -1
    max_failed_starts: int = 0


def limit_fields() -> list[tuple[str, type, object]]:
    """(name, type, default) of every limit, in declaration order."""
    out = []
    for f in fields(Limits):
        default = f.default
        out.append((f.name, type(default), default))
    return out


@dataclass
class Check:
    name: str
    state: str  # PASS / FAIL / INFO
    reading: str
    limit: str = ""

    def line(self) -> str:
        tail = f"  (limit {self.limit})" if self.limit else ""
        return f"  {self.state:<4}  {self.name:<22} {self.reading}{tail}"


def _judge(
    name: str,
    value: float | None,
    limit: float,
    unit: str = "",
    below: bool = False,
    shown: str = "",
) -> Check:
    """value <= limit passes (>= when `below`); None is INFO."""
    if value is None:
        return Check(name, "INFO", "not reported by this firmware")
    ok = value >= limit if below else value <= limit
    reading = shown or f"{value:g}{unit}"
    return Check(name, "PASS" if ok else "FAIL", reading, f"{limit:g}{unit}")


def verdict(s: dict, lim: Limits, *, full: bool = True) -> list[Check]:
    """Every check, in the order a person reads a bad night."""
    if not s["samples"]:
        return [Check("castle answered", "FAIL", "never — nothing to judge")]
    return [
        *_life(s, lim),
        *_card_and_heap(s, lim),
        *_radio_and_show(s, lim),
        *_disruptions(s),
        *_notes(s, full),
    ]


def _life(s: dict, lim: Limits) -> list[Check]:
    """Reboots, crashes and outages: did it stay up, and stay on the air?"""
    reasons = ", ".join(s["reset_reasons"]) or "none"
    outages = s["outages"]
    rebooted = sum(1 for o in outages if o["rebooted"])
    answering = s["answering_at_end"]
    return [
        _judge(
            "reboots",
            s["reboots"],
            lim.max_reboots,
            shown=f"{s['reboots']} ({reasons})",
        ),
        _judge("crashes", s["crashes"], lim.max_crashes),
        _judge(
            "Wi-Fi outages",
            len(outages),
            lim.max_outages,
            shown=f"{len(outages)} ({rebooted} with a reboot, {s['blips']} blips)",
        ),
        _judge("longest outage", s["longest_outage_s"], lim.max_outage_s, " s"),
        Check(
            "answering at the end",
            "PASS" if answering else "FAIL",
            "yes" if answering else "NO — it never came back",
        ),
    ]


def _card_and_heap(s: dict, lim: Limits) -> list[Check]:
    last = f" (last: {s['sd_last_error']})" if s["sd_last_error"] else ""
    return [
        _judge(
            "card read errors",
            s["sd_read_errors"],
            lim.max_sd_errors,
            shown=f"{s['sd_read_errors']}{last}",
        ),
        _judge("card unmounted polls", s["unmounted_samples"], lim.max_unmounted),
        _judge(
            "heap low-water", s["heap_min_kb"], lim.heap_floor_kb, " KB", below=True
        ),
        _heap_trend(s["heap_trend_kb_h"], lim),
    ]


def _radio_and_show(s: dict, lim: Limits) -> list[Check]:
    weak = s["rssi_weak_pct"]
    shown = ""
    if weak is not None:
        shown = (
            f"{weak}% of polls under {lim.rssi_floor} dBm, "
            f"{s['rssi_drops']} drops, worst {s['rssi_min']} dBm"
        )
    checks = [_judge("weak signal", weak, lim.max_weak_rssi_pct, "%", shown=shown)]
    drift = s["sync_drift_ms_max"]
    if drift is None:
        checks.append(
            Check("sync drift", "INFO", "no cue show heard (or not reported)")
        )
    else:
        checks.append(_judge("sync drift", drift, lim.max_drift_ms, " ms"))
    evicted = s["light_evicted"]
    if lim.max_light_evicted < 0:
        checks.append(Check("light frames evicted", "INFO", str(evicted)))
    else:
        checks.append(_judge("light frames evicted", evicted, lim.max_light_evicted))
    failed = s["starts_failed"]
    if s["starts_ok"] or failed:
        shown = f"{len(failed)} of {s['starts_ok'] + len(failed)}"
        if failed:
            shown += f" (first: {failed[0]})"
        checks.append(
            _judge(
                "failed show starts", len(failed), lim.max_failed_starts, shown=shown
            )
        )
    return checks


def _disruptions(s: dict) -> list[Check]:
    return [
        Check(
            "disruption command",
            "PASS" if d.get("exit") == 0 else "FAIL",
            f"at {d.get('at_h')} h, exit {d.get('exit')}",
        )
        for d in s["disruptions"]
    ]


def _notes(s: dict, full: bool) -> list[Check]:
    """What the verdict could not judge but a person should know."""
    checks = []
    if s["missing"]:
        checks.append(Check("card reports missing", "INFO", ",".join(s["missing"])))
    if s["monitor_paused_s"]:
        paused = f"{s['monitor_paused_s']} s unwatched (this computer slept)"
        checks.append(Check("monitor paused", "INFO", paused))
    if s["event_overflows"]:
        overflows = f"{s['event_overflows']} (events pushed out between polls)"
        checks.append(Check("event ring overflows", "INFO", overflows))
    if not full:
        early = f"stopped early at {s['hours']} h"
        checks.append(Check("ran the full time", "FAIL", early))
    return checks


def _heap_trend(trend: dict | None, lim: Limits) -> Check:
    if trend is None:
        return Check(HEAP_TREND, "INFO", "too few readings")
    reading = f"{trend['kb_per_h']:+g} KB/h over {trend['hours']:g} h"
    if trend["hours"] < lim.heap_trend_min_h:
        return Check(
            HEAP_TREND,
            "INFO",
            f"{reading} — too short to judge",
            f">= {lim.heap_trend_min_h:g} h",
        )
    ok = trend["kb_per_h"] >= -lim.max_heap_fall_kb_h
    return Check(
        HEAP_TREND,
        "PASS" if ok else "FAIL",
        reading,
        f"-{lim.max_heap_fall_kb_h:g} KB/h",
    )


def passed(checks: list[Check]) -> bool:
    return all(c.state != "FAIL" for c in checks)
