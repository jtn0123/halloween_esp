#!/usr/bin/env python3
"""Leave a castle running for hours and say whether it held up — `make soak`.

  .venv/bin/python tools/soak.py 192.168.1.20 --hours 72
  .venv/bin/python tools/soak.py castle-ab12cd.local --hours 24 --drive show
  .venv/bin/python tools/soak.py --hours 12 --disrupt-at 2 --disrupt-hold 30 \\
      --disrupt-cmd "kasa --host 192.168.1.31 off" \\
      --disrupt-cmd "kasa --host 192.168.1.31 on"

What it asks, and how often (--interval, default 10 s): /api/status every
poll; /api/health and /api/events every --slow-every polls (default 3, so
every 30 s — the castle's 64-line event ring holds far more than half a
minute of its life) and on every poll that noticed something; /api/bootlog
at the start and after every reboot. Every reply goes into soak.jsonl under
--out, flushed as it is written; the running numbers go into summary.json
every few minutes, so a run that is killed still leaves them; the verdict
goes on screen and into verdict.txt. Exit status: 0 the castle passed, 1 it
failed, 2 the soak itself could not run.

What it judges is soak_verdict.py: reboots and their reset reasons, crashes,
Wi-Fi outages (how many, and the longest), card read errors and unmounts, the
heap's low-water mark and its trend, the radio, the heard clock's drift and
failed show starts. Every threshold is a flag (`--max-outages 5`), and
docs/SOAK.md says why each default is what it is.

Unattended means: the soak keeps this computer awake while it runs (macOS
caffeinate, Windows SetThreadExecutionState; --no-keep-awake to skip),
notices when it slept anyway and says so rather than blaming the castle, and
an interrupted run (Ctrl-C) still prints a verdict, marked as not having run
the full time.

The suite never points this at a real castle: tests/test_soak.py runs it
against tools/castle_emu.py on a free port.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import re
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import hosts
from soak_run import Soak
from soak_verdict import LIMIT_HELP, Limits, limit_fields

REPO = Path(__file__).resolve().parent.parent


def keep_awake() -> Callable[[], None]:
    """Hold off idle sleep while this process lives; returns the undo.
    Linux has no one switch — run the soak under `systemd-inhibit` there."""
    if sys.platform == "darwin" and shutil.which("caffeinate"):
        p = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])

        def undo() -> None:
            p.terminate()
            p.wait(5)

        return undo
    if sys.platform == "win32":  # pragma: no cover - Windows only
        k32 = getattr(ctypes, "windll").kernel32
        k32.SetThreadExecutionState(0x80000001)  # CONTINUOUS | SYSTEM_REQUIRED
        return lambda: k32.SetThreadExecutionState(0x80000000)
    return lambda: None


def _hours(text: str) -> list[float]:
    return [float(x) for x in text.split(",") if x.strip()]


def parse(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="soak.py",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Thresholds and the reasons for them: docs/SOAK.md.",
    )
    ap.add_argument(
        "host",
        nargs="?",
        help="IP, host:port, mDNS name or devices.toml "
        "name (default: CASTLE_HOST, then devices.toml)",
    )
    ap.add_argument("--hours", type=float, default=72.0, help="how long (default 72)")
    ap.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="seconds between status polls (default 10)",
    )
    ap.add_argument(
        "--slow-every",
        type=int,
        default=3,
        help="read health and events every Nth poll (default 3)",
    )
    ap.add_argument(
        "--out", type=Path, help="where the log goes (default soak-logs/<host>-<time>/)"
    )
    ap.add_argument(
        "--drive",
        choices=("none", "show", "scenes"),
        default="none",
        help="run the evening show, or start scenes in turn (default: watch only)",
    )
    ap.add_argument(
        "--scenes",
        type=lambda t: [x for x in t.split(",") if x],
        default=[],
        help="--drive scenes: these ids (default: the castle's)",
    )
    ap.add_argument(
        "--scene-every",
        type=float,
        default=300.0,
        help="--drive scenes: seconds between starts (default 300)",
    )
    ap.add_argument(
        "--start-timeout",
        type=float,
        default=10.0,
        help="seconds a start has to show in status (default 10)",
    )
    ap.add_argument(
        "--disrupt-cmd",
        action="append",
        default=[],
        help="a command that disturbs the castle on purpose, e.g. a smart "
        "plug switching the router off; repeat it for the steps, which run in "
        "order (no shell: tools/operator_cmd.py)",
    )
    ap.add_argument(
        "--disrupt-hold",
        type=float,
        default=30.0,
        help="seconds between one --disrupt-cmd step and the next (default 30)",
    )
    ap.add_argument(
        "--disrupt-at",
        type=_hours,
        default=[],
        help="hours into the run to run --disrupt-cmd, comma list",
    )
    ap.add_argument(
        "--no-keep-awake",
        action="store_true",
        help="let this computer sleep (the soak will say it did)",
    )
    lim = ap.add_argument_group("limits")
    for name, kind, default in limit_fields():
        lim.add_argument(
            "--" + name.replace("_", "-"),
            type=kind,
            default=default,
            help=f"{LIMIT_HELP[name]} (default {default})",
        )
    args = ap.parse_args(argv)
    if args.hours <= 0 or args.interval <= 0:
        ap.error("--hours and --interval must be positive")
    if args.disrupt_hold < 0:
        ap.error("--disrupt-hold cannot be negative")
    if args.disrupt_at and not args.disrupt_cmd:
        ap.error("--disrupt-at needs --disrupt-cmd")
    if any(not 0 <= h < args.hours for h in args.disrupt_at):
        ap.error("every --disrupt-at must fall inside --hours")
    return args


def limits_of(args: argparse.Namespace) -> Limits:
    """The Limits the parsed flags name (every field is a flag)."""
    return Limits(**{name: getattr(args, name) for name, _, _ in limit_fields()})


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    found = hosts.candidates(args.host)
    if not found:
        print("soak: no castle named — pass one, or set CASTLE_HOST", file=sys.stderr)
        return 2
    host = found[0]
    limits = limits_of(args)
    when = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = args.out or REPO / "soak-logs" / f"{re.sub(r'[^\w.-]+', '_', host)}-{when}"
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"soak: cannot write {out}: {e}", file=sys.stderr)
        return 2
    undo = (lambda: None) if args.no_keep_awake else keep_awake()
    try:
        return Soak(host, args, limits, out).run()
    finally:
        undo()


def _terminate(_sig: int, _frame: object) -> None:
    raise KeyboardInterrupt  # a `kill` gets the same verdict as Ctrl-C


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _terminate)
    sys.exit(main())
