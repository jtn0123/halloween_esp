"""The castle key, in two languages — held together on the wire.

Firmware v5.74 refuses a keyed castle's writes without `X-Castle-Key`. Two
copies decide which key to send: Python's `hosts.castle_key` (castle_link,
sd_sync, Castle Radio) and castle-core's `hosts::castle_key` (the studio's
relay and the `castle` bin). The rule is one sentence — CASTLE_KEY when it
is set, set-but-empty meaning none; else the key of the FIRST devices.toml
entry whose host or fallbacks name the castle; a key the firmware could not
hold is never sent — and this suite holds the two to it the only way that
matters: a recording castle hears what each one actually SENDS, and the
header is compared with `hosts.key_headers` on the same inputs.

The corpus is the inventory a hand and tools/castle_keys.py both write: a
basic string with a quote, a backslash, a hash and a \\u escape in it, a
literal string, a table that is also an earlier table's fallback, a castle
with no key, a key no castle could hold, and a castle nobody named — under
CASTLE_KEY unset, empty, padded and invalid. docs/PARITY.md lists it.

Skipped, not failed, without cargo — except in CI.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import cargo_gate
import hosts
from exe_paths import exe
from studio_rs_case import BIN as STUDIO
from studio_rs_case import fetch, free_port, wait_up

CARGO = cargo_gate.CARGO
IN_CI = bool(os.environ.get("CI"))
CASTLE = ROOT / "core" / "target" / "release" / exe("castle")

#: CASTLE_KEY as a shell might leave it: unset, empty, padded, unusable.
ENVS: tuple[str | None, ...] = (None, "", "  fr0m-env\t", "two words")


class Ear(http.server.ThreadingHTTPServer):
    """A castle that answers everything 200 and remembers each request's
    X-Castle-Key (None when the header was not sent)."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Heard)
        self.heard: list[tuple[str, str | None]] = []
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def host(self) -> str:
        return f"127.0.0.1:{self.server_address[1]}"


