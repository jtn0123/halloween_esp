"""One request must not be able to end the studio — the black-box half.

`core/src/jsonio.rs` has a `#[test]` for the depth limit itself. This is the
other half of the same claim, and the half that failed before the fix: a real
`POST /studio/scene` at a real Rust studio, with the body that used to abort
the process (grade report 2026-09-06 B1 — `parse_val` recursed once per
nesting level, and 100 KB of `[` overflowed the stack; `catch_unwind` in
`core/src/bin/studio.rs` cannot catch an abort, so every other connection in
flight died with it). The Python twin answered 500 and kept serving, which is
what "the twins differ under fault" meant.

So the assertion is not only the status code. It is that the process is still
alive afterwards and still answering, because a unit test over the parser
cannot tell the difference between a refusal and a crash the harness never
saw.

Skipped, not failed, without cargo — except in CI. The sandbox and the
launcher are golden_case.py's; nothing here touches the repo's own library,
show or castle (CASTLE_HOST="" — not one socket leaves the machine).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

import cargo_gate
import golden_case as gc
from golden_corpus import JSON_HDRS
from helpers import SANDBOX_ENV

CARGO = cargo_gate.CARGO
IN_CI = bool(os.environ.get("CI"))
BIN = ROOT / "core" / "target" / "release" / "studio"

#: The body from the report, unchanged: 100 KB of one byte. It is nothing
#: like the ~20 KB actually needed, and that is the point — MAX_BODY is
#: 512 MB, so nothing upstream of the parser was ever going to stop it.
FLOOD = b"[" * 100_000


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class DepthRefusalIsAnAnswer(unittest.TestCase):
    """One studio, several hostile bodies, and a server still standing."""

    tmp: ClassVar[Path]
    proc: ClassVar[subprocess.Popen[bytes] | None]
    port: ClassVar[int]

    @classmethod
    def setUpClass(cls) -> None:
        built = cargo_gate.build()
        assert built.returncode == 0, built.stderr
        cls.tmp = Path(tempfile.mkdtemp(prefix="studio-depth-rs-"))
        cls.proc = None
        box = gc.Sandbox(cls.tmp)
        box.seed()
        env = {k: v for k, v in os.environ.items() if k not in SANDBOX_ENV}
        cls.proc, cls.port = gc.launch([str(BIN)], box, env)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.proc is not None:
            cls.proc.terminate()
            cls.proc.wait(timeout=10)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def post(self, body: bytes) -> tuple[int, bytes]:
        status, _hdrs, data = gc.fetch(
            self.port, "/studio/scene", "POST", JSON_HDRS, body
        )
        return status, data

    def test_the_flood_is_refused_and_the_studio_keeps_serving(self) -> None:
        status, data = self.post(FLOOD)
        self.assertGreaterEqual(status, 400)
        self.assertLess(status, 500)
        out = json.loads(data)
        self.assertIs(out["ok"], False)
        self.assertIn("not valid JSON", out["error"])
        self.assertIn("nested deeper", out["error"])
        # The half a unit test cannot see: the process is still there.
        assert self.proc is not None
        self.assertIsNone(self.proc.poll(), "the studio died on the request")
        self.assertEqual(gc.fetch(self.port, "/api/status")[0], 200)

    def test_it_is_not_a_one_off_and_a_good_body_still_works(self) -> None:
        """Three floods back to back, then an ordinary refusal: the guard is
        a verdict on one request, not a wound the server carries."""
        for _ in range(3):
            self.assertGreaterEqual(self.post(FLOOD)[0], 400)
        status, data = self.post(b"{}")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(data)["error"], "need id and yaml")
        assert self.proc is not None
        self.assertIsNone(self.proc.poll())

    def test_a_document_the_studio_could_ever_be_handed_still_parses(self) -> None:
        """The limit has to be far above anything real, or it is a second
        outage wearing the first one's clothes. A scene body nested to the
        depth the desk actually sends is unremarkable; only the wall is not."""
        body = json.dumps({"id": "x", "cues": [{"at": 0.0, "zones": ["door"]}]})
        status, data = self.post(body.encode())
        self.assertEqual(status, 400)  # a refusal from the validator, not the parser
        self.assertNotIn("not valid JSON", json.loads(data)["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
