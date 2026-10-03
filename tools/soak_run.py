"""The soak's engine: one run against one castle (tools/soak.py is the CLI).

Polls, logs and drives; tools/soak_track.py decides what the replies MEAN
and tools/soak_verdict.py judges the result. Split from soak.py on the seam
between the command line and the loop, so the loop can be driven from a
test with a clock of its own (tests/test_soak.py).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import castle_probe as probe
from soak_track import Tracker
from soak_verdict import Limits, passed, verdict

#: summary.json is rewritten every this many polls (5 minutes at 10 s).
SUMMARY_EVERY = 30
#: A progress line on screen this often, in seconds.
HEARTBEAT_S = 1800.0
#: How long a --disrupt-cmd may run before it is called hung.
DISRUPT_TIMEOUT_S = 600
#: Ring kinds worth a line on screen as they arrive (all are in soak.jsonl).
LOUD_EVENTS = frozenset({"wifi_down", "wifi_up", "restart", "scene_missing"})


def stamp(wall: float) -> str:
    return datetime.fromtimestamp(wall, UTC).isoformat(timespec="seconds")


class Log:
    """soak.jsonl, one object per line and flushed per line — the record a
    killed run still leaves — plus the lines worth a person's eye."""

    def __init__(self, out: Path, echo: Callable[[str], None]) -> None:
        self._f = (out / "soak.jsonl").open("a", encoding="utf-8")
        self._lock = threading.Lock()
        self.echo = echo

    def write(self, kind: str, wall: float, **data: object) -> None:
        line = json.dumps({"at": stamp(wall), "kind": kind, **data})
        with self._lock:
            self._f.write(line + "\n")
            self._f.flush()

    def note(self, wall: float, text: str) -> None:
        self.write("note", wall, text=text)
        self.echo(f"{stamp(wall)}  {text}")

    def close(self) -> None:
        self._f.close()