class _Heard(http.server.BaseHTTPRequestHandler):
    server: Ear

    def _any(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        self.server.heard.append((self.path, self.headers.get("X-Castle-Key")))
        body = b'{"version":"5.74","scenes":"vigil","locked":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_DELETE = _any

    def log_message(self, format: str, *args: object) -> None:
        pass


def inventory(h: list[str]) -> str:
    """The corpus, around five live addresses."""
    return (
        "# the key store, as a hand and tools/castle_keys.py both write it\n"
        "[first]\n"
        f'host = "{h[0]}"\n'
        # A quote, a backslash, a hash and a \u escape, inside the string.
        'key = "f1rst\\"q\\\\#\\u0041"   # and a comment after it\n'
        f'fallbacks = ["{h[1]}"]\n\n'
        "[second]\n"
        f'host = "{h[1]}"   # also first\'s fallback: the FIRST table wins\n'
        "key = 'second\\literal'\n\n"
        "[open]\n"
        f'host = "{h[2]}"\n\n'
        "[junk]\n"
        f'host = "{h[3]}"\n'
        'key = "two words"   # no castle could hold it: never sent\n'
    )


def python_says(toml: Path, host: str, env: str | None) -> str | None:
    """What hosts.key_headers puts on the wire for `host`."""
    clean = {
        k: v for k, v in os.environ.items() if k not in ("CASTLE_KEY", "CASTLE_DEVICES")
    }
    clean["CASTLE_DEVICES"] = str(toml)
    if env is not None:
        clean["CASTLE_KEY"] = env
    with mock.patch.dict(os.environ, clean, clear=True):
        return hosts.key_headers(host).get("X-Castle-Key")


def child_env(toml: Path, env: str | None, **extra: str) -> dict[str, str]:
    e = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CASTLE_KEY", "CASTLE_DEVICES", "CASTLE_HOST")
    }
    e["CASTLE_DEVICES"] = str(toml)
    if env is not None:
        e["CASTLE_KEY"] = env
    return {**e, **extra}


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class TestTheKeyOnTheWire(unittest.TestCase):
    ears: ClassVar[list[Ear]]
    tmp: ClassVar[Path]
    toml: ClassVar[Path]

    @classmethod
    def setUpClass(cls) -> None:
        built = cargo_gate.build()
        assert built.returncode == 0, built.stderr
        cls.ears = [Ear() for _ in range(5)]
        cls.tmp = Path(tempfile.mkdtemp(prefix="castle-key-"))
        cls.toml = cls.tmp / "devices.toml"
        cls.toml.write_text(inventory([e.host for e in cls.ears]), encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        for e in cls.ears:
            e.shutdown()
            e.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_corpus_means_what_it_says(self) -> None:
        """Python's answers, stated — so a parity pass is not two copies
        agreeing on something wrong."""
        h = [e.host for e in self.ears]
        want = ['f1rst"q\\#A', 'f1rst"q\\#A', None, None, None]
        self.assertEqual([python_says(self.toml, x, None) for x in h], want)
        self.assertEqual(python_says(self.toml, h[2], "  fr0m-env\t"), "fr0m-env")
        self.assertIsNone(python_says(self.toml, h[0], ""))
        self.assertIsNone(python_says(self.toml, h[0], "two words"))

    def test_the_castle_bin_sends_what_hosts_py_would(self) -> None:
        for ear in self.ears:
            for env in ENVS:
                with self.subTest(host=ear.host, env=env):
                    r = subprocess.run(
                        [str(CASTLE), "--host", ear.host, "stop"],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=15,
                        env=child_env(self.toml, env),
                    )
                    self.assertEqual(r.returncode, 0, r.stderr)
                    path, sent = ear.heard[-1]
                    self.assertEqual(path, "/api/stop")
                    self.assertEqual(sent, python_says(self.toml, ear.host, env))
                    # Nothing the bin prints carries a key.
                    for secret in ('f1rst"q', "second\\literal", "fr0m-env"):
                        self.assertNotIn(secret, r.stdout + r.stderr)

    def test_the_studio_relay_sends_what_hosts_py_would(self) -> None:
        """One studio per (castle, CASTLE_KEY): the variable is read by the
        server's own process. Every request it relays carries the key for
        the host it went to — the status probe and a write alike."""
        dead = f"127.0.0.1:{free_port()}"
        cases = [(e.host, None) for e in self.ears] + [
            (self.ears[0].host, "  fr0m-env\t"),
            (self.ears[0].host, ""),
        ]
        for host, env in cases:
            with self.subTest(host=host, env=env):
                ear = next(e for e in self.ears if e.host == host)
                ear.heard.clear()
                # A dead first candidate: the key must follow the host that
                # ANSWERED, not the one the walk started with.
                log = self.tmp / "studio.log"
                with log.open("wb") as out:
                    port = free_port()
                    proc = subprocess.Popen(
                        [str(STUDIO), str(port), "--localhost"],
                        env=child_env(
                            self.toml,
                            env,
                            CASTLE_HOST=f"{dead},{host}",
                            CASTLE_TRACKS=str(self.tmp / "tracks"),
                            CASTLE_SCENES=str(self.tmp / "scenes.yaml"),
                            CASTLE_BUILD=str(self.tmp / "build"),
                            CASTLE_PY=os.environ.get("CASTLE_PY", sys.executable),
                        ),
                        stdout=out,
                        stderr=subprocess.STDOUT,
                    )
                    try:
                        wait_up(port)
                        code, _, _ = fetch(port, "/api/pir?armed=1", "POST")
                        self.assertEqual(code, 200)
                        # CASTLE_KEY pins the key, so the desk's key route
                        # says so and changes nothing (the corpus is shared:
                        # an unpinned clear would really forget a key).
                        _, _, raw = fetch(port, "/studio/castle-key")
                        self.assertEqual(json.loads(raw)["pinned"], env is not None)
                        if env is not None:
                            code, _, _ = fetch(
                                port,
                                "/studio/castle-key",
                                "POST",
                                {"Content-Type": "application/json"},
                                b'{"action": "clear"}',
                            )
                            self.assertEqual(code, 409)
                    finally:
                        proc.terminate()
                        proc.wait(timeout=10)
                want = python_says(self.toml, host, env)
                relayed = [k for p, k in ear.heard if p == "/api/pir?armed=1"]
                self.assertEqual(relayed, [want])
                probes = [k for p, k in ear.heard if p == "/api/status"]
                self.assertEqual(probes[-1], want)
                said = log.read_text(encoding="utf-8", errors="replace")
                for secret in ('f1rst"q', "second\\literal", "fr0m-env"):
                    self.assertNotIn(secret, said)


if __name__ == "__main__":
    unittest.main()
