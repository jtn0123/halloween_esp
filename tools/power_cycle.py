#!/usr/bin/env python3
"""Switch a castle off and on N times and check it comes back whole — `make power-cycle`.

  .venv/bin/python tools/power_cycle.py 192.168.1.20 --cycles 50 \\
      --off-cmd "kasa --host 192.168.1.30 off" --on-cmd "kasa --host 192.168.1.30 on"

The plug is yours: --off-cmd and --on-cmd are any shell commands that cut and
restore the castle's power — a smart plug's own CLI, a curl to its local API,
a Home Assistant webhook. Each cycle:

  1. --off-cmd, then wait for the castle to stop answering. A plug that did
     not cut the power is the harness's failure, not the castle's (exit 2);
  2. hold it off --off-s seconds (default 10: long enough for the carrier's
     capacitors to drain, so the next start is a real cold boot);
  3. --on-cmd, and time how long until /api/status answers — the boot time;
  4. check it came back whole: a fresh uptime (it really restarted), a
     firmware version, the card mounted and the scene list read, and a reset
     reason that is not a crash (a BROWNOUT at power-up is exactly what this
     is here to catch);
  5. start a short scene (--scene, default the castle's first), confirm
     status says it is playing, stop it, and confirm it stopped.

Every cycle is a line in cycles.jsonl under --out, the totals go in
summary.json, and the verdict — every cycle whole, no boot slower than
--max-boot-s — goes on screen and into verdict.txt. Exit 0 pass, 1 fail,
2 the harness could not run. Whatever happens, the castle is left switched on.

tests/test_power_cycle.py runs it against tools/castle_emu.py, with a fake
plug (tests/fake_plug.py) that stops and starts the emulator's process.
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import statistics
import subprocess
import sys
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import castle_probe as probe
import hosts
from soak import keep_awake

REPO = Path(__file__).resolve().parent.parent
#: How long a plug command may take before the harness gives up on it.
CMD_TIMEOUT_S = 60.0
#: A poll while waiting for the castle: short, so a powered-off castle (whose
#: address times out rather than refusing) costs seconds, not minutes.
POLL_TIMEOUT_S = 2.0


class HarnessError(RuntimeError):
    """The run could not go on, and it is not the castle's fault."""


@dataclass
class Cycle:
    n: int
    boot_s: float | None = None
    ready_s: float | None = None
    uptime_s: int | None = None
    version: str = ""
    reason: str = ""
    scene: str = ""
    play_s: float | None = None
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def line(self) -> str:
        boot = f"{self.boot_s:.1f} s" if self.boot_s is not None else "no answer"
        head = f"cycle {self.n}: {'whole' if self.ok else 'FAIL'}, booted in {boot}"
        return head + "".join(f"\n    {f}" for f in self.failures)


