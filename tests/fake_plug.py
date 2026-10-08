"""A smart plug for the test suite: `fake_plug.py on|off --port P --state DIR`.

tests/test_power_cycle.py hands tools/power_cycle.py these as its --on-cmd
and --off-cmd, exactly as an owner hands it their plug's CLI:

  on   starts tools/castle_emu.py on port P in a process of its own — power
       reaching the castle — and returns at once, as a plug's API does; the
       castle answers when it has booted, not when the command returns.
  off  kills that process and returns once the port refuses, as cutting the
       power does. A castle that is already off stays off.

The emulator's pid lives in DIR/castle.pid, so `off` in a test's cleanup
always finds what `on` started. --no-sd boots a castle with no card. The
port is the test's free one, never a well-known port.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def refused(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return False
    except OSError:
        return True


def on(port: int, state: Path, no_sd: bool) -> int:
    pidfile = state / "castle.pid"
    if pidfile.exists():
        return 0  # already on
    card = state / "card"
    card.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(TOOLS / "castle_emu.py"),
        str(port),
        "--dir",
        str(card),
        "--scenes",
        "vigil,storm",
        *(["--no-sd"] if no_sd else []),
    ]
    # Detached, as a castle is from the plug: the emulator outlives `on`.
    detach: dict[str, Any] = {"start_new_session": True}
    if sys.platform == "win32":  # pragma: no cover - Windows CI
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        detach = {"creationflags": flags}
    p = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **detach,
    )
    pidfile.write_text(str(p.pid), encoding="utf-8")
    return 0


def off(port: int, state: Path) -> int:
    pidfile = state / "castle.pid"
    if pidfile.exists():
        try:
            os.kill(int(pidfile.read_text(encoding="utf-8")), signal.SIGTERM)
        except (OSError, ValueError):
            pass  # already gone
        pidfile.unlink()
    deadline = time.monotonic() + 10
    while not refused(port):
        if time.monotonic() > deadline:
            return 1
        time.sleep(0.05)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("verb", choices=("on", "off"))
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--state", type=Path, required=True)
    ap.add_argument("--no-sd", action="store_true")
    args = ap.parse_args()
    if args.verb == "on":
        return on(args.port, args.state, args.no_sd)
    return off(args.port, args.state)


if __name__ == "__main__":
    sys.exit(main())
