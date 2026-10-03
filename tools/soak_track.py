"""What a soak has seen so far — the state machine behind tools/soak.py.

Pure: no sockets, no clock of its own. The loop in soak.py feeds it every
reply (and every failure to get one) with the wall-clock time it happened,
and asks it for notes worth logging and, at the end, for the numbers the
verdict (soak_verdict.py) judges. Kept apart so the arithmetic — when is a
smaller uptime a reboot, when is a missed poll an outage, which events are
new — is tested on made-up timelines in milliseconds rather than on a
castle in real time.

Every field is read defensively: an older firmware simply lacks a key, and
a reading this module never received is reported as "not reported", never
as zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import castle_probe as probe

#: sd_web_state.h kEventRing — a full ring with no overlap means lines were
#: pushed out between two polls.
EVENT_RING = 64
#: A smaller uptime than the last one is always a reboot; so is one this far
#: (or 1% of the time since) behind where the last reading said it would be,
#: which is a reboot hidden inside a long outage.
CONTINUITY_SLACK_S = 15.0


@dataclass
class Gap:
    """A stretch with no answer: from the first failed poll to the first
    good one. `rebooted` when the castle came back with a fresh uptime."""

    start: float
    end: float = 0.0
    error: str = ""
    rebooted: bool = False

    @property
    def seconds(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Boot:
    """One life of the castle as the soak saw it."""

    seen_at: float
    reason: str = ""
    crash: bool = False
    how: str = "first contact"
    heap: list[tuple[float, int]] = field(default_factory=list)
    sd_errors: int = 0
    evicted: int = 0


class Tracker:
    """Feed it replies in time order; read `summary()` at any point."""

    def __init__(self) -> None:
        self.boots: list[Boot] = []
        self.gaps: list[Gap] = []
        self.open_gap: Gap | None = None
        self.samples = 0
        self.misses = 0
        self.last_wall: float | None = None
        self.last_up: int | None = None
        self.first_wall: float | None = None
        self.versions: list[str] = []
        self.unmounted = 0
        self.mounted: bool | None = None
        self.rssi: list[int] = []
        self.rssi_drops = 0
        self.rssi_floor = -80
        self.heap_min_kb: int | None = None
        self.max_drift_ms: int | None = None
        self.max_lead_ms: int | None = None
        self.missing: set[str] = set()
        self.sd_last_error = ""
        self.events: dict[str, int] = {}
        self.event_overflows = 0
        self.pauses: list[float] = []
        self.starts_ok = 0
        self.starts_failed: list[str] = []
        self.disruptions: list[dict[str, object]] = []
        self._ring: list[tuple[int, str, str]] = []
        self._ring_primed = False
        self._boots_seen: int | None = None
        self._bump_expected = False
        self._need_reason = True
        self._sd_base: int | None = None
        self._evict_base: int | None = None

    # -- the castle answered ------------------------------------------------

    @property
    def boot(self) -> Boot | None:
        return self.boots[-1] if self.boots else None

    def sample(self, wall: float, status: dict) -> list[str]:
        """One /api/status reply. Returns the notes worth a log line."""
        notes: list[str] = []
        self.samples += 1
        if self.first_wall is None:
            self.first_wall = wall
        up = probe.as_int(status, "uptime_s")
        rebooted = self._rebooted(wall, up)
        if not self.boots:
            self.boots.append(Boot(seen_at=wall))
        elif rebooted:
            notes.append(self._new_boot(wall, "uptime went back"))
        if self.open_gap is not None:
            gap, self.open_gap = self.open_gap, None
            gap.end, gap.rebooted = wall, rebooted
            self.gaps.append(gap)
            notes.append(
                f"castle back after {gap.seconds:.0f} s"
                + (" (it had rebooted)" if rebooted else " (it stayed up)")
            )
        self.last_wall, self.last_up = wall, up
        notes += self._readings(wall, status)
        return notes

    def _rebooted(self, wall: float, up: int | None) -> bool:
        if up is None or self.last_up is None or self.last_wall is None:
            return False
        elapsed = wall - self.last_wall
        expected = self.last_up + elapsed
        slack = max(CONTINUITY_SLACK_S, 0.01 * elapsed)
        return up < self.last_up or up + slack < expected

    def _new_boot(self, wall: float, how: str) -> str:
        self.boots.append(Boot(seen_at=wall, how=how))
        self._ring = []
        self._need_reason = True
        self._sd_base = self._evict_base = 0
        if how == "uptime went back":
            self._bump_expected = True
        return f"REBOOT detected ({how})"

    def _readings(self, wall: float, status: dict) -> list[str]:
        notes: list[str] = []
        boot = self.boot
        assert boot is not None
        version = status.get("version")
        if isinstance(version, str) and version not in self.versions[-1:]:
            if self.versions:
                notes.append(f"firmware changed: {self.versions[-1]} -> {version}")
            self.versions.append(version)
        mounted = status.get("sd_mounted")
        if isinstance(mounted, bool):
            if not mounted:
                self.unmounted += 1
            if self.mounted is not None and mounted != self.mounted:
                notes.append("card " + ("mounted again" if mounted else "UNMOUNTED"))
            self.mounted = mounted
        rssi = probe.as_int(status, "rssi")
        if rssi is not None and rssi != 0:  # 0 is "not associated"
            weak_before = bool(self.rssi) and self.rssi[-1] < self.rssi_floor
            if rssi < self.rssi_floor and not weak_before:
                self.rssi_drops += 1
                notes.append(f"signal weak: {rssi} dBm")
            self.rssi.append(rssi)
        heap = probe.as_int(status, "heap_free_kb")
        if heap is not None:
            boot.heap.append((wall, heap))
        for key, attr in (
            ("sync_drift_ms", "max_drift_ms"),
            ("sync_lead_ms", "max_lead_ms"),
        ):
            got = probe.as_int(status, key)
            if got is not None and got >= 0:  # -1 is "not heard yet"
                setattr(self, attr, max(got, getattr(self, attr) or 0))
        missing = status.get("missing")
        if isinstance(missing, str) and missing:
            new = {m for m in missing.split(",") if m} - self.missing
            if new:
                notes.append("card missing: " + ",".join(sorted(new)))
            self.missing |= new
        evicted = probe.as_int(status, "light_evicted")
        if evicted is not None:
            if self._evict_base is None:
                self._evict_base = evicted  # what happened before we came
            boot.evicted = max(boot.evicted, evicted - self._evict_base)
        return notes

    def health(
        self, wall: float, health: dict, status: dict | None = None
    ) -> list[str]:
        """One /api/health reply (status: the latest, for v5.75's field)."""
        notes: list[str] = []
        if self.boot is None:
            self.boots.append(Boot(seen_at=wall))
        boots = probe.as_int(health, "boots")
        if boots is not None and self._boots_seen is not None:
            extra = boots - self._boots_seen
            if extra > 0 and self._bump_expected:
                extra -= 1
            self._bump_expected = False
            notes.extend(
                self._new_boot(wall, "boot counter rose") for _ in range(max(0, extra))
            )
        if boots is not None:
            self._boots_seen = boots
        boot = self.boot
        assert boot is not None
        if self._need_reason:
            reason = probe.reset_reason(status, health)
            boot.reason = reason
            boot.crash = probe.is_crash(reason, health)
            self._need_reason = False
            if len(self.boots) > 1 or boot.crash:
                notes.append(
                    f"reset reason: {reason or 'not reported'}"
                    + (" — a CRASH" if boot.crash else "")
                )
        errors = probe.as_int(health, "sd_read_errors")
        if errors is not None:
            if self._sd_base is None:
                self._sd_base = errors
            now = max(0, errors - self._sd_base)
            if now > boot.sd_errors:
                notes.append(f"card read errors: {now} this boot")
            boot.sd_errors = max(boot.sd_errors, now)
        last = health.get("sd_last_error")
        if isinstance(last, str) and last and last != self.sd_last_error:
            self.sd_last_error = last
        low = probe.as_int(health, "heap_min_kb")
        if low is not None:
            self.heap_min_kb = (
                low if self.heap_min_kb is None else min(low, self.heap_min_kb)
            )
        return notes

    def events_in(self, ring: list) -> list[dict]:
        """The /api/events reply; returns the entries not seen before."""
        rows = [
            (int(e["t"]), str(e["e"]), str(e.get("a", "")))
            for e in ring
            if isinstance(e, dict) and isinstance(e.get("t"), int) and "e" in e
        ]
        old = self._ring
        new = rows
        for k in range(len(old) + 1):
            tail = old[k:]
            if rows[: len(tail)] == tail:
                new = rows[len(tail) :]
                if old and not tail and len(rows) >= EVENT_RING:
                    self.event_overflows += 1
                break
        self._ring = rows
        if self._ring_primed:  # the first ring is history from before the run
            for _t, kind, _a in new:
                self.events[kind] = self.events.get(kind, 0) + 1
        self._ring_primed = True
        return [{"t": t, "e": kind, "a": a} for t, kind, a in new]

    # -- the castle did not answer, and other things that happened ---------

    def miss(self, wall: float, error: str) -> str | None:
        """A failed poll. Returns a note when it opens a gap."""
        self.misses += 1
        if self.open_gap is None:
            self.open_gap = Gap(start=wall, error=error)
            return f"castle NOT answering: {error}"
        return None

    def paused(self, seconds: float) -> None:
        """The monitoring computer itself slept or stalled this long."""
        self.pauses.append(seconds)

    def started(self, ok: bool, what: str) -> None:
        if ok:
            self.starts_ok += 1
        else:
            self.starts_failed.append(what)

    # -- the numbers --------------------------------------------------------

    def summary(self, now: float, outage_min_s: float = 20.0) -> dict[str, Any]:
        gaps = list(self.gaps)
        if self.open_gap is not None:
            gaps.append(Gap(self.open_gap.start, now, self.open_gap.error))
        outages = [g for g in gaps if g.seconds >= outage_min_s]
        later = self.boots[1:]
        return {
            "hours": round((now - (self.first_wall or now)) / 3600, 4),
            "samples": self.samples,
            "misses": self.misses,
            "versions": self.versions,
            "reboots": len(later),
            "crashes": sum(1 for b in later if b.crash),
            "reset_reasons": [b.reason or "not reported" for b in later],
            "first_boot_reason": self.boots[0].reason if self.boots else "",
            "outages": [
                {
                    "start": g.start,
                    "seconds": round(g.seconds, 1),
                    "rebooted": g.rebooted,
                    "error": g.error,
                }
                for g in outages
            ],
            "blips": len(gaps) - len(outages),
            "longest_outage_s": round(
                max((g.seconds for g in outages), default=0.0), 1
            ),
            "answering_at_end": self.open_gap is None and self.samples > 0,
            "sd_read_errors": sum(b.sd_errors for b in self.boots),
            "sd_last_error": self.sd_last_error,
            "unmounted_samples": self.unmounted,
            "missing": sorted(self.missing),
            "heap_min_kb": self.heap_min_kb,
            "heap_trend_kb_h": heap_trend(self.boots),
            "rssi_min": min(self.rssi, default=None),
            "rssi_median": sorted(self.rssi)[len(self.rssi) // 2]
            if self.rssi
            else None,
            "rssi_drops": self.rssi_drops,
            "rssi_weak_pct": round(
                100.0
                * sum(1 for r in self.rssi if r < self.rssi_floor)
                / len(self.rssi),
                1,
            )
            if self.rssi
            else None,
            "light_evicted": sum(b.evicted for b in self.boots),
            "sync_drift_ms_max": self.max_drift_ms,
            "sync_lead_ms_max": self.max_lead_ms,
            "events": dict(sorted(self.events.items())),
            "event_overflows": self.event_overflows,
            "monitor_paused_s": round(sum(self.pauses), 1),
            "starts_ok": self.starts_ok,
            "starts_failed": self.starts_failed,
            "disruptions": self.disruptions,
        }


def heap_trend(boots: list[Boot]) -> dict[str, float] | None:
    """The least-squares slope of heap_free_kb over the LONGEST boot, in KB
    per hour, and the hours it spans — a leak is a steady fall across a
    life, and a reboot resets the heap, so lives are never mixed."""
    best = max(
        boots,
        key=lambda b: b.heap[-1][0] - b.heap[0][0] if len(b.heap) > 1 else -1.0,
        default=None,
    )
    if best is None or len(best.heap) < 3:
        return None
    xs = [(w - best.heap[0][0]) / 3600 for w, _ in best.heap]
    ys = [float(kb) for _, kb in best.heap]
    n, mx, my = len(xs), sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mx) ** 2 for x in xs)
    if var <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / var
    return {"kb_per_h": round(slope, 3), "hours": round(xs[-1], 3), "samples": float(n)}
