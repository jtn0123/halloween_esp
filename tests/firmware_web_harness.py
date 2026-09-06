"""Two castles on one bench: the real C handlers and the emulator.

tests/cxx/web_check.cpp compiles firmware/sd_web.h with a host compiler and
answers requests on a pipe; tools/castle_emu.py answers the same requests
over a socket. This module is what both test suites use to stand them side
by side — build the binary, seed one card directory, speak raw HTTP at the
emulator, and hand back two replies that can simply be compared.

Raw sockets rather than http.client on purpose. Half of what is worth
testing is malformed on purpose: a request target holding bytes no URL
library will pass through, and a Content-Length that deliberately exceeds
the body (the 413 cap, the 507 precondition and the short-write leg all
live in that gap). http.client refuses to send either.
"""

from __future__ import annotations

import atexit
import functools
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

CXX_DIR = ROOT / "tests" / "cxx"
FIRMWARE = ROOT / "firmware"

COMPILER = shutil.which("clang++") or shutil.which("g++")
#: Locally a missing compiler is a skip; in CI it is a failure. Same rule as
#: tests/test_firmware_cxx.py, and for the same reason — a green tick that
#: compiled nothing is worse than a red one.
IN_CI = bool(os.environ.get("CI"))


def firmware_version() -> str:
    """The version string the device build compiles in (-DCASTLE_VERSION),
    so the harness and the emulator answer /api/status the same."""
    for line in (FIRMWARE / "castle.yaml").read_text().splitlines():
        if line.strip().startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"')
    raise AssertionError("no version: in firmware/castle.yaml")