def shell(cmd: str, what: str) -> None:
    """Run a plug command; a non-zero exit is the harness's failure."""
    try:
        r = subprocess.run(
            cmd,
            shell=True,
            check=False,
            capture_output=True,
            text=True,
            timeout=CMD_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as e:
        raise HarnessError(
            f"the {what} command hung ({CMD_TIMEOUT_S:g} s): {cmd}"
        ) from e
    if r.returncode != 0:
        said = (r.stdout + r.stderr).strip()[-300:]
        raise HarnessError(
            f"the {what} command failed (exit {r.returncode}): {cmd}"
            + (f"\n  {said}" if said else "")
        )


def status_now(host: str) -> dict | None:
    try:
        return probe.get_dict(host, "/api/status", POLL_TIMEOUT_S)
    except probe.Unreachable:
        return None


def wait_status(
    host: str, ok: Callable[[dict], bool], timeout: float, interval: float
) -> tuple[bool, dict | None]:
    """Poll status until `ok` holds: (held?, the last status seen)."""
    deadline, last = time.monotonic() + timeout, None
    while True:
        s = status_now(host)
        if s is not None:
            last = s
            if ok(s):
                return True, s
        if time.monotonic() >= deadline:
            return False, last
        time.sleep(interval)


def power_off(host: str, args: argparse.Namespace) -> None:
    shell(args.off_cmd, "off")
    deadline = time.monotonic() + args.down_timeout
    while status_now(host) is not None:
        if time.monotonic() >= deadline:
            raise HarnessError(
                f"the castle still answers {args.down_timeout:g} s after the off "
                "command: the plug did not cut its power"
            )
        time.sleep(args.interval)


def power_on(host: str, args: argparse.Namespace, n: int) -> Cycle:
    shell(args.on_cmd, "on")
    t_on, c = time.monotonic(), Cycle(n)
    up, first = wait_status(host, lambda s: True, args.boot_timeout, args.interval)
    if not up or first is None:
        c.failures.append(f"did not answer within {args.boot_timeout:g} s of power")
        return c
    c.boot_s = round(time.monotonic() - t_on, 2)
    if c.boot_s > args.max_boot_s:
        c.failures.append(f"slow boot: {c.boot_s:g} s (limit {args.max_boot_s:g} s)")
    after_boot(host, args, c, first)
    return c


def after_boot(host: str, args: argparse.Namespace, c: Cycle, first: dict) -> None:
    """Steps 4 and 5: is the castle that answered a whole one?"""
    c.uptime_s = probe.as_int(first, "uptime_s")
    if c.uptime_s is not None and c.boot_s is not None and c.uptime_s > c.boot_s + 5:
        c.failures.append(f"uptime {c.uptime_s} s: it never lost power")
    version = first.get("version")
    c.version = version if isinstance(version, str) else ""
    if not c.version:
        c.failures.append("status names no firmware version")
    t0 = time.monotonic()
    ready, last = wait_status(
        host,
        lambda s: s.get("sd_mounted") is True and bool(probe.scene_ids(s)),
        args.ready_timeout,
        args.interval,
    )
    if not ready:
        why = (
            "card not mounted"
            if (last or {}).get("sd_mounted") is not True
            else ("no scene list")
        )
        c.failures.append(f"{why} {args.ready_timeout:g} s after it answered")
        return
    c.ready_s = round(time.monotonic() - t0, 2)
    try:
        health = probe.get_dict(host, "/api/health")
    except probe.Unreachable:
        health = None
    c.reason = probe.reset_reason(last, health)
    if probe.is_crash(c.reason, health):
        c.failures.append(f"came back from a crash: {c.reason}")
    c.scene = args.scene or probe.scene_ids(last)[0]
    play_and_stop(host, args, c)


def play_and_stop(host: str, args: argparse.Namespace, c: Cycle) -> None:
    sid, t0 = c.scene, time.monotonic()
    try:
        code = probe.post(host, "/api/scene?s=" + urllib.parse.quote(sid))
    except probe.Unreachable as e:
        c.failures.append(f"scene {sid}: {e}")
        return
    if code != 200:
        c.failures.append(f"scene {sid}: HTTP {code}")
        return
    playing, _ = wait_status(
        host,
        lambda s: s.get("playing") is True and s.get("scene") == sid,
        args.play_timeout,
        args.interval,
    )
    if not playing:
        c.failures.append(f"scene {sid} not playing after {args.play_timeout:g} s")
    c.play_s = round(time.monotonic() - t0, 2) if playing else None
    try:
        probe.post(host, "/api/stop")
    except probe.Unreachable as e:
        c.failures.append(f"stop: {e}")
        return
    stopped, _ = wait_status(
        host,
        lambda s: s.get("scene") == "stop" and s.get("playing") is not True,
        args.play_timeout,
        args.interval,
    )
    if not stopped:
        c.failures.append(f"still playing {args.play_timeout:g} s after a stop")


def run(
    host: str, args: argparse.Namespace, out: Path, echo: Callable[[str], None] = print
) -> int:
    if status_now(host) is None:
        echo(f"power-cycle: {host} is not answering before the first cycle — is it on?")
        return 2
    cycles: list[Cycle] = []
    harness, interrupted, powered = "", False, True
    with (out / "cycles.jsonl").open("a", encoding="utf-8") as log:
        try:
            for n in range(1, args.cycles + 1):
                power_off(host, args)
                powered = False
                time.sleep(args.off_s)
                c = power_on(host, args, n)
                powered = True
                cycles.append(c)
                log.write(json.dumps({"ok": c.ok, **asdict(c)}) + "\n")
                log.flush()
                echo(c.line())
                streak = len(cycles) - max(
                    (i + 1 for i, x in enumerate(cycles) if x.ok), default=0
                )
                if streak >= args.stop_after:
                    echo(f"stopping: {streak} cycles in a row failed")
                    break
                time.sleep(args.settle_s)
        except HarnessError as e:
            harness = str(e)
            echo(f"power-cycle: {e}")
        except KeyboardInterrupt:
            interrupted = True
        finally:
            if not powered:
                try:
                    shell(args.on_cmd, "on")
                except HarnessError as e:
                    echo(f"power-cycle: could not switch the castle back on: {e}")
    return verdict(host, args, out, cycles, harness, interrupted, echo)


def verdict(
    host: str,
    args: argparse.Namespace,
    out: Path,
    cycles: list[Cycle],
    harness: str,
    interrupted: bool,
    echo: Callable[[str], None],
) -> int:
    boots = [c.boot_s for c in cycles if c.boot_s is not None]
    whole = sum(1 for c in cycles if c.ok)
    ok = not harness and not interrupted and whole == len(cycles) == args.cycles
    summary = {
        "host": host,
        "cycles_planned": args.cycles,
        "cycles_run": len(cycles),
        "whole": whole,
        "failed": [{"cycle": c.n, "failures": c.failures} for c in cycles if not c.ok],
        "boot_s": {
            "min": min(boots),
            "median": statistics.median(boots),
            "max": max(boots),
        }
        if boots
        else None,
        "versions": sorted({c.version for c in cycles if c.version}),
        "reset_reasons": sorted({c.reason for c in cycles if c.reason}),
        "harness_error": harness,
        "interrupted": interrupted,
        "passed": ok,
    }
    text = json.dumps(summary, indent=2) + "\n"
    (out / "summary.json").write_text(text, encoding="utf-8")
    head = "PASS" if ok else ("HARNESS ERROR" if harness else "FAIL")
    lines = [f"POWER-CYCLE {head}: {host}, {whole} of {args.cycles} cycles whole"]
    if boots:
        lines.append(
            f"  boot to first answer: min {min(boots):g} s, median "
            f"{statistics.median(boots):g} s, max {max(boots):g} s "
            f"(limit {args.max_boot_s:g} s)"
        )
    lines += [f"  {c.line()}" for c in cycles if not c.ok]
    if harness:
        lines.append(f"  {harness}")
    if interrupted:
        lines.append("  interrupted before the last cycle")
    (out / "verdict.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    echo("\n".join(lines))
    return 2 if harness else (0 if ok else 1)


def parse(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="power_cycle.py",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="docs/SOAK.md has a worked example for common plugs.",
    )
    ap.add_argument(
        "host",
        nargs="?",
        help="IP, host:port, mDNS name or devices.toml "
        "name (default: CASTLE_HOST, then devices.toml)",
    )
    ap.add_argument("--off-cmd", required=True, help="shell command that cuts power")
    ap.add_argument("--on-cmd", required=True, help="shell command that restores it")
    num = (
        ("--cycles", int, 50, "how many (default 50)"),
        ("--off-s", float, 10.0, "seconds held off (default 10)"),
        ("--down-timeout", float, 30.0, "seconds for it to go quiet (default 30)"),
        ("--boot-timeout", float, 120.0, "seconds to first answer (default 120)"),
        ("--max-boot-s", float, 60.0, "slowest boot that passes (default 60)"),
        ("--ready-timeout", float, 30.0, "seconds for card + scenes (default 30)"),
        ("--play-timeout", float, 15.0, "seconds for play/stop to show (default 15)"),
        ("--settle-s", float, 5.0, "seconds between cycles (default 5)"),
        ("--stop-after", int, 3, "stop after this many failures in a row (default 3)"),
        ("--interval", float, 0.5, "seconds between polls (default 0.5)"),
    )
    for flag, kind, default, text in num:
        ap.add_argument(flag, type=kind, default=default, help=text)
    ap.add_argument("--scene", help="the scene to start (default: the castle's first)")
    ap.add_argument("--out", type=Path, help="default soak-logs/power-<host>-<time>/")
    ap.add_argument(
        "--no-keep-awake",
        action="store_true",
        help="let this computer sleep during the run",
    )
    args = ap.parse_args(argv)
    if args.cycles < 1 or args.stop_after < 1:
        ap.error("--cycles and --stop-after must be at least 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    found = hosts.candidates(args.host)
    if not found:
        print(
            "power-cycle: no castle named — pass one, or set CASTLE_HOST",
            file=sys.stderr,
        )
        return 2
    host = found[0]
    when = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    name = f"power-{re.sub(r'[^\w.-]+', '_', host)}-{when}"
    out = args.out or REPO / "soak-logs" / name
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"power-cycle: cannot write {out}: {e}", file=sys.stderr)
        return 2
    undo = (lambda: None) if args.no_keep_awake else keep_awake()
    try:
        return run(host, args, out)
    finally:
        undo()


def _terminate(_sig: int, _frame: object) -> None:
    raise KeyboardInterrupt  # a `kill` still switches the castle back on


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _terminate)
    sys.exit(main())