class Soak:
    """One run against one castle. `run()` returns the exit status."""

    def __init__(
        self,
        host: str,
        args: argparse.Namespace,
        limits: Limits,
        out: Path,
        clock: Callable[[], float] = time.time,
        echo: Callable[[str], None] = print,
    ) -> None:
        self.host, self.args, self.limits, self.out = host, args, limits, out
        self.clock = clock
        self.echo = echo
        self.log = Log(out, echo)
        self.track = Tracker()
        self.track.rssi_floor = limits.rssi_floor
        self.stop = threading.Event()
        self.full = False
        self.bootlogs = 0
        self.ring_primed = False
        self.show_started = False
        self.scene_i = 0
        self.next_scene = 0.0

    # -- the run -------------------------------------------------------------

    def run(self) -> int:
        start = self.clock()
        end = start + self.args.hours * 3600
        self.log.write(
            "start",
            start,
            host=self.host,
            hours=self.args.hours,
            interval=self.args.interval,
            drive=self.args.drive,
            limits=asdict(self.limits),
        )
        self.log.note(
            start, f"soaking {self.host} for {self.args.hours:g} h -> {self.out}"
        )
        self._disrupt(start)
        polls, beat = 0, start
        try:
            while (wall := self.clock()) < end:
                self.poll(wall, polls)
                polls += 1
                if polls % SUMMARY_EVERY == 0:
                    self.save_summary()
                if wall - beat >= HEARTBEAT_S:
                    beat = wall
                    self.echo(self.heartbeat())
                self._wait(min(self.args.interval, max(0.0, end - self.clock())))
            self.full = True
        except KeyboardInterrupt:
            self.log.note(self.clock(), "interrupted: judging what was seen")
        finally:
            self.stop.set()
            self._finish_drive()
        return self.finish()

    def _wait(self, seconds: float) -> None:
        """Sleep to the next poll, and notice when this computer did not
        wake on time — a laptop lid, a stalled process. Those minutes were
        not watched, and the verdict says so instead of blaming the castle."""
        before = self.clock()
        time.sleep(seconds)
        late = self.clock() - before - seconds
        if late > max(30.0, 3 * self.args.interval):
            self.track.paused(late)
            self.log.write("pause", self.clock(), seconds=round(late, 1))
            self.log.note(
                self.clock(), f"this computer stalled or slept for {late:.0f} s"
            )

    def poll(self, wall: float, n: int) -> None:
        try:
            status = probe.get_dict(self.host, "/api/status")
        except probe.Unreachable as e:
            self._missed(wall, str(e))
            return
        if status is None:
            self._missed(wall, "/api/status answered, but not with a status")
            return
        self.log.write("status", wall, reply=status)
        gaps = len(self.track.gaps)
        notes = self.track.sample(wall, status)
        if len(self.track.gaps) > gaps:
            g = self.track.gaps[-1]
            self.log.write(
                "gap",
                wall,
                seconds=round(g.seconds, 1),
                rebooted=g.rebooted,
                error=g.error,
            )
        if n % max(1, self.args.slow_every) == 0 or notes:
            notes += self._slow(wall, status)
        for text in notes:
            self.log.note(wall, text)
        rebooted = any(t.startswith("REBOOT") for t in notes)
        if self.track.samples == 1 or rebooted:
            self._bootlog(wall)
        self._drive(wall, status, rebooted)

    def _slow(self, wall: float, status: dict) -> list[str]:
        """Health and the event ring: the counters and the record of the
        ticks a 10-second poll cannot see."""
        try:
            health = probe.get_dict(self.host, "/api/health")
            ring = probe.get_json(self.host, "/api/events")
        except probe.Unreachable as e:
            self._missed(wall, str(e))
            return []
        notes: list[str] = []
        if health is not None:
            self.log.write("health", wall, reply=health)
            notes += self.track.health(wall, health, status)
        if isinstance(ring, list):
            for ev in self.track.events_in(ring):
                self.log.write("event", wall, **ev)
                if self.ring_primed and ev["e"] in LOUD_EVENTS:
                    notes.append(f"castle event: {ev['e']} {ev['a']}".rstrip())
            self.ring_primed = True
        return notes

    def _missed(self, wall: float, error: str) -> None:
        self.log.write("miss", wall, error=error)
        note = self.track.miss(wall, error)
        if note:
            self.log.note(wall, note)

    def _bootlog(self, wall: float) -> None:
        """The castle's own boot log, kept beside the run: the mount, the
        card listing, and after a crash the tail of the life before."""
        try:
            code, body = probe.request(self.host, "GET", "/api/bootlog")
        except probe.Unreachable:
            return
        if code != 200:
            return
        self.bootlogs += 1
        name = f"bootlog-{self.bootlogs}.txt"
        (self.out / name).write_bytes(body)
        self.log.write("bootlog", wall, file=name, bytes=len(body))

    # -- driving the show ----------------------------------------------------

    def _drive(self, wall: float, status: dict, rebooted: bool) -> None:
        if self.args.drive == "show" and (not self.show_started or rebooted):
            self.show_started = self._start(
                wall,
                "/api/show/start",
                "the evening show",
                lambda s: s.get("show_on") is True,
            )
        elif self.args.drive == "scenes" and wall >= self.next_scene:
            self.next_scene = wall + self.args.scene_every
            ids = self.args.scenes or probe.scene_ids(status)
            if not ids:
                self.track.started(False, "the castle lists no scenes")
                return
            sid = ids[self.scene_i % len(ids)]
            self.scene_i += 1
            self._start(
                wall,
                "/api/scene?s=" + urllib.parse.quote(sid),
                sid,
                lambda s: s.get("playing") is True and s.get("scene") == sid,
            )

    def _start(
        self, wall: float, path: str, what: str, ok: Callable[[dict], bool]
    ) -> bool:
        """POST a start and watch status until it took. A castle that is
        not answering is an outage, already counted, not a failed start."""
        try:
            code = probe.post(self.host, path)
        except probe.Unreachable as e:
            self._missed(wall, str(e))
            return False
        t0 = time.monotonic()
        while code == 200 and time.monotonic() - t0 < self.args.start_timeout:
            time.sleep(0.25)
            try:
                s = probe.get_dict(self.host, "/api/status")
            except probe.Unreachable:
                continue
            if s is not None and ok(s):
                took = round(time.monotonic() - t0, 2)
                self.track.started(True, what)
                self.log.write("drive", wall, what=what, ok=True, after_s=took)
                return True
        why = (
            f"HTTP {code}"
            if code != 200
            else (f"not running after {self.args.start_timeout:g} s")
        )
        self.track.started(False, f"{what}: {why}")
        self.log.write("drive", wall, what=what, ok=False, error=why)
        self.log.note(wall, f"start FAILED: {what}: {why}")
        return False

    def _finish_drive(self) -> None:
        """Leave the castle as quiet as the soak found it."""
        path = {"show": "/api/show/stop", "scenes": "/api/stop"}.get(self.args.drive)
        if path is None or (self.args.drive == "show" and not self.show_started):
            return
        try:
            probe.post(self.host, path)
        except probe.Unreachable:
            pass

    def _disrupt(self, start: float) -> None:
        """--disrupt-cmd at each --disrupt-at hour, on its own thread so the
        polls go on watching while (say) the router is off."""
        cmd = self.args.disrupt_cmd
        if not cmd:
            return

        def go() -> None:
            for at_h in self.args.disrupt_at:
                if self.stop.wait(max(0.0, start + at_h * 3600 - self.clock())):
                    return
                t0 = self.clock()
                self.log.note(t0, f"disruption at {at_h:g} h: {cmd}")
                try:
                    r = subprocess.run(
                        cmd,
                        shell=True,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=DISRUPT_TIMEOUT_S,
                    )
                    code, said = r.returncode, (r.stdout + r.stderr)[-500:]
                except subprocess.TimeoutExpired:
                    code, said = -1, f"still running after {DISRUPT_TIMEOUT_S} s"
                d = {
                    "at_h": at_h,
                    "exit": code,
                    "seconds": round(self.clock() - t0, 1),
                    "output": said,
                }
                self.track.disruptions.append(d)
                self.log.write("disrupt", self.clock(), **d)

        threading.Thread(target=go, daemon=True, name="soak-disrupt").start()

    # -- the numbers ---------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        s = self.track.summary(self.clock(), self.limits.outage_min_s)
        return {"host": self.host, **s}

    def save_summary(self) -> None:
        text = json.dumps(self.summary(), indent=2) + "\n"
        (self.out / "summary.json").write_text(text, encoding="utf-8")

    def heartbeat(self) -> str:
        s = self.summary()
        return (
            f"{stamp(self.clock())}  {s['hours']:.1f} h: {s['samples']} polls, "
            f"{s['reboots']} reboots, {len(s['outages'])} outages, "
            f"heap low-water {s['heap_min_kb']} KB"
        )

    def finish(self) -> int:
        now = self.clock()
        s = self.summary()
        self.save_summary()
        checks = verdict(s, self.limits, full=self.full)
        ok = passed(checks)
        head = (
            f"SOAK {'PASS' if ok else 'FAIL'}: {self.host}, {s['hours']:g} h, "
            f"{s['samples']} polls, {s['misses']} unanswered"
        )
        text = "\n".join([head, *(c.line() for c in checks)])
        (self.out / "verdict.txt").write_text(text + "\n", encoding="utf-8")
        self.log.write("verdict", now, passed=ok, checks=[asdict(c) for c in checks])
        self.log.close()
        self.echo(text)
        return 0 if ok else 1