def build(out: Path) -> subprocess.CompletedProcess[str]:
    """Compile web_check.cpp. The shim include path goes FIRST so
    <esp_http_server.h> and friends resolve to the fakes."""
    assert COMPILER is not None
    return subprocess.run(
        [
            COMPILER,
            "-std=c++17",
            "-O1",
            "-Wall",
            "-Wextra",
            "-Werror",
            f'-DCASTLE_VERSION="{firmware_version()}"',
            "-I",
            str(CXX_DIR / "shim"),
            "-I",
            str(FIRMWARE),
            str(CXX_DIR / "web_check.cpp"),
            "-o",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


@functools.cache
def compiled() -> tuple[Path, subprocess.CompletedProcess[str]]:
    """The harness binary, built once for the whole test run — three suites
    import this module and one compile is enough for all of them."""
    tmp = tempfile.mkdtemp(prefix="castle-web-cxx-")
    atexit.register(shutil.rmtree, tmp, True)
    out = Path(tmp) / "web_check"
    return out, build(out)


#: Headers every HTTP server adds and neither side is being judged on.
BORING = {"Server", "Date", "Content-Length", "Content-Type", "Connection"}


@dataclass
class Reply:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ctype(self) -> str:
        return self.headers.get("Content-Type", "")

    @property
    def extra(self) -> dict[str, str]:
        """The headers a handler actually chose to set."""
        return {k: v for k, v in self.headers.items() if k not in BORING}

    def key(self) -> tuple[int, bytes]:
        return self.status, self.body


class CastleC:
    """The compiled firmware, one long-lived process on a pipe."""

    def __init__(
        self, binary: Path, card: Path, args: tuple[str, ...] = (), **env: str
    ) -> None:
        self.card = card
        self.proc = subprocess.Popen(
            [str(binary), *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "CASTLE_CARD": str(card), **env},
        )

    def close(self) -> None:
        assert self.proc.stdin and self.proc.stdout
        try:
            self.proc.stdin.write(b"QUIT\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=10)
        except (BrokenPipeError, ValueError, subprocess.TimeoutExpired):
            self.proc.kill()
        finally:
            self.proc.stdin.close()
            self.proc.stdout.close()

    def http(
        self,
        method: str,
        target: bytes,
        body: bytes = b"",
        declared: int | None = None,
        port: int = 80,
    ) -> Reply:
        assert self.proc.stdin and self.proc.stdout
        n = len(body) if declared is None else declared
        head = f"{method} {len(target)} {n} {len(body)} {port}\n".encode()
        self.proc.stdin.write(head + target + body)
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError(
                f"web_check died on {method} {target!r} (exit {self.proc.poll()})"
            )
        status, blen, nhdr = (int(x) for x in line.split())
        headers = {}
        for _ in range(nhdr):
            raw = self.proc.stdout.readline().decode()
            name, _, value = raw.rstrip("\n").partition(": ")
            headers[name] = value
        return Reply(status, self.proc.stdout.read(blen), headers)

    def rules(self, names: list[bytes]) -> list[tuple[bool, bytes, bytes]]:
        """safe_name / url_decode / json_escape, run in C. Only a binary
        started with --rules answers this."""
        assert self.proc.stdin and self.proc.stdout
        out = []
        for n in names:
            self.proc.stdin.write(f"{len(n)}\n".encode() + n)
            self.proc.stdin.flush()
            safe, dlen, elen = (int(x) for x in self.proc.stdout.readline().split())
            dec = self.proc.stdout.read(dlen)
            esc = self.proc.stdout.read(elen)
            out.append((bool(safe), dec, esc))
        return out


def emu_http(
    port: int,
    method: str,
    target: bytes,
    body: bytes = b"",
    declared: int | None = None,
    timeout: float = 20.0,
) -> Reply:
    """One raw request at the emulator, with the request target and the
    Content-Length exactly as given."""
    n = len(body) if declared is None else declared
    head = (
        method.encode()
        + b" "
        + target
        + b" HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: "
        + str(n).encode()
        + b"\r\nConnection: close\r\n\r\n"
    )
    chunks: list[bytes] = []
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        try:
            s.sendall(head + body)
        except (BrokenPipeError, ConnectionResetError):
            # The reply came back before the body finished going out — an
            # OTA image bigger than the slot is refused on its declared
            # length alone, and 2 MB is a long time to keep writing at a
            # socket nobody is reading. The answer is already waiting.
            pass
        while True:
            try:
                got = s.recv(65536)
            except ConnectionResetError:
                # A handler that refuses before reading the body (the OTA
                # size window, the 413 cap) closes with bytes still in the
                # kernel's receive queue, and the peer answers RST. The
                # reply is already here; the device does exactly the same,
                # and the C harness has no socket to do it on.
                break
            if not got:
                break
            chunks.append(got)
    raw = b"".join(chunks)
    head_bytes, _, payload = raw.partition(b"\r\n\r\n")
    lines = head_bytes.split(b"\r\n")
    status = int(lines[0].split()[1])
    headers = {}
    for line in lines[1:]:
        name, _, value = line.decode("latin-1").partition(": ")
        headers[name] = value
    return Reply(status, payload, headers)


#: The scene ids both castles are built with, so /api/scene agrees on which
#: id is unknown. Deliberately not the repo's real show: a test that follows
#: scenes.yaml would change its meaning every time a scene is added.
SCENE_IDS = ["vigil", "storm"]


class Pair:
    """One firmware binary and one emulator, each over its own copy of the
    same card, configured the same way. `both()` asks them the identical
    question; everything else about a test is what it does with the two
    answers."""

    def __init__(self, tmp: Path, name: str, **env: str) -> None:
        binary, built = compiled()
        assert built.returncode == 0, built.stderr
        self.card_c = tmp / f"{name}-c"
        self.card_e = tmp / f"{name}-e"
        for card in (self.card_c, self.card_e):
            card.mkdir(parents=True)
            seed_card(card)
        import castle_emu  # tools/ is on sys.path above
        import castle_emu_http

        # One number, spelled to both sides: the OTA slot is a per-board
        # constant with a default on each side, and two defaults that agree
        # today are two defaults that can stop agreeing.
        slot = int(env.get("CASTLE_OTA_SLOT", hex(castle_emu_http.OTA_SLOT)), 0)
        self.c = CastleC(
            binary,
            self.card_c,
            (),
            **{
                "CASTLE_SCENE_IDS": ",".join(SCENE_IDS),
                "CASTLE_PIR_SCENE": "storm",
                **env,
                "CASTLE_OTA_SLOT": hex(slot),
            },
        )
        self.emu = castle_emu.CastleEmu(
            port=0,
            sd_dir=self.card_e,
            scenes=list(SCENE_IDS),
            version=firmware_version(),
            sd_mounted=env.get("CASTLE_MOUNTED", "1") != "0",
            ota_slot=slot,
        )
        if "CASTLE_SD_FREE_KB" in env:
            self.emu.sd_free_kb = int(env["CASTLE_SD_FREE_KB"])
        self.emu.start()

    def close(self) -> None:
        self.c.close()
        self.emu.shutdown()
        self.emu.server_close()

    def both(
        self,
        method: str,
        target: bytes,
        body: bytes = b"",
        declared: int | None = None,
        port: int = 80,
    ) -> tuple[Reply, Reply]:
        return (
            self.c.http(method, target, body, declared, port),
            emu_http(self.emu.port, method, target, body, declared),
        )

    def cards(self) -> tuple[set[str], set[str]]:
        """What each card holds, relative — a PUT or DELETE has to leave
        both in the same state, sidecars included."""
        return (
            {str(p.relative_to(self.card_c)) for p in self.card_c.rglob("*")},
            {str(p.relative_to(self.card_e)) for p in self.card_e.rglob("*")},
        )


def seed_card(card: Path) -> None:
    """One card layout both sides read: a root track, a scene track, a site
    page and its gzip twin, plus a directory to list into."""
    import gzip

    (card / "scenes").mkdir(parents=True, exist_ok=True)
    (card / "site").mkdir(parents=True, exist_ok=True)
    (card / "logs").mkdir(parents=True, exist_ok=True)
    (card / "wicked_winds.mp3").write_bytes(b"\xff\xfb" + b"\x00" * 4094)
    (card / "scenes" / "vigil.mp3").write_bytes(b"\xff\xfb" + b"\x00" * 2046)
    (card / "site" / "index.html").write_bytes(b"<!doctype html><title>desk</title>")
    (card / "site" / "index.html.gz").write_bytes(gzip.compress(b"<!doctype html>gz"))
    (card / "site" / "app.js").write_bytes(b"console.log(1)")
    # A name safe_name refuses: the Mac wrote it straight onto the card, so
    # /api/files must count it in {"skipped":N} rather than list it.
    (card / "bad\x7fname.mp3").write_bytes(b"x")


@unittest.skipIf(COMPILER is None and not IN_CI, "no host C++ compiler")
class WebPairCase(unittest.TestCase):
    """A test that drives both castles over one Pair.

    Lives here rather than in either suite because both of them subclass
    it: tests/test_firmware_web_cxx.py takes the reading half of the API
    and tests/test_firmware_web_card.py the writing half, which is the
    firmware's own seam (sd_web_site.h "bytes out" vs sd_web.h "control
    in"). `env` on a subclass configures the castle it gets.
    """

    env: ClassVar[dict[str, str]] = {}
    tmp: ClassVar[str]
    pair: ClassVar[Pair]

    @classmethod
    def setUpClass(cls) -> None:
        if COMPILER is None:
            raise AssertionError(
                "CI is set and no host C++ compiler (clang++/g++) is on PATH "
                "— the firmware web harness must run in CI"
            )
        _, built = compiled()
        assert built.returncode == 0, (
            f"tests/cxx/web_check.cpp did not compile:\n{built.stderr}"
        )
        cls.tmp = tempfile.mkdtemp(prefix="castle-web-pair-")
        cls.pair = Pair(Path(cls.tmp), cls.__name__.lower(), **cls.env)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pair.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def same(
        self,
        method: str,
        target: bytes,
        body: bytes = b"",
        declared: int | None = None,
        port: int = 80,
    ) -> Reply:
        """Ask both, assert they answered identically, return the answer."""
        c, e = self.pair.both(method, target, body, declared, port)
        where = f"{method} {target.decode('latin-1')}"
        self.assertEqual((c.status, c.body), (e.status, e.body), where)
        self.assertEqual(c.ctype, e.ctype, where)
        self.assertEqual(c.extra, e.extra, where)
        return c
