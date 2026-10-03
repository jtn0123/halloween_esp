"""Every server an owner's computer runs listens on the loopback only.

docs/PRODUCTION-TODO.md §4.2. Windows Firewall asks "allow access?" the first
time a program listens on an address the network can reach — a dialog a
buyer should never meet, about servers nothing on the Wi-Fi needs. 127.0.0.1
is not such an address, so nothing asks.

Each server is started the way the owner's launchers start it, on port 0:

  * Castle Radio — `python demo/castle-radio/server.py PORT`, by the desktop
    app (desktop/src-tauri/src/service.rs) and the installer's launcher
    (tools/desktop_launch.py) alike;
  * the cue desk studio — `studio PORT` as the app starts it, and
    `studio PORT --localhost` as the launcher (and the e2e suite) does.

Then it is asked for twice: on 127.0.0.1, where it must answer, and on this
machine's own LAN address, where it must not — an answer there would be an
answer to everyone on the Wi-Fi. A machine with no network route has no LAN
address to ask on; the banner and the loopback answer are still checked.
`--lan` stays the studio's explicit opt-in and is not exercised here: binding
every interface is exactly what would raise a firewall dialog on the machine
running this suite. Its parsing is core/src/bin/studio.rs's own unit test.

Slow-suite name (`_rs`): the studio is the release binary, built here.
"""

from __future__ import annotations

import os
import queue
import re
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import helpers  # noqa: F401 — clears the sandbox knobs before anything reads them
from studio_rs_case import BIN, build_bin

BANNER = re.compile(r"http://([0-9.]+):(\d+)")
RADIO = ROOT / "demo" / "castle-radio" / "server.py"


def lan_address() -> str | None:
    """This machine's address on its network, or None with no route. A UDP
    connect sends nothing: it only asks the routing table which interface
    would carry a packet (castle-core's studio::lan_ip, the same question)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            ip = str(s.getsockname()[0])
        except OSError:
            return None
    return None if ip.startswith("127.") or ip == "0.0.0.0" else ip


def _lines(proc: subprocess.Popen[str], out: queue.Queue[str]) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        out.put(line)


class LoopbackOnly(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="loopback-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        (self.tmp / "tracks").mkdir()
        self.env = {
            **os.environ,
            "CASTLE_HOST": "",
            "CASTLE_RADIO_HOST": "",
            "CASTLE_RADIO_DATA": str(self.tmp / "radio"),
            "CASTLE_TRACKS": str(self.tmp / "tracks"),
            "CASTLE_SCENES": str(self.tmp / "scenes.yaml"),
            "CASTLE_BUILD": str(self.tmp / "build"),
            "CASTLE_DEVICES": str(self.tmp / "devices.toml"),
            "CASTLE_PY": os.environ.get("CASTLE_PY", sys.executable),
            "PYTHONIOENCODING": "utf-8",
        }

    def start(self, argv: list[str], then: str = "") -> int:
        """Run `argv`, wait for the address it prints — and for `then`, a
        line it must print after it — and return the real port."""
        proc = subprocess.Popen(
            argv,
            cwd=ROOT,
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        def stop() -> None:
            proc.kill()
            proc.wait(timeout=10)
            if proc.stdout:
                proc.stdout.close()

        self.addCleanup(stop)
        out: queue.Queue[str] = queue.Queue()
        threading.Thread(target=_lines, args=(proc, out), daemon=True).start()
        seen: list[str] = []
        port = 0
        while not port or then not in "".join(seen):
            try:
                line = out.get(timeout=60)
            except queue.Empty:
                self.fail(f"{argv[0]} never printed its address and {then!r}: {seen}")
            seen.append(line)
            if not port and (m := BANNER.search(line)):
                self.assertEqual(m.group(1), "127.0.0.1", line)
                port = int(m.group(2))
        return port

    def assert_loopback_only(self, port: int) -> None:
        self.assertNotEqual(port, 0)
        with socket.create_connection(("127.0.0.1", port), timeout=10):
            pass
        lan = lan_address()
        if lan is None:
            return
        # Refused at once on every OS; a firewall that drops instead of
        # refusing makes it a timeout, which is the same answer.
        with self.assertRaises(OSError, msg=f"{lan}:{port} answered: it is on the LAN"):
            socket.create_connection((lan, port), timeout=5).close()

    def test_castle_radio(self) -> None:
        self.assert_loopback_only(self.start([sys.executable, str(RADIO), "0"]))

    def test_the_studio_as_the_app_starts_it(self) -> None:
        build_bin()
        port = self.start([str(BIN), "0"], then="this computer only")
        self.assert_loopback_only(port)

    def test_the_studio_as_the_launcher_starts_it(self) -> None:
        build_bin()
        port = self.start([str(BIN), "0", "--localhost"], then="this computer only")
        self.assert_loopback_only(port)


if __name__ == "__main__":
    unittest.main()
